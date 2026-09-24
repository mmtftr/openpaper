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

GET /stream-parts is schema-only: it types OpenPaper's custom stream parts
(app/schemas/chat_stream.py) in the OpenAPI document.
"""

import logging
from typing import Any, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, ValidationError, field_validator
from pydantic_ai.ui.vercel_ai.request_types import UIMessage
from sqlalchemy.orm import Session

from app.api.errors import ApiError
from app.auth.dependencies import get_required_user
from app.database.database import get_db
from app.llm.chat.quick_question import (
    MAX_QUESTION_CHARS as MAX_QUICK_QUESTION_CHARS,
)
from app.llm.chat.quick_question import QuickQuestionError, run_quick_question
from app.llm.chat.runtime import ChatRequestError, run_paper_chat
from app.llm.chat.stream import OpenPaperAdapter
from app.llm.model_registry import LLMProvider, get_registry
from app.llm.model_slots import resolve_slot
from app.schemas.chat_stream import ChatStreamSchema
from app.schemas.user import CurrentUser

logger = logging.getLogger(__name__)

message_router = APIRouter()

# See https://ai-sdk.dev/docs/ai-sdk-ui/stream-protocol
UI_MESSAGE_STREAM_HEADERS = {"x-vercel-ai-ui-message-stream": "v1"}
UI_MESSAGE_STREAM_RESPONSES: dict[int | str, dict[str, Any]] = {
    200: {
        "description": (
            "Vercel AI SDK UIMessage stream (SSE). OpenPaper's custom parts "
            "are typed by GET /api/message/stream-parts."
        ),
        "content": {"text/event-stream": {"schema": {"type": "string"}}},
    }
}


class ChatModelOption(BaseModel):
    id: str
    name: str
    provider: LLMProvider
    supports_reasoning_effort: bool
    supports_vision: bool


class ChatModelsResponse(BaseModel):
    models: list[ChatModelOption]
    # What a chat request that names no model gets: the `chat.default` slot
    # (with any stored override), model and reasoning effort.
    default: str
    default_provider: LLMProvider
    default_reasoning_effort: Optional[str] = None


@message_router.get("/models")
def get_available_models() -> ChatModelsResponse:
    """User-selectable chat models with capabilities, for the picker."""
    registry = get_registry()
    specs = registry.chat_models()
    default_slot = resolve_slot("chat.default", registry)
    return ChatModelsResponse(
        models=[ChatModelOption(**spec.to_public_dict()) for spec in specs],
        default=default_slot.spec.id,
        default_provider=default_slot.spec.provider,
        default_reasoning_effort=default_slot.reasoning_effort,
    )


@message_router.get("/stream-parts", responses={404: {"model": ApiError}})
def stream_parts_schema() -> ChatStreamSchema:
    """Schema-only: types OpenPaper's custom parts on the chat and
    quick-question UIMessage streams (`data-citations`, the transient
    `data-retry-status`, and assistant message metadata). Nothing to fetch;
    always 404."""
    raise HTTPException(status_code=404, detail="Schema-only route.")


class PaperChatBody(BaseModel):
    """OpenPaper's custom fields riding along the AI SDK submit payload."""

    paper_id: str
    conversation_id: str
    llm_provider: Optional[str] = None
    model: Optional[str] = None
    reasoning_effort: Optional[Literal["low", "medium", "high", "xhigh"]] = None
    context_mode: Optional[Literal["adaptive", "comprehensive", "full"]] = None
    # PDF text selections attached by the user. Bounded: they flow into the
    # model prompt.
    user_references: Optional[list[str]] = Field(default=None, max_length=20)

    @field_validator("user_references")
    @classmethod
    def _cap_reference_length(cls, value: Optional[list[str]]) -> Optional[list[str]]:
        if value and any(len(ref) > 5000 for ref in value):
            raise ValueError("Each reference is limited to 5000 characters.")
        return value


class PaperChatRequest(PaperChatBody):
    """The whole POST body: the AI SDK submit payload plus OpenPaper's fields.

    `messages` carries only the NEW user message; server-side history is
    ground truth.
    """

    trigger: Literal["submit-message", "regenerate-message"] = "submit-message"
    id: str
    messages: list[UIMessage]
    messageId: Optional[str] = None


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


@message_router.post(
    "/quick-question/code",
    response_class=StreamingResponse,
    responses=UI_MESSAGE_STREAM_RESPONSES,
)
async def quick_question_code(
    extras: QuickQuestionCodeBody,
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


@message_router.post(
    "/chat/paper",
    response_class=StreamingResponse,
    responses=UI_MESSAGE_STREAM_RESPONSES,
)
async def chat_paper(
    extras: PaperChatRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_required_user),
) -> StreamingResponse:
    # `extras` is the validated body (typed schema + OpenPaper's fields); the
    # adapter builds its own run input from the same raw bytes, which
    # Starlette has cached on the request.
    try:
        run_input = OpenPaperAdapter.build_run_input(await request.body())
    except ValidationError as exc:
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
