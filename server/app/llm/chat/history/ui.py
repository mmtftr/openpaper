"""Stored rows -> Vercel AI UIMessages for the client (`useChat({messages})`).

The same parts the live stream produced — reasoning, tool calls with
truncated outputs, step boundaries, text with evidence stripped, and a
`data-citations` part from the row's `references` column. Legacy rows (no
dump) degrade to text + citations. An unfinished turn carries
`metadata.interrupted`, plus `metadata.errorText` when the run failed
(never on a user stop). The raw `bucket` column never goes over the wire.

Rows arrive already paged ("load earlier" is `message_crud.
get_conversation_messages` behind `GET /api/conversation/{id}`); this module
only renders them.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional, Sequence

from pydantic import ValidationError
from pydantic_ai.messages import ModelMessagesTypeAdapter
from pydantic_ai.ui.vercel_ai import VercelAIAdapter
from pydantic_ai.ui.vercel_ai.request_types import (
    DataUIPart,
    StepStartUIPart,
    TextUIPart,
    ToolOutputAvailablePart,
    UIMessage,
    UIMessagePart,
)

from app.database.models import Message
from app.llm.chat.evidence import strip_evidence_blocks
from app.llm.chat.history.bucket import (
    BUCKET_ERROR_KEY,
    BUCKET_INTERRUPTED_KEY,
    CLIENT_MESSAGE_ID_KEY,
    dump_from_bucket,
)
from app.llm.chat.stream import EvidenceFilter, truncate_tool_output
from app.schemas.chat_stream import (
    CITATIONS_PART_ID,
    CITATIONS_PART_TYPE,
    citations_data,
    message_metadata,
)

logger = logging.getLogger(__name__)


def _citations_part(references: Any) -> Optional[DataUIPart]:
    if not references or not isinstance(references, dict):
        return None
    citations = references.get("citations")
    if not citations:
        return None
    try:
        data = citations_data(citations)
    except ValidationError:
        # A stored row predating the current citation shape: serve it as-is
        # rather than failing the whole conversation page.
        logger.warning("Stored citations do not match ChatCitation; sent unvalidated")
        data = {"citations": citations}
    return DataUIPart(type=CITATIONS_PART_TYPE, id=CITATIONS_PART_ID, data=data)


def _clean_assistant_parts(parts: List[UIMessagePart]) -> List[UIMessagePart]:
    """Strip evidence from text parts, drop empties, cap tool outputs.

    Evidence stripping is stateful ACROSS text parts (one `EvidenceFilter`
    instance for the whole turn), matching the live stream's behavior when
    a block spans a response boundary.
    """
    evidence_filter = EvidenceFilter()
    cleaned: List[UIMessagePart] = []
    last_text_index: Optional[int] = None
    for part in parts:
        if isinstance(part, TextUIPart):
            # Flush only at the very end — a delimiter can straddle two
            # adjacent text parts, and flushing per part would leak the
            # fragment as visible text.
            stripped = evidence_filter.push(part.text).strip()
            if not stripped:
                continue
            cleaned.append(TextUIPart(text=stripped, state="done"))
            last_text_index = len(cleaned) - 1
        elif isinstance(part, ToolOutputAvailablePart):
            cleaned.append(
                part.model_copy(update={"output": truncate_tool_output(part.output)})
            )
        else:
            cleaned.append(part)
    tail = evidence_filter.flush().strip()
    if tail:
        if last_text_index is not None:
            existing = cleaned[last_text_index]
            assert isinstance(existing, TextUIPart)
            cleaned[last_text_index] = TextUIPart(
                text=existing.text + tail, state="done"
            )
        else:
            cleaned.append(TextUIPart(text=tail, state="done"))
    return cleaned


def _assistant_parts_from_dump(dump: Any) -> Optional[List[UIMessagePart]]:
    """Rebuild this turn's assistant UI parts from its ModelMessage dump.

    Returns None when the dump can't be deserialized (caller falls back to
    text). One `step-start` part precedes each ModelResponse's parts, which
    is what the live stream produces client-side.
    """
    try:
        # No figure rehydration: UI parts don't need image bytes.
        messages = ModelMessagesTypeAdapter.validate_json(json.dumps(dump))
        ui_messages = VercelAIAdapter.dump_messages(messages, sdk_version=6)
    except Exception as exc:
        logger.warning("Failed to build UI parts from dump: %s", exc)
        return None

    parts: List[UIMessagePart] = []
    for ui_message in ui_messages:
        if ui_message.role != "assistant":
            # The dump's leading ModelRequest re-emits the user prompt (with
            # the citation block appended) — the DB user row is the source
            # of truth for that, so drop it here.
            continue
        parts.append(StepStartUIPart())
        parts.extend(ui_message.parts)
    return _clean_assistant_parts(parts)


def _interrupted_metadata(message: Message) -> Optional[Dict[str, Any]]:
    """UIMessage metadata for a turn that never completed.

    `{"interrupted": true}` alone means the turn was cut short (disconnect
    or user stop); `errorText` is added when the run actually failed, so the
    client can render the failure and offer a retry. camelCase is the wire
    contract with the client.
    """
    bucket = getattr(message, "bucket", None)
    if not isinstance(bucket, dict) or not bucket.get(BUCKET_INTERRUPTED_KEY):
        return None
    error = bucket.get(BUCKET_ERROR_KEY)
    error_text: Optional[str] = None
    if isinstance(error, dict):
        text = error.get("message")
        if isinstance(text, str) and text:
            error_text = text
    elif isinstance(error, str) and error:
        error_text = error
    return message_metadata(interrupted=True, error_text=error_text)


def serialize_ui_messages(rows: Sequence[Message]) -> List[Dict[str, Any]]:
    """Rows → UIMessage dicts (camelCase), ready for `useChat({messages})`."""
    out: List[Dict[str, Any]] = []
    for message in rows:
        role = str(message.role)
        if role == "user":
            parts: List[UIMessagePart] = [
                TextUIPart(text=str(message.content or ""), state="done")
            ]
            citations = _citations_part(message.references)
            if citations:
                parts.append(citations)
            # The client's own id, so "load earlier" dedupes against live
            # messages; legacy rows without one fall back to the DB id.
            bucket = getattr(message, "bucket", None)
            client_id = (
                bucket.get(CLIENT_MESSAGE_ID_KEY) if isinstance(bucket, dict) else None
            )
            ui_id = (
                client_id
                if isinstance(client_id, str) and client_id
                else str(message.id)
            )
            ui = UIMessage(id=ui_id, role="user", parts=parts)
        elif role == "assistant":
            parts = []
            dump = dump_from_bucket(message)
            if dump is not None:
                parts = _assistant_parts_from_dump(dump) or []
            if not parts:
                text = strip_evidence_blocks(str(message.content or ""))
                parts = [TextUIPart(text=text, state="done")] if text else []
            citations = _citations_part(message.references)
            if citations:
                parts.append(citations)
            metadata = _interrupted_metadata(message)
            # An empty failed turn is still worth sending: its metadata is
            # what tells the client to render the error and offer a retry.
            if not parts and not metadata:
                continue
            ui = UIMessage(
                id=str(message.id),
                role="assistant",
                metadata=metadata,
                parts=parts,
            )
        else:
            continue
        out.append(ui.model_dump(by_alias=True, exclude_none=True))
    return out
