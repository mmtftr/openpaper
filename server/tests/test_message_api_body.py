"""The chat route's typed body must accept exactly what the AI SDK client
sends (DefaultChatTransport in PaperChatPanel), and the adapter must still get
its run input from the same raw bytes."""

from __future__ import annotations

import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import message_api
from app.auth.dependencies import get_required_user
from app.database.database import get_db
from app.schemas.user import CurrentUser

USER = CurrentUser(id=uuid.uuid4(), email="me@example.com")


@pytest.fixture
def client(monkeypatch):
    calls: list[dict] = []

    async def fake_run_paper_chat(**kwargs):
        calls.append(kwargs)
        yield 'data: {"type":"start"}\n\n'

    monkeypatch.setattr(message_api, "run_paper_chat", fake_run_paper_chat)
    app = FastAPI()
    app.include_router(message_api.message_router, prefix="/api/message")
    app.dependency_overrides[get_db] = lambda: None
    app.dependency_overrides[get_required_user] = lambda: USER
    return TestClient(app), calls


def _user_message(text: str = "hi") -> dict:
    return {
        "id": "msg-1",
        "role": "user",
        "parts": [{"type": "text", "text": text}],
    }


def test_submit_payload_streams(client):
    http, calls = client
    body = {
        "trigger": "submit-message",
        "id": "conv-1",
        "messages": [_user_message()],
        "paper_id": "p1",
        "conversation_id": "c1",
        "llm_provider": "codex_proxy",
        "model": "gpt-6-astra",
        "reasoning_effort": "high",
        "context_mode": "adaptive",
        "user_references": ["a selection"],
    }
    response = http.post("/api/message/chat/paper", json=body)
    assert response.status_code == 200, response.text
    assert response.headers["x-vercel-ai-ui-message-stream"] == "v1"
    assert '"type":"start"' in response.text
    [call] = calls
    assert call["paper_id"] == "p1"
    assert call["conversation_id"] == "c1"
    assert call["user_references"] == ["a selection"]
    assert call["run_input"].trigger == "submit-message"
    assert call["run_input"].messages[0].parts[0].text == "hi"


def test_regenerate_payload_streams(client):
    http, calls = client
    body = {
        "trigger": "regenerate-message",
        "id": "conv-1",
        "messageId": "msg-9",
        "messages": [_user_message()],
        "paper_id": "p1",
        "conversation_id": "c1",
    }
    response = http.post("/api/message/chat/paper", json=body)
    assert response.status_code == 200, response.text
    assert calls[0]["run_input"].message_id == "msg-9"


def test_missing_conversation_is_422(client):
    http, calls = client
    body = {
        "trigger": "submit-message",
        "id": "conv-1",
        "messages": [_user_message()],
        "paper_id": "p1",
        "conversation_id": None,
    }
    response = http.post("/api/message/chat/paper", json=body)
    assert response.status_code == 422
    assert calls == []


def test_stream_parts_route_is_schema_only(client):
    http, _ = client
    response = http.get("/api/message/stream-parts")
    assert response.status_code == 404
    assert response.json() == {"detail": "Schema-only route."}
