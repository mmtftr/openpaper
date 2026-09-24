"""Paper-chat run orchestration.

`run_paper_chat` wires everything together for one turn:

1. Validates the request (conversation ownership, trigger, model)
   — failures raise before any streaming starts.
2. Persists the user row up front (client UIMessage id = idempotency key).
3. Runs the agent through `OpenPaperAdapter` / `OpenPaperEventStream`
   (evidence holdback, tool-output caps) with server-side history from
   `load_model_history`.
4. `on_complete` (never raises): emits `data-citations` twice — raw
   immediately, reconciled after the pymupdf matching — then persists the
   assistant row (content = evidence-stripped text, references =
   citations, bucket = versioned ModelMessage dump), renames the
   conversation non-fatally, and records telemetry.
5. A `finally` safety net persists a partial assistant row (with
   `bucket.interrupted=true`, plus `bucket.error` when the run FAILED
   rather than being stopped) on a fresh DB session when the client
   disconnected or the run died before `on_complete`.

Transient provider failures are retried by `RetryingModel` (3 attempts,
exponential backoff) wrapped around the built model. Because a backoff
sleep blocks the pull side of the stream, encoded chunks travel through a
queue fed by a pump task, which lets the retry callback interleave
transient `data-retry-status` chunks while the run waits.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timezone
from typing import (
    Any,
    AsyncGenerator,
    AsyncIterator,
    Awaitable,
    Callable,
    Dict,
    List,
    Optional,
    Tuple,
)

from pydantic_ai import UsageLimits
from pydantic_ai.messages import ModelMessagesTypeAdapter
from pydantic_ai.run import AgentRunResult
from pydantic_ai.ui.vercel_ai.request_types import (
    DataUIPart,
    RequestData,
    TextUIPart,
    UIMessage,
)
from pydantic_ai.ui.vercel_ai.response_types import DataChunk
from sqlalchemy.orm import Session

from app.database.crud.conversation_crud import conversation_crud
from app.database.crud.message_crud import MessageCreate, MessageUpdate, message_crud
from app.database.database import SessionLocal
from app.database.models import ConversableType
from app.database.telemetry import track_event
from app.llm.chat.evidence import (
    convert_references_to_citations,
    convert_references_to_dict,
    extract_citations,
    reconcile_citations,
    strip_evidence_blocks,
)
from app.llm.chat.history import (
    BUCKET_DUMP_KEY,
    BUCKET_ERROR_KEY,
    BUCKET_INTERRUPTED_KEY,
    BUCKET_VERSION,
    BUCKET_VERSION_KEY,
    MODEL_PROMPT_KEY,
    load_model_history,
    strip_figure_bytes,
    strip_instructions,
)
from app.llm.chat.paper import (
    MAX_AGENTIC_ITERATIONS,
    PaperAgentDeps,
    build_paper_agent,
    build_paper_chat_context,
)
from app.llm.chat.stream import MAX_ERROR_TEXT_CHARS, OpenPaperAdapter
from app.llm.chat.title import rename_conversation
from app.llm.model_registry import LLMProvider, get_registry
from app.llm.model_slots import resolve_slot
from app.llm.retrying_model import RetryingModel, RetryStatus, retry_status_payload
from app.schemas.chat_stream import (
    CITATIONS_PART_ID,
    CITATIONS_PART_TYPE,
    RETRY_STATUS_PART_TYPE,
    citations_data,
)
from app.schemas.user import CurrentUser

logger = logging.getLogger(__name__)

CLIENT_MESSAGE_ID_KEY = "client_message_id"

# Transient UI chunk announcing a provider retry. Wire contract with the
# client (camelCase payload keys):
#   {"type":"data-retry-status","transient":true,
#    "data":{"state":"retrying","attempt":2,"maxAttempts":3,
#            "delayMs":2000,"error":"..."}}
#   {"type":"data-retry-status","transient":true,"data":{"state":"recovered"}}
RETRY_STATUS_CHUNK_TYPE = RETRY_STATUS_PART_TYPE

# Sentinel pushed onto the chunk queue when the protocol stream is done.
_STREAM_END = object()

# Look-ahead the pump may build up before it blocks on the consumer. Small
# enough to keep the provider stream paced by the client, big enough that a
# burst of chunks doesn't ping-pong the event loop.
CHUNK_QUEUE_SIZE = 32

# How long teardown waits for a cancelled pump before re-cancelling it, and
# again before giving up. Cancellation can be swallowed by a `finally` that
# yields, so "cancel once and wait forever" is not safe (see `_stop_pump`).
PUMP_TEARDOWN_TIMEOUT = 5.0
# Attempts at stopping the pump; the last one closes the HTTP transport
# first, to unblock a pump waiting on the provider rather than on us.
_PUMP_STOP_ATTEMPTS = 3

# Hard ceiling on a background teardown. Comfortably above the pump budget
# (_PUMP_STOP_ATTEMPTS x PUMP_TEARDOWN_TIMEOUT), so it only ever fires for a
# close that has genuinely wedged.
TEARDOWN_DEADLINE_SECONDS = 45.0

# Strong references to in-flight (shielded) stream teardowns; without them
# the event loop can garbage-collect one mid-flight.
_BACKGROUND_TEARDOWNS: set = set()

# Cap on the failure text copied into telemetry.
MAX_TELEMETRY_ERROR_CHARS = 500


class ChatRequestError(ValueError):
    """Validation failure surfaced to the client before streaming starts."""

    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code


def _last_user_message(run_input: RequestData) -> UIMessage:
    if getattr(run_input, "trigger", None) != "submit-message":
        raise ChatRequestError(
            "Only submit-message is supported (regeneration is disabled)."
        )
    # Exactly ONE message: the adapter forwards every client-sent message to
    # the model verbatim, so accepting more would let a client inject
    # fabricated assistant turns / tool returns into the context. Server-side
    # history is the only history.
    if len(run_input.messages) != 1:
        raise ChatRequestError("Send exactly one new user message.")
    message = run_input.messages[0]
    if message.role != "user":
        raise ChatRequestError("The last message must be a user message.")
    # Text and data parts only — a FileUIPart would be forwarded to the
    # provider as a fetched/inlined attachment (unbilled, unpersisted).
    message.parts = [
        part for part in message.parts if isinstance(part, (TextUIPart, DataUIPart))
    ]
    return message


def _message_text(message: UIMessage) -> str:
    return "\n\n".join(
        part.text for part in message.parts if isinstance(part, TextUIPart)
    ).strip()


def _is_interrupted_row(row: Any) -> bool:
    """True for an assistant row persisted by the partial-turn safety net."""
    bucket = getattr(row, "bucket", None) or {}
    return isinstance(bucket, dict) and bool(bucket.get(BUCKET_INTERRUPTED_KEY))


def _is_replaceable_row(row: Any) -> bool:
    """True for an interrupted row a retry may safely DELETE.

    Only two shapes qualify: a row the run actually failed on (it carries
    `bucket.error`), and an empty one. A content-bearing row without an
    error is a user stop — or a disconnect that landed after the answer was
    complete but before `on_complete` persisted it — and deleting that would
    destroy a real answer.
    """
    if not _is_interrupted_row(row):
        return False
    bucket = getattr(row, "bucket", None) or {}
    if bucket.get(BUCKET_ERROR_KEY):
        return True
    return not str(getattr(row, "content", "") or "").strip()


def _is_duplicate_submission(rows: List[Any], client_message_id: str) -> bool:
    for row in reversed(rows):
        if row.role != "user":
            continue
        bucket = getattr(row, "bucket", None) or {}
        if (
            isinstance(bucket, dict)
            and bucket.get(CLIENT_MESSAGE_ID_KEY) == client_message_id
        ):
            return True
    return False


def _plan_trailing_reuse(
    rows: List[Any], user_query: str
) -> Tuple[List[Any], Optional[Any], Optional[Any]]:
    """Decide what a resubmission of `user_query` does to the trailing rows.

    Returns `(history_rows, user_row_to_reuse, failed_assistant_row_to_delete)`.

    Two shapes are recognized, both only when the trailing question matches
    the resubmitted text:

    - `[..., user]` — a dangling turn whose attempt died before persisting
      anything. Reuse the row (no duplicate question).
    - `[..., user, failed assistant]` — the attempt failed *after*
      persisting an errored (or empty) row. Reuse the question AND drop the
      failed row, so the retry replaces the failed turn instead of stacking
      a second copy of the question after it. An interrupted row that has
      TEXT but no error is a user stop, not a failure: it is left alone (see
      `_is_replaceable_row`) and the resubmission starts a fresh turn.

    Either way the reused rows leave the model history: the run input
    already carries the prompt.
    """
    if (
        len(rows) >= 2
        and rows[-1].role == "assistant"
        and _is_replaceable_row(rows[-1])
        and rows[-2].role == "user"
        and str(rows[-2].content or "").strip() == user_query
    ):
        return rows[:-2], rows[-2], rows[-1]
    if rows and rows[-1].role == "user":
        trailing = rows[-1]
        if str(trailing.content or "").strip() == user_query:
            return rows[:-1], trailing, None
    return rows, None, None


def _stored_model_prompt(row: Any, user_query: str) -> Optional[str]:
    """The exact prompt a reused user row's model saw, if it extends the
    resubmitted text (i.e. it carries the reference-citation block)."""
    bucket = getattr(row, "bucket", None)
    if not isinstance(bucket, dict):
        return None
    prompt = bucket.get(MODEL_PROMPT_KEY)
    if (
        isinstance(prompt, str)
        and prompt.startswith(user_query)
        and prompt.strip() != user_query
    ):
        return prompt
    return None


def _stored_references(row: Any) -> List[str]:
    """The reference strings persisted on a user row, oldest form first."""
    references = getattr(row, "references", None)
    if not isinstance(references, dict):
        return []
    out: List[str] = []
    for citation in references.get("citations") or []:
        if not isinstance(citation, dict):
            continue
        text = citation.get("reference")
        if isinstance(text, str) and text.strip():
            out.append(text)
    return out


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

    conversation = conversation_crud.get(db, conversation_id, user=current_user)
    if not conversation:
        raise ChatRequestError("Conversation not found.", status_code=404)
    if run_input.id and run_input.id != conversation_id:
        raise ChatRequestError("Chat id does not match conversation_id.")
    # The conversation must actually belong to this paper — otherwise an
    # owned conversation for paper A could be replayed against paper B's
    # context (and persist the result into A's history).
    if (
        conversation.conversable_type != ConversableType.PAPER
        or str(conversation.conversable_id) != paper_id
    ):
        raise ChatRequestError(
            "Conversation does not belong to this paper.", status_code=400
        )

    user_message = _last_user_message(run_input)
    user_query = _message_text(user_message)
    if not user_query:
        raise ChatRequestError("Empty message.")

    # Model selection: resolve against the registry.
    registry = get_registry()
    provider_enum: Optional[LLMProvider] = None
    if provider:
        try:
            provider_enum = LLMProvider(provider.lower())
        except ValueError:
            raise ChatRequestError(f"Unknown provider '{provider}'.")
    try:
        if provider_enum is None and not model:
            slot = resolve_slot("chat.default", registry)
            spec = slot.spec
            reasoning_effort = reasoning_effort or slot.reasoning_effort
        else:
            spec = registry.resolve(provider_enum, model)
    except ValueError as exc:
        raise ChatRequestError(str(exc))

    try:
        chat_context = build_paper_chat_context(
            db,
            paper_id=paper_id,
            current_user=current_user,
            context_mode=context_mode or "adaptive",
        )
    except ValueError as exc:
        raise ChatRequestError(str(exc), status_code=404)

    # Full chronological history BEFORE persisting the new user row.
    rows = message_crud.get_all_conversation_messages(
        db, conversation_id=uuid.UUID(conversation_id), current_user=current_user
    )

    # A trailing user row with no assistant reply is a dangling turn (the
    # previous attempt failed before producing content). A resubmission of
    # the same text reuses that row instead of inserting a duplicate — and
    # the dangling row is excluded from model history since the run input
    # carries the prompt. When the failed attempt DID leave an assistant row
    # behind (interrupted / errored), the pair is reused the same way and
    # the failed partial is deleted below, so a retry REPLACES the failed
    # turn instead of stacking a second copy of the question after it.
    # A duplicate of a COMPLETED turn is a client retry bug and gets a 409.
    rows, reused_user_row, stale_assistant_row = _plan_trailing_reuse(rows, user_query)
    if _is_duplicate_submission(rows, user_message.id):
        raise ChatRequestError("This message was already submitted.", status_code=409)
    # A retry that resends the text alone must not silently ask a different
    # question: the references are the prompt's evidence block, and the
    # reused row still has them. Prefer the stored prompt verbatim (ground
    # truth for what the model saw); fall back to regenerating the block
    # from the stored references. (The web client resends references
    # itself; this covers everything else.)
    stored_prompt: Optional[str] = None
    if not user_references and reused_user_row is not None:
        stored_prompt = _stored_model_prompt(reused_user_row, user_query)
        if stored_prompt is None:
            user_references = _stored_references(reused_user_row) or None
    # Off the event loop: replay may rehydrate figure bytes from S3
    # (blocking boto3 calls) and JSON-serialize large dumps.
    #
    # The spec is THIS turn's model, and replay is sanitized for it: a figure
    # fetched by an earlier vision-capable model would otherwise be replayed
    # as image content, and a gpt-5.x reasoning item id would be replayed as
    # a bogus message field — either one hard-400s DeepSeek V4 Flash / Kimi
    # K3 and poisons the conversation for every later turn on that model.
    model_history = await asyncio.to_thread(load_model_history, rows, spec=spec)

    # User citations ride along as an evidence block appended to the prompt
    # text (ground truth: the dump will contain exactly what the model saw).
    model_prompt_text = user_query
    if user_references:
        citation_block = convert_references_to_citations(user_references)
        user_message.parts.append(TextUIPart(text=citation_block, state="done"))
        model_prompt_text = f"{user_query}\n\n{citation_block}"
    elif stored_prompt is not None:
        citation_block = stored_prompt[len(user_query) :].strip()
        user_message.parts.append(TextUIPart(text=citation_block, state="done"))
        model_prompt_text = stored_prompt

    # Build the model BEFORE persisting the user row: a mis-configured
    # provider raises here, and it must surface as a clean 4xx without
    # leaving an orphaned user row behind.
    try:
        built_model = registry.build_model(spec)
    except ValueError as exc:
        raise ChatRequestError(str(exc))

    # Encoded chunks reach the response through a queue rather than a plain
    # `async for`: a retry backoff blocks the pull side for seconds, and the
    # retry-status chunks have to reach the client DURING that wait.
    #
    # Bounded, so a client that stops reading still throttles the provider
    # stream the way the direct loop did instead of buffering a whole turn.
    # The bound is why the consumer ALSO stops on `pump_task.done()`: the
    # end-of-stream sentinel is pushed from a `finally` that runs under
    # cancellation too, where it must never suspend and so may be dropped.
    chunk_queue: asyncio.Queue[Any] = asyncio.Queue(maxsize=CHUNK_QUEUE_SIZE)

    async def emit_retry_status(status: RetryStatus) -> None:
        """Push a transient `data-retry-status` chunk into the live stream."""
        chunk = DataChunk(
            type=RETRY_STATUS_CHUNK_TYPE,
            data=retry_status_payload(status),
            transient=True,
        )
        stream_state = adapter.last_event_stream
        encoded = (
            stream_state.encode_event(chunk)
            if stream_state is not None
            else f"data: {chunk.encode(6)}\n\n"
        )
        if status.state == "recovered":
            # "recovered" is the ONLY chunk that clears the client's retry
            # banner, so it must not be dropped — but it must not evict a
            # queued chunk either: on a later step the queue holds real
            # content, and losing a `text-start` or a tool lifecycle chunk
            # breaks protocol pairing on the client. So block, exactly like
            # the pump already does; a stalled reader throttling us is the
            # intended behavior, and teardown's drainer means this cannot
            # deadlock after a disconnect.
            #
            # A CancelledError here MUST escape: `RetryingModel._notify`
            # swallows only `Exception`, so teardown cancellation is never
            # eaten inside the retry loop.
            await chunk_queue.put(encoded)
            return
        try:
            # A missed "retrying" is cosmetic; never stall the run for it.
            chunk_queue.put_nowait(encoded)
        except asyncio.QueueFull:
            logger.debug("Dropped retry-status chunk: chunk queue is full")

    # Retrying at the MODEL layer is what makes this safe: the agent graph
    # re-sends the full message history including executed tool calls and
    # their returns, so a retry re-asks the provider — it never re-runs a
    # tool.
    pai_model = RetryingModel(built_model, on_retry=emit_retry_status)

    formatted_references = (
        convert_references_to_dict(references=user_references)
        if user_references
        else None
    )
    user_bucket: Dict[str, Any] = {CLIENT_MESSAGE_ID_KEY: user_message.id}
    if model_prompt_text != user_query:
        # Preserve the exact prompt (with the citation block) so history
        # replay can reproduce what the model actually saw — the pydantic-ai
        # dump only contains responses, not the request.
        user_bucket[MODEL_PROMPT_KEY] = model_prompt_text
    if stale_assistant_row is not None:
        # Drop the failed partial BEFORE the new run so history (model and
        # UI alike) shows one turn, not a failure followed by its retry.
        # Non-fatal: `remove` returns None (and rolls back) on failure — the
        # retry is still worth running, it just leaves the old row visible.
        if (
            message_crud.remove(db, id=stale_assistant_row.id, user=current_user)
            is None
        ):
            logger.warning(
                "Could not delete failed assistant row %s before retry",
                stale_assistant_row.id,
            )
    if reused_user_row is not None:
        # MERGE, never overwrite. A retry resends the text only, so an empty
        # `user_references` means "unchanged", not "the user deleted their
        # citations" — and `MessageUpdate(references=None)` really does null
        # the column (`exclude_unset` keeps an explicitly-passed None). Same
        # for the bucket: rebuilding it from scratch would drop the stored
        # `model_prompt`, so the replayed prompt would silently lose its
        # evidence block.
        existing_bucket = getattr(reused_user_row, "bucket", None)
        merged_bucket: Dict[str, Any] = (
            dict(existing_bucket) if isinstance(existing_bucket, dict) else {}
        )
        merged_bucket.update(user_bucket)
        message_crud.update(
            db,
            db_obj=reused_user_row,
            obj_in=MessageUpdate(
                references=(
                    formatted_references
                    if formatted_references is not None
                    else getattr(reused_user_row, "references", None)
                ),
                bucket=merged_bucket,
            ),
            user=current_user,
        )
    else:
        message_crud.create(
            db,
            obj_in=MessageCreate(
                conversation_id=uuid.UUID(conversation_id),
                role="user",
                content=user_query,
                references=formatted_references,
                bucket=user_bucket,
            ),
            user=current_user,
        )

    assistant_id = uuid.uuid4()
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
        server_message_id=str(assistant_id),
        error_context={
            "model": spec.id,
            "provider": spec.provider.value,
            "conversation_id": conversation_id,
            "paper_id": paper_id,
        },
    )

    state: Dict[str, Any] = {"persisted": False}

    async def on_complete(result: AgentRunResult[Any]) -> AsyncIterator[DataChunk]:
        """Persist the finished turn, then emit citations. Never raises: a
        failure here must not turn an already-delivered answer into a
        client-visible error.

        Persistence happens BEFORE any yield — a client disconnect while
        citation chunks are being delivered cancels this generator at the
        yield point, and a completed turn must not be downgraded to an
        "interrupted" row just because the disconnect landed here.
        """
        citations: List[Dict[str, Any]] = []
        stream_state = adapter.last_event_stream
        full_text = stream_state.accumulated_text if stream_state else ""
        try:
            citations = extract_citations(full_text)
        except Exception as exc:
            logger.warning("Citation extraction failed (non-fatal): %s", exc)

        assistant_row = None
        try:
            dump = None
            try:
                dump = ModelMessagesTypeAdapter.dump_python(
                    result.new_messages(), mode="json"
                )
            except Exception as exc:
                logger.warning("Failed to serialize pydantic-ai messages: %s", exc)

            bucket: Dict[str, Any] = {CLIENT_MESSAGE_ID_KEY: user_message.id}
            if dump is not None:
                bucket[BUCKET_VERSION_KEY] = BUCKET_VERSION
                # Persisted WHOLE: the replayed copy of a turn must match
                # byte-for-byte what the model saw during it, or the
                # provider's prompt-cache prefix breaks on the next turn.
                # Sandbox output is already bounded at the source (24k chars
                # per call, 240k per turn — see repo/sandbox.py).
                bucket[BUCKET_DUMP_KEY] = strip_figure_bytes(strip_instructions(dump))
            assistant_row = message_crud.create(
                db,
                obj_in=MessageCreate(
                    id=assistant_id,
                    conversation_id=uuid.UUID(conversation_id),
                    role="assistant",
                    content=strip_evidence_blocks(full_text),
                    references={"citations": citations} if citations else None,
                    bucket=bucket,
                ),
                user=current_user,
            )
            state["persisted"] = True
        except Exception as exc:
            logger.error("Failed to persist assistant turn: %s", exc, exc_info=True)
            # Hand the finished turn to the safety net so it can be retried
            # on a FRESH session as the completed answer it is. Without this
            # the net would rewrite a correct, fully-streamed answer as an
            # `interrupted` row and the client would badge it as a failure.
            state["pending_complete"] = {
                "content": strip_evidence_blocks(full_text),
                "citations": citations,
                "bucket": bucket,
            }
            # Leave no failed transaction behind — later session users in
            # this callback (references update, rename, telemetry) would all
            # raise PendingRollbackError otherwise.
            try:
                db.rollback()
            except Exception:
                pass

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
                )
                if reconciled:
                    if assistant_row is not None:
                        message_crud.update(
                            db,
                            db_obj=assistant_row,
                            obj_in=MessageUpdate(references={"citations": reconciled}),
                            user=current_user,
                        )
                    yield DataChunk(
                        type=CITATIONS_PART_TYPE,
                        id=CITATIONS_PART_ID,
                        data=citations_data(reconciled),
                    )
            except Exception as exc:
                logger.warning("Citation reconciliation failed (non-fatal): %s", exc)

        # First-message title: idempotent; runs the FAST model, which can be
        # rejected by provider content filters — must stay non-fatal.
        try:
            # Off the event loop: this makes a SYNCHRONOUS LLM call for the
            # title. Inline it would stall every chunk queued behind
            # `on_complete` — and every other request on this worker.
            await asyncio.to_thread(
                rename_conversation,
                db=db,
                conversation_id=conversation_id,
                user=current_user,
            )
        except Exception as exc:
            logger.warning("Conversation title generation failed (non-fatal): %s", exc)

        try:
            track_event(
                "did_chat_message",
                properties={
                    "has_user_references": bool(user_references),
                    "has_evidence": bool(citations),
                    "llm_provider": spec.provider.value,
                    "model": spec.id,
                    "time_taken": (
                        datetime.now(timezone.utc) - start_time
                    ).total_seconds(),
                    "paper_id": str(paper_id),
                    "type": "paper",
                    "context_mode": chat_context.context_mode,
                },
                user_id=str(current_user.id),
            )
        except Exception:
            pass

    def persist_failed_turn() -> None:
        """Write the unfinished turn's assistant row, at most once.

        Called twice on the error path — once before the error chunk goes
        out, once from the `finally` — and the second call is what makes a
        transient DB failure survivable, so the "done" latch may only be set
        when the row is actually there. Writes are id-keyed and skip an
        existing row, so a retry after an ambiguous commit cannot duplicate.
        """
        if state["persisted"]:
            return
        pending = state.get("pending_complete")
        if pending is not None:
            # A complete answer whose INSERT failed inside `on_complete`.
            persisted = _persist_assistant_row(
                assistant_id=assistant_id,
                conversation_id=conversation_id,
                current_user=current_user,
                content=pending["content"],
                citations=pending["citations"],
                bucket=pending["bucket"],
            )
        else:
            stream_state = adapter.last_event_stream
            persisted = _persist_partial_turn(
                assistant_id=assistant_id,
                conversation_id=conversation_id,
                current_user=current_user,
                client_message_id=user_message.id,
                full_text=stream_state.accumulated_text if stream_state else "",
                error_text=stream_state.error_text if stream_state else None,
            )
        state["persisted"] = persisted

    # Everything from the sandbox acquisition onwards runs under ONE
    # try/finally: the pool has two slots per worker, so a failure between
    # taking one and reaching teardown would wedge the repo feature for the
    # life of the process.
    native_stream = None
    event_stream = None
    pump_task: Optional[asyncio.Task] = None
    try:
        if repo_sandbox is not None:
            await repo_sandbox.open()

        # Compose the stream manually (instead of adapter.run_stream) so the
        # NATIVE agent event stream can be closed directly on interruption.
        # Closing only the outer protocol generator is not enough:
        # pydantic-ai's transform_stream yields its finish chunks from a
        # `finally`, which makes aclose() raise "async generator ignored
        # GeneratorExit" and leaves the provider HTTP stream running (and
        # billing) in the background.
        native_stream = adapter.run_stream_native(
            message_history=model_history,
            deps=deps,
            # The cache key routes every turn of one conversation to the
            # same prompt-cache owner, which is the other half of the
            # prefix-stability work in `load_model_history`: a stable prefix
            # only hits if the request lands where that prefix is cached.
            model_settings=registry.build_settings(
                spec, reasoning_effort, cache_key=f"openpaper:{conversation_id}"
            ),
            usage_limits=UsageLimits(
                # Leave headroom after the manual tool budget is exhausted so
                # the model can see the budget error and produce a final
                # answer. Generous headroom on purpose: at +2 a single
                # over-budget tool call would RAISE instead of degrading into
                # the budget message.
                request_limit=MAX_AGENTIC_ITERATIONS + 5,
                tool_calls_limit=None,
            ),
            metadata={
                "paper_id": paper_id,
                "context_mode": chat_context.context_mode,
                "provider": spec.provider.value,
                "model": spec.id,
            },
        )
        event_stream = adapter.transform_stream(native_stream, on_complete=on_complete)

        async def pump() -> None:
            """Drive the protocol stream into the queue.

            A task, not an inline `async for`, so `emit_retry_status` — which
            fires from inside the agent run, i.e. from THIS task — can hand a
            transient chunk to the consumer while a retry backoff sleeps.
            """
            try:
                async for encoded in adapter.encode_stream(event_stream):
                    await chunk_queue.put(encoded)
            finally:
                # `put_nowait`, never `await put`: this also runs when the
                # pump is CANCELLED, where suspending would strand the
                # consumer forever. A full queue drops the sentinel, which
                # the consumer's `pump_task.done()` check covers.
                try:
                    chunk_queue.put_nowait(_STREAM_END)
                except asyncio.QueueFull:
                    pass

        pump_task = asyncio.create_task(pump())
        while True:
            # Ends on the sentinel, or on a finished pump with nothing left
            # (the sentinel can be dropped when the queue is full).
            if chunk_queue.empty() and pump_task.done():
                break
            item = await chunk_queue.get()
            if item is _STREAM_END:
                break
            # Persist a FAILED turn BEFORE its error chunk reaches the
            # client. `on_error` stashes `error_text` before emitting that
            # chunk, so this always fires first — otherwise a client that
            # retries the instant it sees the error would read history that
            # is missing the failed row and duplicate the turn.
            stream_state = adapter.last_event_stream
            if stream_state is not None and stream_state.error_text:
                persist_failed_turn()
            yield item
        # Re-raise anything the pump failed on: the plain `async for` this
        # replaced propagated encode/transform failures to the caller.
        await pump_task
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
        # Stop the pump before anything else. `.cancel()` is synchronous and
        # everything up to the first `await` below is synchronous too, so the
        # pump cannot advance (or double-persist) while the safety net runs.
        if pump_task is not None:
            pump_task.cancel()
        # Release the sandbox FIRST, for the same reason persistence comes
        # before the aclose() awaits: they can re-raise CancelledError.
        if repo_sandbox is not None:
            try:
                repo_sandbox.close()
            except BaseException as exc:  # pragma: no cover - defensive
                logger.warning("Failed to close repo sandbox: %s", exc)
        # Persist FIRST, and synchronously: `_persist_partial_turn` does no
        # awaiting, so it cannot be interrupted by a cancellation landing on
        # this generator, and the cancelled pump cannot advance past its own
        # await points to double-persist while it runs.
        persist_failed_turn()
        # SHIELDED: teardown must finish even when this generator is being
        # torn down by task cancellation (server shutdown, anyio disconnect).
        # anyio re-delivers cancellation at every await, so awaiting the
        # closes inline would abandon them — leaving the provider HTTP
        # stream open and billing, and racing `aclose()` against a pump that
        # is still iterating the same generator ("already running").
        teardown = _schedule_teardown(
            _close_stream_stack(
                pump_task, chunk_queue, native_stream, event_stream, pai_model
            )
        )
        if teardown is not None:
            try:
                await asyncio.shield(teardown)
            except BaseException:
                # Cancelled out from under us — `teardown` keeps running to
                # completion on its own (the registry holds the reference).
                pass


def _teardown_finished(task: "asyncio.Task") -> None:
    """Retire a background teardown, surfacing whatever it ended with.

    Retrieving the result matters: a task whose exception is never read logs
    "Task exception was never retrieved" at GC time, from a context with no
    connection to the request that produced it.
    """
    _BACKGROUND_TEARDOWNS.discard(task)
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        logger.warning("Chat stream teardown did not finish cleanly: %s", exc)


def _schedule_teardown(coro: Any) -> Optional["asyncio.Task"]:
    """Run `coro` as a tracked, time-bounded background task.

    The deadline is the backstop for a close that never returns (a wedged
    provider socket): without it a task would sit in `_BACKGROUND_TEARDOWNS`
    forever and they would pile up across disconnects.
    """
    try:
        task = asyncio.ensure_future(asyncio.wait_for(coro, TEARDOWN_DEADLINE_SECONDS))
    except RuntimeError as exc:  # pragma: no cover - loop already gone
        # Nothing left to schedule on (interpreter/loop shutdown). Do not
        # mask whatever exception is propagating through the caller's
        # `finally`.
        logger.warning("Could not schedule chat stream teardown: %s", exc)
        coro.close()
        return None
    _BACKGROUND_TEARDOWNS.add(task)
    task.add_done_callback(_teardown_finished)
    return task


async def _stop_pump(
    pump_task: asyncio.Task,
    chunk_queue: asyncio.Queue,
    *,
    escalate: Optional[Callable[[], Awaitable[None]]] = None,
) -> bool:
    """Wait out an already-cancelled pump without letting it block.

    Never raises. Returns True when the pump is finished, False when it had
    to be ABANDONED — the caller must then treat the streams as still owned
    by it (see `_close_stream_stack`).

    Two hazards, both real:

    1. `transform_stream` yields its finish chunks from a `finally`
       (pydantic-ai `ui/_event_stream.py`), so a `CancelledError` delivered
       while the pump sits in `__anext__` is CONSUMED by that yield: the
       generator hands back a chunk, `__anext__` returns normally, and the
       pump keeps running. Cancellation is edge-triggered, so it does not
       fire again by itself.
    2. The pump then blocks on `queue.put()` — the consumer is gone, so
       nobody will ever make room, and teardown would hang forever (the
       response task never completes and its DB session is never returned).

    So: drain concurrently for the WHOLE sequence (the pump can never block
    on the queue), re-cancel on each timeout, and before the final attempt
    run `escalate` — closing the HTTP transport, which unblocks a pump stuck
    on provider I/O rather than on us.
    """

    async def drain() -> None:
        while True:
            await chunk_queue.get()

    drainer = asyncio.ensure_future(drain())
    try:
        for attempt in range(_PUMP_STOP_ATTEMPTS):
            if attempt == _PUMP_STOP_ATTEMPTS - 1 and escalate is not None:
                try:
                    await escalate()
                except BaseException as exc:  # pragma: no cover - defensive
                    logger.warning("Teardown escalation failed: %s", exc)
            try:
                await asyncio.wait_for(asyncio.shield(pump_task), PUMP_TEARDOWN_TIMEOUT)
                return True
            except asyncio.CancelledError:
                # Ambiguous: either the pump finished (cancelled, which the
                # shield reports as CancelledError) or THIS teardown task is
                # being cancelled. Only `pump_task` can tell them apart.
                if pump_task.done():
                    return True
                pump_task.cancel()
            except asyncio.TimeoutError:
                # A `finally`-yield swallowed the last cancel: re-arm it.
                pump_task.cancel()
            except Exception:
                # The pump raised — it is finished either way.
                return True
        if pump_task.done():
            return True
        logger.error(
            "Chat pump did not stop within %ss; abandoning it",
            _PUMP_STOP_ATTEMPTS * PUMP_TEARDOWN_TIMEOUT,
        )
        return False
    finally:
        drainer.cancel()
        try:
            await drainer
        except BaseException:
            # Cancelled, as intended — awaiting just keeps the loop from
            # collecting it while still pending.
            pass


async def _close_stream_stack(
    pump_task: Optional[asyncio.Task],
    chunk_queue: asyncio.Queue,
    native_stream: Optional[Any],
    event_stream: Optional[Any],
    model: Optional[Any] = None,
) -> None:
    """Stop the pump, close the agent streams, release the client.

    Never raises. Order matters: the pump owns the iteration of both
    generators, so it has to be finished before either is closed — and the
    HTTP client outlives both, so it goes last.
    """
    from app.llm._pai_compat import close_model_transport

    transport_closed = False

    async def close_transport() -> None:
        # Idempotent: used both as the escalation lever and as the last step.
        nonlocal transport_closed
        if transport_closed:
            return
        transport_closed = True
        await close_model_transport(model)

    if pump_task is not None:
        stopped = await _stop_pump(pump_task, chunk_queue, escalate=close_transport)
        if not stopped:
            # The pump may still be inside `__anext__` on these very
            # generators. Closing one under active iteration raises
            # "asynchronous generator is already running" and can corrupt
            # the run's teardown — worse than leaking them to the garbage
            # collector, which is what we do instead. The transport is
            # already closed (the escalation above), so nothing is left
            # streaming or billing.
            logger.error(
                "Skipping stream close: chat pump still running (streams left to GC)"
            )
            return
    # Both may be None when the failure happened before they were built.
    try:
        if native_stream is not None:
            await native_stream.aclose()
    except BaseException as exc:
        logger.warning("Failed to close native agent stream: %s", exc)
    try:
        if event_stream is not None:
            await event_stream.aclose()
    except BaseException:
        # transform_stream yields finish chunks from its `finally`;
        # aclose() reports that as RuntimeError. The native stream is
        # already closed above, so nothing is leaked.
        pass
    # The client is built per request and the pydantic-ai provider does not
    # own it, so nothing else would ever close it.
    await close_transport()


def _persist_assistant_row(
    *,
    assistant_id: uuid.UUID,
    conversation_id: str,
    current_user: CurrentUser,
    content: str,
    citations: Optional[List[Dict[str, Any]]],
    bucket: Dict[str, Any],
) -> bool:
    """Write the assistant row on a FRESH session. True = the row is there.

    The request-scoped session may already be torn down (client disconnect)
    or poisoned (the failure that sent us here), hence a new one.

    Idempotent by the preallocated id: an existing row counts as success, so
    retrying after an ambiguous commit can never duplicate the turn. False
    means "not stored" — the caller may (and does) try again later.
    """
    session = SessionLocal()
    try:
        from app.database.models import Message

        if session.get(Message, assistant_id) is not None:
            logger.info("Assistant turn %s already persisted", assistant_id)
            return True
        message_crud.create(
            session,
            obj_in=MessageCreate(
                id=assistant_id,
                conversation_id=uuid.UUID(conversation_id),
                role="assistant",
                content=content,
                references={"citations": citations} if citations else None,
                bucket=bucket,
            ),
            user=current_user,
        )
        logger.info(
            "Persisted assistant turn %s for conversation %s (interrupted=%s)",
            assistant_id,
            conversation_id,
            bool(bucket.get(BUCKET_INTERRUPTED_KEY)),
        )
        return True
    except Exception as exc:
        logger.error("Failed to persist assistant turn: %s", exc, exc_info=True)
        return False
    finally:
        session.close()


def _persist_partial_turn(
    *,
    assistant_id: uuid.UUID,
    conversation_id: str,
    current_user: CurrentUser,
    client_message_id: str,
    full_text: str,
    error_text: Optional[str] = None,
) -> bool:
    """Persist whatever streamed before an interruption.

    `error_text` (set only when the run actually FAILED, as opposed to the
    user stopping it) forces the row to be written even with no content: an
    empty failed turn still has to be visible to the client, which renders
    it from `bucket.error` and offers a retry.

    Returns True when there is nothing left to do — including the "nothing
    worth storing" case, which is a deliberate skip, not a failure.
    """
    content = strip_evidence_blocks(full_text)
    if not content and not error_text:
        return True
    bucket: Dict[str, Any] = {
        CLIENT_MESSAGE_ID_KEY: client_message_id,
        BUCKET_INTERRUPTED_KEY: True,
    }
    if error_text:
        bucket[BUCKET_ERROR_KEY] = {"message": error_text[:MAX_ERROR_TEXT_CHARS]}
    try:
        citations = extract_citations(full_text)
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("Citation extraction failed on partial turn: %s", exc)
        citations = []
    return _persist_assistant_row(
        assistant_id=assistant_id,
        conversation_id=conversation_id,
        current_user=current_user,
        content=content,
        citations=citations,
        bucket=bucket,
    )
