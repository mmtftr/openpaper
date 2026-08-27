"""Smoke-test the real paper-chat streaming path inside the server container.

Run from the Docker Compose project directory:
    docker compose exec server python -m app.scripts.smoke_chat

This deliberately calls ``message_api.chat_message_stream`` and consumes its
``StreamingResponse`` body.  It therefore covers the production chain:

    chat_message_stream -> operations.chat_with_paper_agentic
    -> run_pydantic_paper_agent -> Agent.run_stream_events
    -> message_api._stream_chat_chunks

Only persistence and fixture lookups are replaced in-process.  The configured
LLM provider, model construction, pydantic-ai agent, and streaming stack are
real.  In particular, this fails with the pydantic-ai 2.x
``run_stream_events`` context-manager API mismatch that previously surfaced as
"Something went wrong while answering".
"""

from __future__ import annotations

import asyncio
import importlib.metadata
import json
import os
import sys
import uuid
from contextlib import ExitStack
from typing import Any
from unittest.mock import patch

from app.api import message_api
from app.api.message_api import ChatMessageRequest
from app.database.models import Paper
from app.llm import paper_agentic_operations, paper_pydantic_agent
from app.llm.provider import LLMProvider
from app.schemas.message import ResponseStyle
from app.schemas.user import CurrentUser


class SmokeFailure(RuntimeError):
    """Raised when the production stream does not return a usable answer."""


def _fixture_paper(paper_id: uuid.UUID, user_id: uuid.UUID) -> Paper:
    page_text = (
        "# Runtime smoke fixture\n\n"
        "This tiny paper exists only to verify OpenPaper's live chat stream."
    )
    return Paper(
        id=paper_id,
        user_id=user_id,
        file_url="smoke://paper.pdf",
        title="Runtime smoke fixture",
        authors=["OpenPaper"],
        abstract="A deterministic fixture for the paper-chat smoke test.",
        parser="mistral",
        page_count=1,
        raw_content=page_text,
        ocr={
            "pages": [
                {
                    "index": 0,
                    "markdown": page_text,
                    "pymupdf_text": page_text,
                }
            ],
            "figures": [],
        },
    )


def _parse_sse_events(stream_text: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for record in stream_text.split("\n\n"):
        for line in record.splitlines():
            if not line.startswith("data:"):
                continue
            payload = line.removeprefix("data:").strip()
            if not payload or payload == "[DONE]":
                continue
            try:
                event = json.loads(payload)
            except json.JSONDecodeError as exc:
                raise SmokeFailure(f"invalid SSE JSON payload: {payload!r}") from exc
            if not isinstance(event, dict):
                raise SmokeFailure(f"unexpected SSE payload: {event!r}")
            events.append(event)
    return events


async def _consume_response(response: Any, timeout_seconds: float) -> str:
    chunks: list[str] = []
    async with asyncio.timeout(timeout_seconds):
        async for chunk in response.body_iterator:
            if isinstance(chunk, bytes):
                chunks.append(chunk.decode("utf-8", errors="replace"))
            else:
                chunks.append(str(chunk))
    return "".join(chunks)


async def run_smoke() -> None:
    timeout_seconds = float(os.getenv("SMOKE_CHAT_TIMEOUT_SECONDS", "180"))
    paper_id = uuid.uuid4()
    conversation_id = uuid.uuid4()
    user_id = uuid.uuid4()
    paper = _fixture_paper(paper_id, user_id)
    current_user = CurrentUser(
        id=user_id,
        email="paper-chat-smoke@example.com",
        name="Paper chat smoke test",
        is_active=True,
    )
    request = ChatMessageRequest(
        paper_id=str(paper_id),
        conversation_id=str(conversation_id),
        user_query=(
            "This is a transport smoke test. Reply with a short greeting. "
            "Do not call tools, read or write docs, or include citations."
        ),
        style=ResponseStyle.CONCISE,
        context_mode="full",
        llm_provider=LLMProvider.OPENAI,
    )

    # Keep the test hermetic with respect to application data: the handler and
    # agent see realistic objects, while reads/writes never touch production
    # rows.  The LLM call itself is intentionally not mocked.
    with ExitStack() as patches:
        patches.enter_context(
            patch.object(paper_agentic_operations.paper_crud, "get", return_value=paper)
        )
        patches.enter_context(
            patch.object(
                paper_agentic_operations.paper_crud,
                "list_supplementary_for",
                return_value=[],
            )
        )
        patches.enter_context(
            patch.object(
                paper_agentic_operations.message_crud,
                "get_conversation_messages",
                return_value=[],
            )
        )
        patches.enter_context(patch.object(message_api.message_crud, "create"))
        patches.enter_context(
            patch.object(message_api.operations, "rename_conversation")
        )
        patches.enter_context(patch.object(message_api, "track_event"))
        patches.enter_context(patch.object(paper_pydantic_agent, "track_event"))

        response = await message_api.chat_message_stream(
            request=request,
            db=object(),  # type: ignore[arg-type] - CRUD is patched above.
            current_user=current_user,
        )
        stream_text = await _consume_response(response, timeout_seconds)

    events = _parse_sse_events(stream_text)
    error_events = [event for event in events if event.get("type") == "error"]
    if error_events:
        messages = [str(event.get("errorText") or event) for event in error_events]
        raise SmokeFailure("stream returned error event(s): " + "; ".join(messages))

    content_deltas = [
        str(event.get("delta"))
        for event in events
        if event.get("type") == "text-delta" and event.get("delta")
    ]
    if not content_deltas:
        event_types = [str(event.get("type")) for event in events]
        raise SmokeFailure(
            "stream returned no content chunks; event types were " + repr(event_types)
        )

    if not any(event.get("type") == "finish" for event in events):
        raise SmokeFailure("stream returned content but no finish event")

    answer = "".join(content_deltas).strip().replace("\n", " ")
    print(f"Received {len(content_deltas)} content chunk(s): {answer[:160]}")
    print("SMOKE OK")


def main() -> int:
    try:
        version = importlib.metadata.version("pydantic-ai")
    except importlib.metadata.PackageNotFoundError:
        version = "not installed"
    print(f"pydantic-ai {version}")

    try:
        asyncio.run(run_smoke())
    except Exception as exc:
        print(f"SMOKE FAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
