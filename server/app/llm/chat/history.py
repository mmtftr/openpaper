"""Conversation history: model replay and UI serialization.

Two independent consumers read the same `messages` rows:

- **The model** (`load_model_history`): chronological ModelMessages for the
  next agent turn. Recent turns replay their persisted `bucket.pai_messages`
  dump (tool calls + returns + final text — keeps the prompt prefix stable
  and the model informed); once a char budget is exhausted, older turns
  degrade to text-only. NEVER paginated — pagination is a UI concern.

  Replay is not verbatim: it is SANITIZED for the model about to be called
  (`spec`), because history is model-agnostic on disk but not on the wire.
  See `_replay_sanitizer` — images become a placeholder for a model without
  vision, Responses-API reasoning ids are cleared for a Chat Completions
  model, and rehydrated figures are charged against the char budget.

- **The client** (`serialize_ui_messages`): Vercel AI UIMessage dicts with
  the same parts the live stream produced — reasoning, tool calls with
  truncated outputs, step boundaries, text with evidence stripped, and a
  `data-citations` part from the row's `references` column. Legacy rows
  (no dump) degrade to text + citations. An unfinished turn carries
  `metadata.interrupted`, plus `metadata.errorText` when the run failed
  (never on a user stop). The raw `bucket` column never goes over the wire.
"""

from __future__ import annotations

import base64
import copy
import json
import logging
from typing import Any, Dict, List, Optional, Sequence

from app.database.models import Message
from app.helpers.s3 import s3_service
from app.llm.chat.citations import strip_evidence_blocks
from app.llm.chat.stream import EvidenceFilter, truncate_tool_output
from pydantic_ai.messages import (
    ModelMessage,
    ModelMessagesTypeAdapter,
    ModelRequest,
    ModelResponse,
    TextPart,
    UserPromptPart,
)
from pydantic_ai.ui.vercel_ai import VercelAIAdapter
from pydantic_ai.ui.vercel_ai.request_types import (
    DataUIPart,
    StepStartUIPart,
    TextUIPart,
    ToolOutputAvailablePart,
    UIMessage,
    UIMessagePart,
)

logger = logging.getLogger(__name__)

BUCKET_DUMP_KEY = "pai_messages"
BUCKET_VERSION_KEY = "pai_v"
BUCKET_VERSION = 1
# On user rows: the exact prompt text sent to the model when it differs
# from the display content (e.g. the reference-citation block is appended).
MODEL_PROMPT_KEY = "model_prompt"
# On assistant rows: the turn ended without a completed answer (client
# disconnect, user stop, or a mid-run provider failure). `BUCKET_ERROR_KEY`
# is present only in the failure case: `{"message": "..."}`.
BUCKET_INTERRUPTED_KEY = "interrupted"
BUCKET_ERROR_KEY = "error"

# Char budget for replaying full turn dumps into the next model call
# (~60k tokens at 4 chars/token). Beyond it, turns degrade to text-only.
MODEL_HISTORY_CHAR_BUDGET = 240_000

# Marker prefix on `BinaryImage.identifier` for figures we can rehydrate
# from S3. Bytes are dropped on persistence and re-fetched by S3 key on
# replay — keeps the assistant row small while keeping the replayed binary
# content byte-identical to the original tool return.
FIGURE_ID_PREFIX = "openpaper-figure:"

# Substituted for a replayed image when the selected model has no vision.
# Same contract as `get_figure`'s vision gate in chat/paper.py: the model
# still learns a figure was fetched, it just never receives the pixels.
IMAGE_PLACEHOLDER = (
    "[figure image omitted: current model does not support image inputs]"
)

# Prefix of OpenAI Responses-API reasoning item ids. See
# `strip_responses_reasoning_ids`.
RESPONSES_REASONING_ID_PREFIX = "rs_"

# Budget charge for a figure whose stored size can't be read (~45KB PNG as
# base64). Only a fallback: the real size comes from S3 metadata.
FIGURE_REPLAY_CHAR_ESTIMATE = 60_000


