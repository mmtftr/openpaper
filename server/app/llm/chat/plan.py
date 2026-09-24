"""Planning a paper-chat turn: everything decided before anything is written.

`plan_paper_turn` validates the request (conversation ownership, trigger,
the single new user message), resolves the model, loads the paper context
and the conversation, decides what a resubmission does to the trailing rows,
rebuilds the prompt and the model history, and builds the model. Every
failure raises `ChatRequestError` here, before the user row is persisted or
a byte is streamed — the endpoint turns it into a real HTTP status.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from typing import Any, List, Optional, Tuple

from pydantic_ai.messages import ModelMessage
from pydantic_ai.models import Model
from pydantic_ai.ui.vercel_ai.request_types import (
    DataUIPart,
    RequestData,
    TextUIPart,
    UIMessage,
)
from sqlalchemy.orm import Session

from app.database.crud.conversation_crud import conversation_crud
from app.database.crud.message_crud import message_crud
from app.database.models import ConversableType
from app.llm.chat.evidence import convert_references_to_citations
from app.llm.chat.history import (
    BUCKET_ERROR_KEY,
    BUCKET_INTERRUPTED_KEY,
    CLIENT_MESSAGE_ID_KEY,
    MODEL_PROMPT_KEY,
    load_model_history,
)
from app.llm.chat.model_choice import ModelChoice, ModelChoiceError, choose_model
from app.llm.chat.paper import PaperChatContext, build_paper_chat_context
from app.schemas.user import CurrentUser


class ChatRequestError(ValueError):
    """Validation failure surfaced to the client before streaming starts."""

    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code


@dataclass
class PaperTurnPlan:
    """One validated turn, ready to store and run."""

    conversation_id: str
    paper_id: str
    # The client's message: text/data parts only, plus the evidence block
    # when the prompt carries user references.
    user_message: UIMessage
    # What the user typed (the row's display content)...
    user_query: str
    # ...and what the model sees (the query plus the reference block).
    model_prompt_text: str
    user_references: Optional[List[str]]
    # A resubmission's trailing rows: the user row to reuse, and a failed
    # assistant row to delete (see `_plan_trailing_reuse`).
    reused_user_row: Optional[Any]
    stale_assistant_row: Optional[Any]
    choice: ModelChoice
    chat_context: PaperChatContext
    model_history: List[ModelMessage]
    built_model: Model


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


async def plan_paper_turn(
    *,
    db: Session,
    current_user: CurrentUser,
    run_input: RequestData,
    paper_id: str,
    conversation_id: str,
    provider: Optional[str],
    model: Optional[str],
    reasoning_effort: Optional[str],
    context_mode: Optional[str],
    user_references: Optional[List[str]],
) -> PaperTurnPlan:
    """Validate the request and prepare the turn. Writes nothing.

    Raises ChatRequestError for every failure.
    """
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

    try:
        choice = choose_model(
            slot="chat.default",
            provider=provider,
            model=model,
            reasoning_effort=reasoning_effort,
        )
    except ModelChoiceError as exc:
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
    # the failed partial is deleted by the store, so a retry REPLACES the
    # failed turn instead of stacking a second copy of the question after it.
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
    model_history = await asyncio.to_thread(load_model_history, rows, spec=choice.spec)

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

    # Build the model BEFORE the user row is persisted: a mis-configured
    # provider raises here, and it must surface as a clean 4xx without
    # leaving an orphaned user row behind.
    try:
        built_model = choice.registry.build_model(choice.spec)
    except ValueError as exc:
        raise ChatRequestError(str(exc))

    return PaperTurnPlan(
        conversation_id=conversation_id,
        paper_id=paper_id,
        user_message=user_message,
        user_query=user_query,
        model_prompt_text=model_prompt_text,
        user_references=user_references,
        reused_user_row=reused_user_row,
        stale_assistant_row=stale_assistant_row,
        choice=choice,
        chat_context=chat_context,
        model_history=model_history,
        built_model=built_model,
    )
