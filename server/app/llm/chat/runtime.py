"""One paper-chat turn, top to bottom.

1. **Plan** (`plan.plan_paper_turn`): validate the request, resolve the
   model, load the paper and the conversation, rebuild the prompt and the
   model history. Every failure raises `ChatRequestError` before anything is
   written or streamed.
2. **Store the question** (`store.store_user_turn`): the client's UIMessage
   id is the idempotency key; a retry reuses its failed turn's rows.
3. **Run** the paper agent through the **stream pump** (`pump.StreamPump`),
   which turns its events into the AI-SDK UI-message stream
   (`stream.OpenPaperAdapter`: evidence holdback, tool-output caps).
   Transient provider failures are retried by `RetryingModel` (3 attempts,
   exponential backoff), and the pump interleaves `data-retry-status`
   chunks while a backoff sleeps.
4. **Finish** (`on_complete`, never raises): persist the answer
   (`store.AssistantTurnStore`), emit `data-citations` twice — raw
   immediately, reconciled after the pymupdf matching — rename the
   conversation non-fatally, and record telemetry.
5. A `finally` safety net persists a partial assistant row (with
   `bucket.interrupted=true`, plus `bucket.error` when the run FAILED rather
   than being stopped) when the client disconnected or the run died before
   `on_complete`, then tears the stream down.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, AsyncGenerator, AsyncIterator, List, Optional

from pydantic_ai import UsageLimits
from pydantic_ai.run import AgentRunResult
from pydantic_ai.ui.vercel_ai.request_types import RequestData
from pydantic_ai.ui.vercel_ai.response_types import DataChunk
from sqlalchemy.orm import Session

from app.database.telemetry import track_event
from app.llm.chat.evidence import reconcile_citations
from app.llm.chat.paper import (
    MAX_AGENTIC_ITERATIONS,
    PaperAgentDeps,
    build_paper_agent,
)
from app.llm.chat.plan import ChatRequestError, PaperTurnPlan, plan_paper_turn
from app.llm.chat.pump import StreamPump
from app.llm.chat.store import AssistantTurnStore, store_user_turn
from app.llm.chat.stream import OpenPaperAdapter
from app.llm.chat.title import rename_conversation
from app.llm.retrying_model import RetryingModel
from app.schemas.chat_stream import (
    CITATIONS_PART_ID,
    CITATIONS_PART_TYPE,
    citations_data,
)
from app.schemas.user import CurrentUser

logger = logging.getLogger(__name__)

__all__ = ["ChatRequestError", "run_paper_chat"]

# Cap on the failure text copied into telemetry.
MAX_TELEMETRY_ERROR_CHARS = 500


async def run_paper_chat(
    *,
    db: Session,
    current_user: CurrentUser,
    run_input: RequestData,
    accept: Optional[str],
    paper_id: str,
    conversation_id: str,
    provider: Optional[str],
    model: Optional[str],
    reasoning_effort: Optional[str],
    context_mode: Optional[str],
    user_references: Optional[List[str]],
) -> AsyncGenerator[str, None]:
    """Validate, run the agent, and yield encoded SSE strings.

    Raises ChatRequestError for pre-stream validation failures.
    """
    start_time = datetime.now(timezone.utc)

    # -- 1. plan ---------------------------------------------------------
    plan = await plan_paper_turn(
        db=db,
        current_user=current_user,
        run_input=run_input,
        paper_id=paper_id,
        conversation_id=conversation_id,
        provider=provider,
        model=model,
        reasoning_effort=reasoning_effort,
        context_mode=context_mode,
        user_references=user_references,
    )
    spec = plan.choice.spec
    chat_context = plan.chat_context

    pump = StreamPump()
    # Retrying at the MODEL layer is what makes this safe: the agent graph
    # re-sends the full message history including executed tool calls and
    # their returns, so a retry re-asks the provider — it never re-runs a
    # tool.
    pai_model = RetryingModel(plan.built_model, on_retry=pump.push_retry_status)

    # -- 2. store the question ------------------------------------------
    store_user_turn(db, plan, current_user)
    store = AssistantTurnStore(
        db=db,
        current_user=current_user,
        conversation_id=conversation_id,
        client_message_id=plan.user_message.id,
    )

    # -- 3. run ------------------------------------------------------------
    agent = build_paper_agent(
        model=pai_model,
        spec=spec,
        system_prompt=chat_context.system_prompt,
        paper=chat_context.paper,
        context_mode=chat_context.context_mode,
        repo_snapshot=chat_context.repo_snapshot,
    )

    # The sandbox session + mount are owned by THIS function, not the tool.
    # `open()` is INSIDE the try whose `finally` closes it: with only two pool
    # slots per worker, a failure or cancellation between acquisition and the
    # teardown block would otherwise wedge the repo feature for the rest of
    # the process's life.
    repo_sandbox = None
    if chat_context.repo_snapshot is not None:
        from app.llm.repo.sandbox import RepoSandbox

        repo_sandbox = RepoSandbox(chat_context.repo_snapshot)

    deps = PaperAgentDeps(
        paper_id=paper_id,
        paper=chat_context.paper,
        current_user=current_user,
        db=db,
        context_mode=chat_context.context_mode,
        allowed_paper_ids=chat_context.allowed_paper_ids,
        repo_sandbox=repo_sandbox,
    )
    adapter = OpenPaperAdapter(
        agent=agent,
        run_input=run_input,
        accept=accept,
        sdk_version=6,
        server_message_id=str(store.assistant_id),
        error_context={
            "model": spec.id,
            "provider": spec.provider.value,
            "conversation_id": conversation_id,
            "paper_id": paper_id,
        },
    )

    # -- 4. finish ---------------------------------------------------------
    async def on_complete(result: AgentRunResult[Any]) -> AsyncIterator[DataChunk]:
        """Persist the finished turn, then emit citations. Never raises: a
        failure here must not turn an already-delivered answer into a
        client-visible error.

        Persistence happens BEFORE any yield — a client disconnect while
        citation chunks are being delivered cancels this generator at the
        yield point, and a completed turn must not be downgraded to an
        "interrupted" row just because the disconnect landed here.
        """
        stream_state = adapter.last_event_stream
        full_text = stream_state.accumulated_text if stream_state else ""
        assistant_row, citations = store.store_completed(result, full_text)

        # `data-citations` as parsed, then again once reconciled against the
        # paper (and written back to the row). A failed reconcile only costs
        # the second chunk.
        if citations:
            yield DataChunk(
                type=CITATIONS_PART_TYPE,
                id=CITATIONS_PART_ID,
                data=citations_data(citations),
            )
            try:
                reconciled = await reconcile_citations(
                    citations,
                    chat_context.paper,
                    family_index=chat_context.family_index,
                    parent_paper_id=paper_id,
                    repo_snapshot=chat_context.repo_snapshot,
                    db=db,
                )
                if reconciled:
                    if assistant_row is not None:
                        store.store_reconciled(assistant_row, reconciled)
                    yield DataChunk(
                        type=CITATIONS_PART_TYPE,
                        id=CITATIONS_PART_ID,
                        data=citations_data(reconciled),
                    )
            except Exception as exc:
                logger.warning("Citation reconciliation failed (non-fatal): %s", exc)
                # A failed write leaves the session needing a rollback before
                # the title step below can use it.
                try:
                    db.rollback()
                except Exception:
                    pass

        await _title_and_record(db, plan, current_user, citations, start_time)

    def store_unfinished() -> None:
        stream_state = adapter.last_event_stream
        store.store_unfinished(
            full_text=stream_state.accumulated_text if stream_state else "",
            error_text=stream_state.error_text if stream_state else None,
        )

    # Everything from the sandbox acquisition onwards runs under ONE
    # try/finally: the pool has two slots per worker, so a failure between
    # taking one and reaching teardown would wedge the repo feature for the
    # life of the process.
    try:
        if repo_sandbox is not None:
            await repo_sandbox.open()

        pump.start(
            adapter,
            adapter.run_stream_native(
                message_history=plan.model_history,
                deps=deps,
                # The cache key routes every turn of one conversation to the
                # same prompt-cache owner, which is the other half of the
                # prefix-stability work in `load_model_history`: a stable
                # prefix only hits if the request lands where that prefix is
                # cached.
                model_settings=plan.choice.registry.build_settings(
                    spec,
                    plan.choice.reasoning_effort,
                    cache_key=f"openpaper:{conversation_id}",
                ),
                usage_limits=UsageLimits(
                    # Leave headroom after the tool budget is exhausted so
                    # the model can see the budget error and produce a final
                    # answer. Generous headroom on purpose: at +2 a single
                    # over-budget tool call would RAISE instead of degrading
                    # into the budget message.
                    request_limit=MAX_AGENTIC_ITERATIONS + 5,
                    tool_calls_limit=None,
                ),
                metadata={
                    "paper_id": paper_id,
                    "context_mode": chat_context.context_mode,
                    "provider": spec.provider.value,
                    "model": spec.id,
                },
            ),
            on_complete=on_complete,
        )
        async for item in pump:
            # Persist a FAILED turn BEFORE its error chunk reaches the
            # client. `on_error` stashes `error_text` before emitting that
            # chunk, so this always fires first — otherwise a client that
            # retries the instant it sees the error would read history that
            # is missing the failed row and duplicate the turn.
            stream_state = adapter.last_event_stream
            if stream_state is not None and stream_state.error_text:
                store_unfinished()
            yield item
        # Re-raise anything the pump failed on: the plain `async for` this
        # replaced propagated encode/transform failures to the caller.
        await pump.join()
        stream_state = adapter.last_event_stream
        if stream_state is not None and stream_state._finish_reason == "error":
            track_event(
                "chat_message_error",
                properties={
                    "paper_id": str(paper_id),
                    "conversation_id": str(conversation_id),
                    "model": spec.id,
                    "error": (stream_state.error_text or "stream_error")[
                        :MAX_TELEMETRY_ERROR_CHARS
                    ],
                },
                user_id=str(current_user.id),
            )
    finally:
        # -- 5. safety net + teardown --------------------------------------
        # Stop the pump before anything else. `.cancel()` is synchronous and
        # everything up to the first `await` below is synchronous too, so the
        # pump cannot advance (or double-persist) while the safety net runs.
        pump.cancel()
        # Release the sandbox FIRST, for the same reason persistence comes
        # before the teardown awaits: they can re-raise CancelledError.
        if repo_sandbox is not None:
            try:
                repo_sandbox.close()
            except BaseException as exc:  # pragma: no cover - defensive
                logger.warning("Failed to close repo sandbox: %s", exc)
        # Persist FIRST, and synchronously: `store_unfinished` does no
        # awaiting, so it cannot be interrupted by a cancellation landing on
        # this generator, and the cancelled pump cannot advance past its own
        # await points to double-persist while it runs.
        store_unfinished()
        await pump.close(pai_model)


async def _title_and_record(
    db: Session,
    plan: PaperTurnPlan,
    current_user: CurrentUser,
    citations: List[Any],
    start_time: datetime,
) -> None:
    """Title the conversation and record the turn. Never raises."""
    # First-message title: idempotent; runs the FAST model, which can be
    # rejected by provider content filters — must stay non-fatal.
    try:
        # Off the event loop: this makes a SYNCHRONOUS LLM call for the
        # title. Inline it would stall every chunk queued behind
        # `on_complete` — and every other request on this worker.
        await asyncio.to_thread(
            rename_conversation,
            db=db,
            conversation_id=plan.conversation_id,
            user=current_user,
        )
    except Exception as exc:
        logger.warning("Conversation title generation failed (non-fatal): %s", exc)

    spec = plan.choice.spec
    try:
        track_event(
            "did_chat_message",
            properties={
                "has_user_references": bool(plan.user_references),
                "has_evidence": bool(citations),
                "llm_provider": spec.provider.value,
                "model": spec.id,
                "time_taken": (datetime.now(timezone.utc) - start_time).total_seconds(),
                "paper_id": str(plan.paper_id),
                "type": "paper",
                "context_mode": plan.chat_context.context_mode,
            },
            user_id=str(current_user.id),
        )
    except Exception:
        pass
