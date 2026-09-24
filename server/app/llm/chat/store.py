"""Persisting a paper-chat turn: the user row, then the assistant row.

`store_user_turn` writes the question before the run starts (the client's
UIMessage id is the idempotency key). `AssistantTurnStore` owns the answer's
row, which is written exactly once by whichever path ends the turn:

- `store_completed` from `on_complete` (content = evidence-stripped text,
  references = citations, bucket = versioned ModelMessage dump);
- `store_unfinished` from the runtime's error path and its `finally` safety
  net — a completed answer whose INSERT failed is retried as the completed
  answer it is; anything else becomes a partial row with
  `bucket.interrupted=true`, plus `bucket.error` when the run FAILED rather
  than being stopped. Those writes go through a FRESH session: the request's
  may already be torn down (client disconnect) or poisoned (the failure that
  sent us here).
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, Dict, List, Optional, Tuple

from pydantic_ai.messages import ModelMessagesTypeAdapter
from pydantic_ai.run import AgentRunResult
from sqlalchemy.orm import Session

from app.database.crud.message_crud import MessageCreate, MessageUpdate, message_crud
from app.database.database import SessionLocal
from app.database.models import Message
from app.llm.chat.evidence import (
    convert_references_to_dict,
    extract_citations,
    strip_evidence_blocks,
)
from app.llm.chat.history import (
    BUCKET_DUMP_KEY,
    BUCKET_ERROR_KEY,
    BUCKET_INTERRUPTED_KEY,
    BUCKET_VERSION,
    BUCKET_VERSION_KEY,
    CLIENT_MESSAGE_ID_KEY,
    MODEL_PROMPT_KEY,
    strip_figure_bytes,
    strip_instructions,
)
from app.llm.chat.plan import PaperTurnPlan
from app.llm.chat.stream import MAX_ERROR_TEXT_CHARS
from app.schemas.user import CurrentUser

logger = logging.getLogger(__name__)

Citations = List[Dict[str, Any]]


def store_user_turn(
    db: Session, plan: PaperTurnPlan, current_user: CurrentUser
) -> None:
    """Persist the question — a new row, or the reused row of a retry."""
    formatted_references = (
        convert_references_to_dict(references=plan.user_references)
        if plan.user_references
        else None
    )
    user_bucket: Dict[str, Any] = {CLIENT_MESSAGE_ID_KEY: plan.user_message.id}
    if plan.model_prompt_text != plan.user_query:
        # Preserve the exact prompt (with the citation block) so history
        # replay can reproduce what the model actually saw — the pydantic-ai
        # dump only contains responses, not the request.
        user_bucket[MODEL_PROMPT_KEY] = plan.model_prompt_text
    if plan.stale_assistant_row is not None:
        # Drop the failed partial BEFORE the new run so history (model and
        # UI alike) shows one turn, not a failure followed by its retry.
        # Non-fatal: the retry is still worth running, it just leaves the old
        # row visible.
        try:
            message_crud.remove(db, id=plan.stale_assistant_row.id, user=current_user)
        except Exception:
            db.rollback()
            logger.warning(
                "Could not delete failed assistant row %s before retry",
                plan.stale_assistant_row.id,
                exc_info=True,
            )
    reused = plan.reused_user_row
    if reused is not None:
        # MERGE, never overwrite. A retry resends the text only, so an empty
        # `user_references` means "unchanged", not "the user deleted their
        # citations" — and `MessageUpdate(references=None)` really does null
        # the column (`exclude_unset` keeps an explicitly-passed None). Same
        # for the bucket: rebuilding it from scratch would drop the stored
        # `model_prompt`, so the replayed prompt would silently lose its
        # evidence block.
        existing_bucket = getattr(reused, "bucket", None)
        merged_bucket: Dict[str, Any] = (
            dict(existing_bucket) if isinstance(existing_bucket, dict) else {}
        )
        merged_bucket.update(user_bucket)
        message_crud.update(
            db,
            db_obj=reused,
            obj_in=MessageUpdate(
                references=(
                    formatted_references
                    if formatted_references is not None
                    else getattr(reused, "references", None)
                ),
                bucket=merged_bucket,
            ),
            user=current_user,
        )
    else:
        message_crud.create(
            db,
            obj_in=MessageCreate(
                conversation_id=uuid.UUID(plan.conversation_id),
                role="user",
                content=plan.user_query,
                references=formatted_references,
                bucket=user_bucket,
            ),
            user=current_user,
        )


class AssistantTurnStore:
    """The answer's row for one turn, under a preallocated id."""

    def __init__(
        self,
        *,
        db: Session,
        current_user: CurrentUser,
        conversation_id: str,
        client_message_id: str,
    ) -> None:
        self.db = db
        self.current_user = current_user
        self.conversation_id = conversation_id
        self.client_message_id = client_message_id
        # Also the UIMessage id the stream announces in its `start` chunk.
        self.assistant_id = uuid.uuid4()
        self.persisted = False
        # A completed answer whose INSERT failed in `store_completed`,
        # handed to `store_unfinished` to retry on a fresh session.
        self._pending_complete: Optional[Dict[str, Any]] = None

    def store_completed(
        self, result: AgentRunResult[Any], full_text: str
    ) -> Tuple[Optional[Any], Citations]:
        """Persist the finished turn. Never raises.

        Returns `(row, citations)`; `row` is None when the INSERT failed (the
        answer is then pending for `store_unfinished`).
        """
        citations: Citations = []
        try:
            citations = extract_citations(full_text)
        except Exception as exc:
            logger.warning("Citation extraction failed (non-fatal): %s", exc)

        assistant_row = None
        bucket: Dict[str, Any] = {CLIENT_MESSAGE_ID_KEY: self.client_message_id}
        try:
            dump = None
            try:
                dump = ModelMessagesTypeAdapter.dump_python(
                    result.new_messages(), mode="json"
                )
            except Exception as exc:
                logger.warning("Failed to serialize pydantic-ai messages: %s", exc)

            if dump is not None:
                bucket[BUCKET_VERSION_KEY] = BUCKET_VERSION
                # Persisted WHOLE: the replayed copy of a turn must match
                # byte-for-byte what the model saw during it, or the
                # provider's prompt-cache prefix breaks on the next turn.
                # Sandbox output is already bounded at the source (24k chars
                # per call, 240k per turn — see repo/sandbox.py).
                bucket[BUCKET_DUMP_KEY] = strip_figure_bytes(strip_instructions(dump))
            assistant_row = message_crud.create(
                self.db,
                obj_in=MessageCreate(
                    id=self.assistant_id,
                    conversation_id=uuid.UUID(self.conversation_id),
                    role="assistant",
                    content=strip_evidence_blocks(full_text),
                    references={"citations": citations} if citations else None,
                    bucket=bucket,
                ),
                user=self.current_user,
            )
            self.persisted = True
        except Exception as exc:
            logger.error("Failed to persist assistant turn: %s", exc, exc_info=True)
            # Hand the finished turn to the safety net so it can be retried
            # on a FRESH session as the completed answer it is. Without this
            # the net would rewrite a correct, fully-streamed answer as an
            # `interrupted` row and the client would badge it as a failure.
            self._pending_complete = {
                "content": strip_evidence_blocks(full_text),
                "citations": citations,
                "bucket": bucket,
            }
            # Leave no failed transaction behind — later session users in
            # `on_complete` (references update, rename, telemetry) would all
            # raise PendingRollbackError otherwise.
            try:
                self.db.rollback()
            except Exception:
                pass
        return assistant_row, citations

    def store_reconciled(self, row: Any, citations: Citations) -> None:
        """Replace the stored citations with their reconciled form."""
        message_crud.update(
            self.db,
            db_obj=row,
            obj_in=MessageUpdate(references={"citations": citations}),
            user=self.current_user,
        )

    def store_unfinished(self, full_text: str, error_text: Optional[str]) -> None:
        """Write the unfinished turn's assistant row, at most once.

        Called twice on the error path — once before the error chunk goes
        out, once from the runtime's `finally` — and the second call is what
        makes a transient DB failure survivable, so the "done" latch may only
        be set when the row is actually there. Writes are id-keyed and skip
        an existing row, so a retry after an ambiguous commit cannot
        duplicate. Synchronous on purpose: nothing here awaits, so a
        cancellation cannot interrupt it.
        """
        if self.persisted:
            return
        pending = self._pending_complete
        if pending is not None:
            # A complete answer whose INSERT failed inside `on_complete`.
            persisted = _persist_assistant_row(
                assistant_id=self.assistant_id,
                conversation_id=self.conversation_id,
                current_user=self.current_user,
                content=pending["content"],
                citations=pending["citations"],
                bucket=pending["bucket"],
            )
        else:
            persisted = _persist_partial_turn(
                assistant_id=self.assistant_id,
                conversation_id=self.conversation_id,
                current_user=self.current_user,
                client_message_id=self.client_message_id,
                full_text=full_text,
                error_text=error_text,
            )
        self.persisted = persisted


def _persist_assistant_row(
    *,
    assistant_id: uuid.UUID,
    conversation_id: str,
    current_user: CurrentUser,
    content: str,
    citations: Optional[Citations],
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
