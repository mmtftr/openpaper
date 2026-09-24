"""Typed custom stream parts (`app/schemas/chat_stream.py`).

The models must describe exactly what goes on the wire today: the builders
may not add, drop or null out any key, and every chunk the server emits must
validate against `OpenPaperDataPart`.
"""

from __future__ import annotations

import json
import uuid
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError
from pydantic_ai.ui.vercel_ai.response_types import DataChunk

from app.api.conversation_api import ConversationPage
from app.llm.chat.evidence import extract_citations
from app.llm.chat.history import serialize_ui_messages
from app.llm.retrying_model import RetryStatus, retry_status_payload
from app.schemas.chat_stream import (
    CITATIONS_PART_ID,
    CITATIONS_PART_TYPE,
    RETRY_STATUS_PART_TYPE,
    ChatMessageMetadata,
    OpenPaperDataPart,
    citations_data,
    message_metadata,
)

# One of each citation shape stored in `messages.references`.
STORED_CITATIONS = [
    {"key": 1, "reference": "plain quote"},
    {"key": 2, "paper_id": None, "reference": "legacy explicit null"},
    {"key": 3, "page": 1, "reference": "pinned to a page"},
    {"key": 4, "page": 1, "reference": "rewritten", "matched_via": "normalizer"},
    {
        "key": 5,
        "page": 18,
        "paper_id": "d4cb7e17-200d-45c7-ab9b-9a9b50c517c3",
        "reference": "Table S1. Datasets",
        "matched_via": "normalizer",
    },
    {
        "key": 6,
        "file": "model.py",
        "verified": False,
        "reference": "def forward(self, x):",
        "github_url": None,
    },
    {
        "key": 7,
        "file": "pipeline/select_direction.py",
        "start_line": 17,
        "end_line": 31,
        "verified": True,
        "reference": "def refusal_score(",
        "github_url": "https://github.com/o/r/blob/abc/pipeline/select_direction.py#L17-L31",
        "matched_via": "exact",
    },
]


def _wire(chunk: DataChunk) -> dict:
    """What the client receives for a chunk (the adapter's own encoding)."""
    return json.loads(chunk.encode(sdk_version=6))


# -- data-citations -----------------------------------------------------------


def test_citations_payload_round_trips_every_stored_shape():
    assert citations_data(STORED_CITATIONS) == {"citations": STORED_CITATIONS}


def test_citations_from_the_evidence_parser_validate():
    text = (
        "Answer.\n---EVIDENCE---\n"
        "@cite[1|page=3]\nquote one\n"
        "@cite[2|page=7|paper_id=abc-123]\nquote two\n"
        "@cite[3|file=/repo/src/model.py|lines=42-57]\ncode\n"
        "@cite[4]\nlegacy\n"
        "---END-EVIDENCE---"
    )
    citations = extract_citations(text)
    assert citations_data(citations) == {"citations": citations}


def test_live_citations_chunk_matches_the_part_model():
    chunk = DataChunk(
        type=CITATIONS_PART_TYPE,
        id=CITATIONS_PART_ID,
        data=citations_data(STORED_CITATIONS),
    )
    wire = _wire(chunk)
    part = OpenPaperDataPart.model_validate(wire).root
    assert part.type == "data-citations"
    assert [c.key for c in part.data.citations] == [c["key"] for c in STORED_CITATIONS]


def test_citation_without_reference_is_rejected():
    with pytest.raises(ValidationError):
        citations_data([{"key": 1}])


def test_history_citations_part_matches_the_part_model():
    row = SimpleNamespace(
        id=str(uuid.uuid4()),
        role="assistant",
        content="answer",
        bucket=None,
        references={"citations": STORED_CITATIONS},
    )
    [message] = serialize_ui_messages([row])
    data_parts = [p for p in message["parts"] if p["type"].startswith("data-")]
    assert len(data_parts) == 1
    part = OpenPaperDataPart.model_validate(data_parts[0]).root
    assert part.type == "data-citations"
    assert data_parts[0]["data"] == {"citations": STORED_CITATIONS}


