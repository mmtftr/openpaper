"""Replay sanitation: make a stored turn valid input for the NEXT model.

History is model-agnostic on disk but not on the wire: what one model
produced can be invalid input for the next one the user picks. Every rule
here is keyed on the model about to be called, never on the one that
produced the turn, and only ever touches the replay copy:

- no vision -> images become a text placeholder (and are not fetched);
- vision -> stored figures are rehydrated from S3;
- Chat Completions -> Responses-API reasoning ids are cleared.
"""

from __future__ import annotations

from typing import Any, Callable, Optional, Tuple

from app.llm.chat.history.figures import FIGURE_ID_PREFIX, rehydrate_figure_bytes

# Substituted for a replayed image when the selected model has no vision.
# Same contract as `get_figure`'s vision gate in chat/paper.py: the model
# still learns a figure was fetched, it just never receives the pixels.
IMAGE_PLACEHOLDER = (
    "[figure image omitted: current model does not support image inputs]"
)

# Prefix of OpenAI Responses-API reasoning item ids. See
# `strip_responses_reasoning_ids`.
RESPONSES_REASONING_ID_PREFIX = "rs_"


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


def _is_image_node(node: Any) -> bool:
    """True for a serialized image content item of any flavor.

    Broader than `walk_binary_image_dicts` on purpose: rehydration only
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


def replay_sanitizer(spec: Optional[Any]) -> Tuple[Callable[[Any], Any], bool]:
    """Return `(prepare_dump, supports_vision)` for the TARGET model.

    `spec=None` means "capabilities unknown": keep the legacy behavior.
    """
    supports_vision = (
        True if spec is None else bool(getattr(spec, "supports_vision", True))
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