# -- figure byte stripping / rehydration ---------------------------------


def _walk_binary_image_dicts(node: Any):
    """Yield every dict in `node` that looks like a serialized BinaryImage
    pointing at an `openpaper-figure:` S3 key."""
    if isinstance(node, dict):
        identifier = node.get("identifier")
        if (
            node.get("kind") == "binary"
            and isinstance(identifier, str)
            and identifier.startswith(FIGURE_ID_PREFIX)
        ):
            yield node
        for value in node.values():
            yield from _walk_binary_image_dicts(value)
    elif isinstance(node, list):
        for item in node:
            yield from _walk_binary_image_dicts(item)


def strip_instructions(dump: Any) -> Any:
    """Null out `instructions` on dumped ModelRequests before save.

    Every ModelRequest in a turn carries the full system prompt (which in
    `full` context mode embeds the whole paper) — 70%+ of the dump's bytes,
    repeated per request. It is never read back on replay: pydantic-ai
    re-injects the *current* run's instructions, so persisting them only
    bloats the row and burns the model-history char budget.
    """
    if isinstance(dump, list):
        for entry in dump:
            if isinstance(entry, dict) and entry.get("kind") == "request":
                entry["instructions"] = None
    return dump


def strip_figure_bytes(dump: Any) -> Any:
    """Replace figure image bytes with empty placeholders before save."""
    for entry in _walk_binary_image_dicts(dump):
        entry["data"] = ""
    return dump


# Cap on a persisted `run_python` tool return. A repo-heavy turn can make a
# dozen sandbox calls of ~4k chars each; replayed verbatim, two such turns
# would exhaust MODEL_HISTORY_CHAR_BUDGET and degrade the whole conversation
# to text-only. The model keeps the file list and a readable head of the
# output — enough to know what it already looked at.
SANDBOX_REPLAY_CHAR_CAP = 1500
SANDBOX_TOOL_NAME = "run_python"


def _walk_part_dicts(node: Any, part_kind: str):
    """Yield every serialized message part of `part_kind`."""
    if isinstance(node, dict):
        if node.get("part_kind") == part_kind:
            yield node
        for value in node.values():
            yield from _walk_part_dicts(value, part_kind)
    elif isinstance(node, list):
        for item in node:
            yield from _walk_part_dicts(item, part_kind)


def strip_responses_reasoning_ids(dump: Any) -> Any:
    """Neutralize Responses-API reasoning item ids in a replayed dump.

    A gpt-5.x turn (Responses API) persists `ThinkingPart(id='rs_…',
    provider_name='openai')`. Replaying that to a **Chat Completions** model
    makes pydantic-ai's `_map_response_thinking_part` take its "field" branch
    (id truthy, not 'content', provider matches) and emit the id as a
    TOP-LEVEL key on the assistant message — `{"rs_00…": "…"}`. Fireworks-
    hosted Azure deployments reject that outright:
    `Extra inputs are not permitted, field: 'messages[2].rs_00…'`, so a
    single gpt-5.x tool turn used to poison every later DeepSeek/Kimi turn in
    the conversation.

    Clearing the id drops it into "tags" mode instead, which inlines the
    thinking as `<think>…</think>` text — supported everywhere. Only the
    replay copy is touched; the stored row keeps its ids for the models that
    can use them.
    """
    for entry in _walk_part_dicts(dump, "thinking"):
        identifier = entry.get("id")
        if isinstance(identifier, str) and identifier.startswith(
            RESPONSES_REASONING_ID_PREFIX
        ):
            entry["id"] = None
    return dump


def _walk_tool_return_dicts(node: Any, tool_name: str):
    """Yield every serialized ToolReturnPart for `tool_name`."""
    if isinstance(node, dict):
        if (
            node.get("part_kind") == "tool-return"
            and node.get("tool_name") == tool_name
        ):
            yield node
        for value in node.values():
            yield from _walk_tool_return_dicts(value, tool_name)
    elif isinstance(node, list):
        for item in node:
            yield from _walk_tool_return_dicts(item, tool_name)


