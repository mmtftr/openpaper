"""Typed highlight positions (`app/schemas/highlight.py`).

Every `position` shape found in the database has to validate and come back
out byte-for-byte the same JSON, or existing highlights would break the list
endpoint or be rewritten on the next PATCH.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import ValidationError

from app.api.webhook_api import PDFProcessingResult
from app.schemas.highlight import HighlightResponse, ScaledPosition


def _rect(x1, y1, x2, y2, width, height, page) -> dict[str, Any]:
    return {
        "x1": x1,
        "y1": y1,
        "x2": x2,
        "y2": y2,
        "width": width,
        "height": height,
        "pageNumber": page,
    }


# Drawn in the reader (client `scaledPositionFromAnchor`): integer page
# dimensions in CSS pixels, float coordinates.
USER_POSITION = {
    "rects": [
        _rect(95.9609375, 148.3671875, 720.2318725585938, 174.8671875, 816, 1056, 1),
        _rect(161.3984375, 177.625, 654.492431640625, 204.125, 816, 1056, 1),
    ],
    "boundingRect": _rect(95.9609375, 148.3671875, 720.2318725585938, 204.125, 816, 1056, 1),
}

# Anchored by the jobs service (`jobs/src/highlight_anchor.py`): PDF points,
# float page dimensions, never `usePdfCoordinates`.
AI_POSITION = {
    "rects": [
        _rect(369.1292419433594, 487.31536865234375, 503.9973449707031, 496.221923828125, 612.0, 792.0, 6),
        _rect(108.0, 498.224365234375, 504.3529968261719, 507.13092041015625, 612.0, 792.0, 6),
    ],
    "boundingRect": _rect(108.0, 487.31536865234375, 504.3529968261719, 507.13092041015625, 612.0, 792.0, 6),
}

# A single-rect AI highlight (the most common multi-line count is 2-3, but
# 1 occurs).
AI_SINGLE_RECT_POSITION = {
    "rects": [_rect(72.0, 100.5, 300.25, 110.75, 612.0, 792.0, 1)],
    "boundingRect": _rect(72.0, 100.5, 300.25, 110.75, 612.0, 792.0, 1),
}

# The client type allows PDF-native coordinates; the reader honours it.
PDF_COORDS_POSITION = {**AI_SINGLE_RECT_POSITION, "usePdfCoordinates": True}

STORED_SHAPES = [USER_POSITION, AI_POSITION, AI_SINGLE_RECT_POSITION, PDF_COORDS_POSITION]


@pytest.mark.parametrize("stored", STORED_SHAPES)
def test_stored_positions_round_trip_exactly(stored):
    position = ScaledPosition.model_validate(stored)
    assert position.model_dump(mode="json") == stored
    # And through real JSON, as FastAPI serializes it.
    assert json.loads(position.model_dump_json()) == stored
    assert position.to_json() == stored


def test_use_pdf_coordinates_is_absent_unless_set():
    position = ScaledPosition.model_validate(USER_POSITION)
    assert position.usePdfCoordinates is None
    assert "usePdfCoordinates" not in position.model_dump(mode="json")


def test_to_json_drops_an_explicit_null_flag():
    """The client PATCHes back the position it fetched; a null flag must not
    get written into the row."""
    position = ScaledPosition.model_validate({**USER_POSITION, "usePdfCoordinates": None})
    assert position.to_json() == USER_POSITION


@pytest.mark.parametrize(
    "broken",
    [
        {"rects": USER_POSITION["rects"]},  # no boundingRect
        {"boundingRect": USER_POSITION["boundingRect"]},  # no rects
        {
            **USER_POSITION,
            "boundingRect": {k: v for k, v in USER_POSITION["boundingRect"].items() if k != "pageNumber"},
        },
        {**USER_POSITION, "rects": [{"x1": "left"}]},
    ],
)
def test_malformed_positions_are_rejected(broken):
    with pytest.raises(ValidationError):
        ScaledPosition.model_validate(broken)


def _highlight_row(position: Any, **overrides) -> SimpleNamespace:
    now = datetime(2026, 9, 24, 12, 0, tzinfo=timezone.utc)
    fields = dict(
        id=uuid.uuid4(),
        paper_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        raw_text="quoted text",
        type=None,
        start_offset=None,
        end_offset=None,
        page_number=1,
        position=position,
        role="user",
        color="blue",
        created_at=now,
        updated_at=now,
    )
    fields.update(overrides)
    return SimpleNamespace(**fields)


@pytest.mark.parametrize("stored", STORED_SHAPES + [None])
def test_highlight_response_serves_the_stored_position_unchanged(stored):
    row = _highlight_row(stored)
    body = json.loads(HighlightResponse.model_validate(row).model_dump_json())
    assert body["position"] == stored


def test_ai_highlight_row_validates():
    row = _highlight_row(
        AI_POSITION,
        role="assistant",
        type="evidence",
        start_offset=10,
        end_offset=40,
        page_number=6,
    )
    body = json.loads(HighlightResponse.model_validate(row).model_dump_json())
    assert body["type"] == "evidence"
    assert body["role"] == "assistant"
    assert body["id"] == str(row.id)


def test_webhook_anchor_payload_validates():
    """The jobs service's `anchor_ai_highlights` output, as it arrives on the
    processing webhook (one entry per highlight, None where unanchored)."""
    result = PDFProcessingResult.model_validate(
        {
            "success": True,
            "job_id": "j1",
            "ai_highlight_anchors": [
                {"page_number": 6, "position": AI_POSITION},
                None,
                {"page_number": 1, "position": AI_SINGLE_RECT_POSITION},
            ],
        }
    )
    anchors = result.ai_highlight_anchors
    assert anchors is not None
    assert anchors[1] is None
    assert anchors[0] is not None and anchors[0].position.to_json() == AI_POSITION
