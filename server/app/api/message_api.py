"""Chat endpoints.

POST /chat/paper speaks the Vercel AI SDK v6 UIMessage stream protocol:
the request body is the AI SDK submit payload (`{trigger, id, messages}` —
the client sends only the NEW user message; server-side history is ground
truth) plus OpenPaper's custom fields (paper_id, conversation_id, model
selection). The response streams UIMessage chunks produced by the
pydantic-ai Vercel adapter. See app/llm/chat/.

POST /quick-question/code streams the same protocol for an ephemeral
question about a selected code range in the paper's connected repo — it gets
read-only repo lookups on a small budget, but nothing is persisted. See
app/llm/chat/quick_question.py.

GET /models lists user-selectable chat models with capability flags.
"""

import json
import logging
from typing import Literal, Optional

from app.auth.dependencies import get_required_user
from app.database.database import get_db
from app.llm.chat.quick_question import (
    MAX_QUESTION_CHARS as MAX_QUICK_QUESTION_CHARS,
)
from app.llm.chat.quick_question import QuickQuestionError, run_quick_question
from app.llm.chat.runtime import ChatRequestError, run_paper_chat
from app.llm.chat.stream import OpenPaperAdapter
from app.llm.model_registry import get_registry
from app.llm.model_slots import resolve_slot
from app.schemas.user import CurrentUser
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, ValidationError, field_validator
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

message_router = APIRouter()

# See https://ai-sdk.dev/docs/ai-sdk-ui/stream-protocol
UI_MESSAGE_STREAM_HEADERS = {"x-vercel-ai-ui-message-stream": "v1"}


@message_router.get("/models")
async def get_available_models() -> dict:
    """User-selectable chat models with capabilities, for the picker."""
    registry = get_registry()
    specs = registry.chat_models()
    default_spec = resolve_slot("chat.default", registry).spec
    return {
        "models": [spec.to_public_dict() for spec in specs],
        "default": default_spec.id,
        "default_provider": default_spec.provider.value,
    }


class PaperChatBody(BaseModel):
    """OpenPaper's custom fields riding along the AI SDK submit payload."""

    paper_id: str
    conversation_id: str
    llm_provider: Optional[str] = None
    model: Optional[str] = None
    reasoning_effort: Optional[Literal["low", "medium", "high", "xhigh"]] = None
    context_mode: Optional[
        Literal["adaptive", "comprehensive", "full", "raw"]
    ] = None
    # PDF text selections attached by the user. Bounded: they flow into the
    # model prompt.
    user_references: Optional[list[str]] = Field(
        default=None, max_length=20
    )

    @field_validator("user_references")
    @classmethod
    def _cap_reference_length(
        cls, value: Optional[list[str]]
    ) -> Optional[list[str]]:
        if value and any(len(ref) > 5000 for ref in value):
            raise ValueError("Each reference is limited to 5000 characters.")
        return value


class QuickQuestionCodeBody(BaseModel):
    """Inline code question. Ephemeral: no conversation, nothing persisted."""

    paper_id: str
    question: str = Field(min_length=1, max_length=MAX_QUICK_QUESTION_CHARS)
    file_path: str
    start_line: int
    end_line: int
    llm_provider: Optional[str] = None
    model: Optional[str] = None
    reasoning_effort: Optional[Literal["low", "medium", "high", "xhigh"]] = None


@message_router.post("/quick-question/code")
async def quick_question_code(
    request: Request,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_required_user),
) -> StreamingResponse:
    """Stream a one-shot answer about a selected range of a repo file.

    Same wire protocol as /chat/paper (the client uses the identical ai-sdk
    reader), with read-only repo lookup tools but no citations and nothing
    written to conversations or messages.
    """
    try:
        extras = QuickQuestionCodeBody.model_validate(json.loads(await request.body()))
    except (ValidationError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    try:
        stream = run_quick_question(
            db=db,
            current_user=current_user,
            accept=request.headers.get("accept"),
            paper_id=extras.paper_id,
            question=extras.question,
            file_path=extras.file_path,
            start_line=extras.start_line,
            end_line=extras.end_line,
            provider=extras.llm_provider,
            model=extras.model,
            reasoning_effort=extras.reasoning_effort,
        )
        # Force validation now so pre-stream failures surface as proper HTTP
        # errors instead of a 200 with a broken SSE body.
        first_chunk = await stream.__anext__()
    except QuickQuestionError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc))
    except StopAsyncIteration:
        raise HTTPException(status_code=500, detail="Empty quick-question stream.")

    async def sse():
        # Same disconnect handling as chat: Starlette's cancellation can lag
        # far behind the client abort while the model keeps generating on
        # our dime, so poll the ASGI state on every chunk.
        try:
            yield first_chunk
            async for chunk in stream:
                if await request.is_disconnected():
                    logger.info("Client disconnected; stopping quick-question stream")
                    break
                yield chunk
        finally:
            await stream.aclose()

    return StreamingResponse(
        sse(),
        media_type="text/event-stream",
        headers=UI_MESSAGE_STREAM_HEADERS,
    )


@message_router.post("/chat/paper")
async def chat_paper(
    request: Request,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_required_user),
) -> StreamingResponse:
    body = await request.body()
    try:
        run_input = OpenPaperAdapter.build_run_input(body)
        extras = PaperChatBody.model_validate(json.loads(body))
    except (ValidationError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    try:
        stream = run_paper_chat(
            db=db,
            current_user=current_user,
            run_input=run_input,
            accept=request.headers.get("accept"),
            paper_id=extras.paper_id,
            conversation_id=extras.conversation_id,
            provider=extras.llm_provider,
            model=extras.model,
            reasoning_effort=extras.reasoning_effort,
            context_mode=extras.context_mode,
            user_references=extras.user_references,
        )
        # Validation happens before the first chunk: force it now so
        # pre-stream failures surface as proper HTTP errors instead of a
        # broken SSE stream.
        first_chunk = await stream.__anext__()
    except ChatRequestError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc))
    except StopAsyncIteration:
        raise HTTPException(status_code=500, detail="Empty chat stream.")

    async def sse():
        # Explicitly close the inner generator on disconnect so its
        # `finally` (partial-turn persistence) runs promptly. Starlette's
        # task cancellation on client disconnect can lag far behind the
        # abort (the model keeps generating on the server's dime), so also
        # poll the ASGI disconnect state on every chunk and bail early.
        try:
            yield first_chunk
            async for chunk in stream:
                if await request.is_disconnected():
                    logger.info("Client disconnected; stopping chat stream")
                    break
                yield chunk
        finally:
            await stream.aclose()

    return StreamingResponse(
        sse(),
        media_type="text/event-stream",
        headers=UI_MESSAGE_STREAM_HEADERS,
    )