def strip_sandbox_outputs(dump: Any, cap: int = SANDBOX_REPLAY_CHAR_CAP) -> Any:
    """Cap `run_python` tool returns in a dump before persisting it.

    The cap applies to the WHOLE serialized return, not just `output`: the
    file-chip list also costs bytes, and the budget this protects is the
    replay budget for the entire structure.
    """
    for entry in _walk_tool_return_dicts(dump, SANDBOX_TOOL_NAME):
        content = entry.get("content")
        if isinstance(content, dict):
            files = content.get("files")
            files_cost = len(json.dumps(files, default=str)) if files else 0
            output = content.get("output")
            budget = max(200, cap - files_cost)
            if isinstance(output, str) and len(output) > budget:
                content["output"] = (
                    output[:budget] + "\n…[output trimmed from history]"
                )
        elif isinstance(content, str) and len(content) > cap:
            entry["content"] = content[:cap] + "\n…[output trimmed from history]"
    return dump


def _is_image_node(node: Any) -> bool:
    """True for a serialized image content item of any flavor.

    Broader than `_walk_binary_image_dicts` on purpose: rehydration only
    cares about OUR figures, but a vision-less model 400s on *any* image in
    the request, whatever put it there.
    """
    if not isinstance(node, dict):
        return False
    kind = node.get("kind")
    if kind == "image-url":
        return True
    if kind == "uploaded-file":
        # A provider-side file reference is just as fatal as inline bytes.
        return str(node.get("media_type") or "").startswith("image/")
    if kind != "binary":
        return False
    if str(node.get("media_type") or "").startswith("image/"):
        return True
    identifier = node.get("identifier")
    return isinstance(identifier, str) and identifier.startswith(FIGURE_ID_PREFIX)


def _strip_image_file_part(part: Any, placeholder: str) -> Any:
    """Turn a response `FilePart` holding an image into a `TextPart`.

    `FilePart.content` is a REQUIRED file object, so substituting a string
    there fails `ModelMessagesTypeAdapter` validation — which is silent:
    `load_model_history` catches it and degrades the whole turn to plain
    text, losing its tool calls.
    """
    if (
        isinstance(part, dict)
        and part.get("part_kind") == "file"
        and _is_image_node(part.get("content"))
    ):
        return {"content": placeholder, "part_kind": "text"}
    return part


def strip_replayed_images(dump: Any, placeholder: str = IMAGE_PLACEHOLDER) -> Any:
    """Replace every image in a dump with a text placeholder, in place.

    Used instead of `rehydrate_figure_bytes` when the selected model has no
    vision: a figure fetched by an earlier turn (possibly by a different,
    vision-capable model) would otherwise be spliced back into the request
    and hard-400 the provider — permanently, for every later turn in that
    conversation. The placeholder keeps the model aware that a figure was
    fetched there, mirroring what `get_figure` itself returns to a
    vision-less model (metadata, no image).
    """
    if isinstance(dump, dict):
        for key, value in dump.items():
            if key == "parts" and isinstance(value, list):
                # Substituting inside a part is not always legal, so rewrite
                # the part itself where the schema demands it.
                dump[key] = [
                    _strip_image_file_part(part, placeholder) for part in value
                ]
                strip_replayed_images(dump[key], placeholder)
            elif _is_image_node(value):
                dump[key] = placeholder
            else:
                strip_replayed_images(value, placeholder)
    elif isinstance(dump, list):
        for index, item in enumerate(dump):
            if _is_image_node(item):
                dump[index] = placeholder
            else:
                strip_replayed_images(item, placeholder)
    return dump


def _figure_size_chars(s3_key: str) -> int:
    """Replay cost of one stored figure, in characters of base64."""
    try:
        size_kb = s3_service.get_file_size_in_kb(s3_key)
    except Exception:  # pragma: no cover - defensive
        size_kb = None
    if not size_kb:
        return FIGURE_REPLAY_CHAR_ESTIMATE
    return int(size_kb * 1024 * 4 / 3)