def test_history_serves_off_shape_legacy_citations_unvalidated():
    """A stored row that no longer fits the model must not break the page."""
    odd = [{"key": "one", "reference": "x"}]
    row = SimpleNamespace(
        id=str(uuid.uuid4()),
        role="user",
        content="q",
        bucket=None,
        references={"citations": odd},
    )
    [message] = serialize_ui_messages([row])
    assert message["parts"][-1]["data"] == {"citations": odd}


# -- data-retry-status --------------------------------------------------------


@pytest.mark.parametrize(
    "status,expected",
    [
        (
            RetryStatus(
                state="retrying", attempt=2, max_attempts=3, delay_ms=2000, error="429"
            ),
            {
                "state": "retrying",
                "attempt": 2,
                "maxAttempts": 3,
                "delayMs": 2000,
                "error": "429",
            },
        ),
        (
            RetryStatus(state="retrying", attempt=2, max_attempts=3, delay_ms=2000),
            {"state": "retrying", "attempt": 2, "maxAttempts": 3, "delayMs": 2000},
        ),
        # Unknown counters stay explicit nulls, exactly as before.
        (
            RetryStatus(state="retrying", error=""),
            {"state": "retrying", "attempt": None, "maxAttempts": 3, "delayMs": None},
        ),
        (RetryStatus(state="recovered", attempt=3), {"state": "recovered"}),
    ],
)
def test_retry_status_payload_wire_shape(status, expected):
    assert retry_status_payload(status) == expected


def test_live_retry_chunk_matches_the_part_model():
    chunk = DataChunk(
        type=RETRY_STATUS_PART_TYPE,
        data=retry_status_payload(
            RetryStatus(state="retrying", attempt=2, max_attempts=3, delay_ms=500)
        ),
        transient=True,
    )
    wire = _wire(chunk)
    assert wire["transient"] is True
    part = OpenPaperDataPart.model_validate(wire).root
    assert part.type == "data-retry-status"
    assert part.data.maxAttempts == 3


def test_unknown_data_part_type_is_rejected():
    with pytest.raises(ValidationError):
        OpenPaperDataPart.model_validate({"type": "data-other", "data": {}})


# -- message metadata ---------------------------------------------------------


def test_message_metadata_shapes():
    assert message_metadata(interrupted=True) == {"interrupted": True}
    assert message_metadata(interrupted=True, error_text="boom") == {
        "interrupted": True,
        "errorText": "boom",
    }
    assert message_metadata(interrupted=True, error_text="") == {"interrupted": True}


def test_history_metadata_matches_the_model():
    row = SimpleNamespace(
        id=str(uuid.uuid4()),
        role="assistant",
        content="partial",
        bucket={"interrupted": True, "error": {"message": "provider died"}},
        references=None,
    )
    [message] = serialize_ui_messages([row])
    assert message["metadata"] == {"interrupted": True, "errorText": "provider died"}
    ChatMessageMetadata.model_validate(message["metadata"])


# -- conversation page wire shape --------------------------------------------


def test_conversation_page_serves_serialized_messages_unchanged():
    """GET /api/conversation/{id} re-validates `serialize_ui_messages` output
    through `UIMessage`; the JSON the client gets must be identical (camelCase,
    no nulls for unset optionals)."""
    from tests.test_chat_runtime import _bucketed, _row, _tool_turn_dump

    rows = [
        _row("user", "q", references={"citations": [{"key": 1, "reference": "x"}]}),
        _row(
            "assistant",
            "a",
            bucket=_bucketed(_tool_turn_dump("q", "a")),
            references={"citations": STORED_CITATIONS},
        ),
        _row("assistant", "partial", bucket={"interrupted": True, "error": "died"}),
    ]
    serialized = serialize_ui_messages(rows)
    conversation_id = uuid.uuid4()

    app = FastAPI()

    @app.get("/page")
    def page() -> ConversationPage:
        return ConversationPage.model_validate(
            {"id": conversation_id, "title": None, "messages": serialized}
        )

    body = TestClient(app).get("/page").json()
    assert body == {
        "id": str(conversation_id),
        "title": None,
        "messages": json.loads(json.dumps(serialized)),
    }
