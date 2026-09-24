"""Figure bytes in persisted dumps: dropped on save, rehydrated on replay.

`get_figure` returns the rendered figure as a `BinaryImage` whose
identifier is `openpaper-figure:<s3 key>`. The bytes are blanked before the
turn is stored (keeps the row small) and re-fetched by S3 key when the turn
is replayed, so the replayed binary content is byte-identical to the
original tool return.
"""

from __future__ import annotations

import base64
import logging
from typing import Any, Dict

from app.helpers.s3 import s3_service

logger = logging.getLogger(__name__)

# Marker prefix on `BinaryImage.identifier` for figures we can rehydrate
# from S3. Bytes are dropped on persistence and re-fetched by S3 key on
# replay — keeps the assistant row small while keeping the replayed binary
# content byte-identical to the original tool return.
FIGURE_ID_PREFIX = "openpaper-figure:"

# Budget charge for a figure whose stored size can't be read (~45KB PNG as
# base64). Only a fallback: the real size comes from S3 metadata.
FIGURE_REPLAY_CHAR_ESTIMATE = 60_000


def walk_binary_image_dicts(node: Any):
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
            yield from walk_binary_image_dicts(value)
    elif isinstance(node, list):
        for item in node:
            yield from walk_binary_image_dicts(item)


def strip_figure_bytes(dump: Any) -> Any:
    """Replace figure image bytes with empty placeholders before save."""
    for entry in walk_binary_image_dicts(dump):
        entry["data"] = ""
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


def figure_replay_cost(dump: Any, sizes: Dict[str, int]) -> int:
    """Characters the figures in `dump` add once rehydrated.

    `sizes` memoizes per S3 key across the whole history walk (the same
    figure is often fetched in several turns).
    """
    total = 0
    for entry in walk_binary_image_dicts(dump):
        if entry.get("data"):
            continue  # inline already — `json.dumps` counted it
        key = entry["identifier"][len(FIGURE_ID_PREFIX) :]
        if key not in sizes:
            sizes[key] = _figure_size_chars(key)
        total += sizes[key]
    return total


def rehydrate_figure_bytes(dump: Any) -> Any:
    """Re-fetch figure bytes from S3 in place after load."""
    for entry in walk_binary_image_dicts(dump):
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