def _figure_replay_cost(dump: Any, sizes: Dict[str, int]) -> int:
    """Characters the figures in `dump` add once rehydrated.

    `sizes` memoizes per S3 key across the whole history walk (the same
    figure is often fetched in several turns).
    """
    total = 0
    for entry in _walk_binary_image_dicts(dump):
        if entry.get("data"):
            continue  # inline already — `json.dumps` counted it
        key = entry["identifier"][len(FIGURE_ID_PREFIX) :]
        if key not in sizes:
            sizes[key] = _figure_size_chars(key)
        total += sizes[key]
    return total


def rehydrate_figure_bytes(dump: Any) -> Any:
    """Re-fetch figure bytes from S3 in place after load."""
    for entry in _walk_binary_image_dicts(dump):
        if entry.get("data"):
            continue
        s3_key = entry["identifier"][len(FIGURE_ID_PREFIX) :]
        try:
            image_bytes = s3_service.get_object_bytes(s3_key)
        except Exception as exc:
            logger.warning("Failed to rehydrate figure %s for replay: %s", s3_key, exc)
            continue
        # `mode='json'` dumps bytes as base64 strings; match that format so
        # `validate_json` round-trips back to the original bytes object.
        entry["data"] = base64.b64encode(image_bytes).decode("ascii")
    return dump


def _dump_from_bucket(message: Message) -> Optional[Any]:
    bucket = getattr(message, "bucket", None) or {}
    if not isinstance(bucket, dict):
        return None
    return bucket.get(BUCKET_DUMP_KEY)


# -- model history -------------------------------------------------------


def _replay_sanitizer(spec: Optional[Any]):
    """Return `(prepare_dump, supports_vision)` for the TARGET model.

    History is model-agnostic on disk but not on the wire: what one model
    produced can be invalid input for the next one the user picks. Both
    known cases are keyed on the model about to be called, never on the one
    that produced the turn:

    - no vision -> images become a text placeholder (and are not fetched);
    - Chat Completions -> Responses-API reasoning ids are cleared.

    `spec=None` means "capabilities unknown": keep the legacy behavior.
    """
    supports_vision = True if spec is None else bool(
        getattr(spec, "supports_vision", True)
    )
    target_api = getattr(spec, "api", None) if spec is not None else None

    def prepare(dump: Any) -> Any:
        dump = (
            rehydrate_figure_bytes(dump)
            if supports_vision
            # No S3 round-trip either: the bytes would only be thrown away
            # (and then rejected by the provider).
            else strip_replayed_images(dump)
        )
        if target_api == "chat":
            dump = strip_responses_reasoning_ids(dump)
        return dump

    return prepare, supports_vision


def load_model_history(
    rows: Sequence[Message],
    char_budget: int = MODEL_HISTORY_CHAR_BUDGET,
    *,
    spec: Optional[Any] = None,
) -> List[ModelMessage]:
    """Build the ModelMessage history for the next agent turn.

    `rows` must be the FULL conversation in chronological order. Newest
    turns replay their dumps while the budget allows; older turns (and rows
    without a dump) fall back to plain user/assistant text.

    `spec` is the CURRENT turn's `ModelSpec` (anything exposing
    `supports_vision` / `api`), NOT the capability of whichever model
    produced the history — see `_replay_sanitizer`. Passing `None` keeps the
    pre-capability behavior (rehydrate everything, touch nothing).
    """
    prepare_dump, supports_vision = _replay_sanitizer(spec)
    # Decide per row what it will cost, newest first, so recency wins.
    # Assistant rows with a dump cost their serialized size; everything else
    # costs its text length (degraded/legacy rows consume budget too).
    replay_ids: set = set()
    remaining = char_budget
    replay_open = True
    figure_sizes: Dict[str, int] = {}
    for message in reversed(rows):
        dump = _dump_from_bucket(message) if message.role == "assistant" else None
        if dump is not None:
            try:
                cost = len(json.dumps(dump))
            except (TypeError, ValueError):
                continue
            # Figures are stored with `data=""`, so the serialized size hides
            # the base64 PNG that rehydration splices back in. Charge it, or
            # a couple of figures silently blow the budget and the provider
            # rejects the request on context length.
            if supports_vision:
                cost += _figure_replay_cost(dump, figure_sizes)
            if replay_open and cost <= remaining:
                replay_ids.add(message.id)
                remaining -= cost
            else:
                # Once one dump doesn't fit, degrade everything older too —
                # replaying turn N-2 in full detail while N-1 is text-only
                # would give the model an incoherent view of the thread.
                replay_open = False
                remaining -= len(str(getattr(message, "content", "") or ""))
        else:
            remaining -= len(str(getattr(message, "content", "") or ""))

    history: List[ModelMessage] = []
    pending_user_text: Optional[str] = None

    def _flush_pending() -> None:
        nonlocal pending_user_text
        if pending_user_text is not None:
            history.append(
                ModelRequest(parts=[UserPromptPart(content=pending_user_text)])
            )
            pending_user_text = None

    for message in rows:
        dump = _dump_from_bucket(message)
        if message.role == "assistant" and dump is not None and message.id in replay_ids:
            try:
                hydrated = prepare_dump(copy.deepcopy(dump))
                # Round-trip through JSON so base64 `data` fields decode back
                # to bytes — `validate_python` rejects the base64 string.
                turn_messages = ModelMessagesTypeAdapter.validate_json(
                    json.dumps(hydrated)
                )
                # The runtime feeds the user prompt to the agent as trailing
                # message_history, so `new_messages()` — and therefore the
                # dump — does NOT contain it (verified against pydantic-ai
                # 1.89.1). Flush the DB user row ahead of the responses,
                # unless this particular dump does carry its own leading
                # user request (defensive against format changes).
                if _dump_leads_with_user_prompt(turn_messages):
                    pending_user_text = None
                else:
                    _flush_pending()
                history.extend(turn_messages)
                continue
            except Exception as exc:
                logger.warning(
                    "Failed to deserialize pai_messages on message %s: %s",
                    getattr(message, "id", "?"),
                    exc,
                )
                # Fall through to text fallback for this turn.

        content = str(getattr(message, "content", "") or "")
        if not content:
            continue
        if message.role == "user":
            _flush_pending()
            # Prefer the exact prompt the model saw (includes the user's
            # reference-citation block) when the runtime stored it.
            bucket = getattr(message, "bucket", None) or {}
            prompt = (
                bucket.get(MODEL_PROMPT_KEY) if isinstance(bucket, dict) else None
            )
            pending_user_text = str(prompt) if prompt else content
        elif message.role == "assistant":
            _flush_pending()
            history.append(ModelResponse(parts=[TextPart(content=content)]))

    _flush_pending()
    return history


def _dump_leads_with_user_prompt(messages: List[ModelMessage]) -> bool:
    if not messages:
        return False
    first = messages[0]
    if not isinstance(first, ModelRequest):
        return False
    return any(isinstance(part, UserPromptPart) for part in first.parts)


# -- UI serialization ----------------------------------------------------


def _citations_part(references: Any) -> Optional[DataUIPart]:
    if not references or not isinstance(references, dict):
        return None
    citations = references.get("citations")
    if not citations:
        return None
    return DataUIPart(
        type="data-citations", id="citations", data={"citations": citations}
    )


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
    metadata: Dict[str, Any] = {"interrupted": True}
    error = bucket.get(BUCKET_ERROR_KEY)
    if isinstance(error, dict):
        text = error.get("message")
        if isinstance(text, str) and text:
            metadata["errorText"] = text
    elif isinstance(error, str) and error:
        metadata["errorText"] = error
    return metadata


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
            ui = UIMessage(id=str(message.id), role="user", parts=parts)
        elif role == "assistant":
            parts = []
            dump = _dump_from_bucket(message)
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
