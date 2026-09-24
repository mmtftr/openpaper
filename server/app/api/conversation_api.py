import logging
import uuid
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, field_serializer
from pydantic_ai.ui.vercel_ai.request_types import UIMessage
from sqlalchemy.orm import Session

from app.auth.dependencies import get_required_user
from app.database.crud.conversation_crud import (
    ConversationCreate,
    ConversationUpdate,
    conversation_crud,
)
from app.database.crud.message_crud import message_crud
from app.database.crud.paper_crud import paper_crud
from app.database.database import get_db
from app.database.models import ConversableType
from app.llm.chat.history import serialize_ui_messages
from app.llm.chat.title import rename_conversation as generate_conversation_title
from app.schemas.common import MessageResponse
from app.schemas.user import CurrentUser

logger = logging.getLogger(__name__)

conversation_router = APIRouter()


class RenameConversationResponse(BaseModel):
    new_title: str


class ConversationSummary(BaseModel):
    id: uuid.UUID
    title: Optional[str] = None


class ConversationPage(ConversationSummary):
    """A page of a conversation as Vercel AI UIMessages (chronological)."""

    messages: list[UIMessage]

    @field_serializer("messages")
    def _messages_wire(self, messages: list[UIMessage]):
        # The AI SDK's shape: camelCase, and unset optionals absent rather
        # than null.
        return [
            message.model_dump(mode="json", by_alias=True, exclude_none=True)
            for message in messages
        ]


def _not_found(conversation_id: Any) -> str:
    return f"Conversation with ID {conversation_id} not found."


@conversation_router.post("/{conversation_id}/rename")
def rename_conversation(
    conversation_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_required_user),
) -> RenameConversationResponse:
    """Rename a conversation based on its chat history"""
    try:
        new_name = generate_conversation_title(
            db=db, conversation_id=str(conversation_id), user=current_user
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    if not new_name:
        raise HTTPException(
            status_code=502,
            detail="Failed to rename conversation. No new title generated.",
        )
    return RenameConversationResponse(new_title=new_name)


@conversation_router.get("/{conversation_id}")
def get_conversation(
    conversation_id: uuid.UUID,
    page: int = 1,
    page_size: int = 10,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_required_user),
) -> ConversationPage:
    """Get a conversation page as Vercel AI UIMessages (chronological)."""
    conversation = conversation_crud.require(
        db, conversation_id, user=current_user, not_found=_not_found(conversation_id)
    )

    messages = message_crud.get_conversation_messages(
        db,
        conversation_id=conversation_id,
        current_user=current_user,
        page=page,
        page_size=page_size,
    )

    return ConversationPage.model_validate(
        {
            "id": conversation.id,
            "title": conversation.title,
            "messages": serialize_ui_messages(messages),
        }
    )


@conversation_router.post("/paper/{paper_id}", status_code=201)
def create_conversation(
    paper_id: uuid.UUID,
    title: str | None = None,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_required_user),
) -> ConversationPage:
    """Create a new conversation for a document"""
    paper_crud.require(db, paper_id, user=current_user, not_found="Paper not found.")

    conversation = conversation_crud.create(
        db,
        obj_in=ConversationCreate(
            conversable_type=ConversableType.PAPER,
            conversable_id=paper_id,
            title=title,
        ),
        user=current_user,
    )
    return ConversationPage.model_validate(
        {"id": conversation.id, "title": conversation.title, "messages": []}
    )


@conversation_router.patch("/{conversation_id}")
def update_conversation(
    conversation_id: uuid.UUID,
    title: str,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_required_user),
) -> ConversationSummary:
    """Update conversation title"""
    existing_conversation = conversation_crud.require(
        db, conversation_id, user=current_user, not_found=_not_found(conversation_id)
    )
    conversation = conversation_crud.update(
        db,
        db_obj=existing_conversation,
        obj_in=ConversationUpdate(title=title),
        user=current_user,
    )
    return ConversationSummary.model_validate(
        {"id": conversation.id, "title": conversation.title}
    )


@conversation_router.delete("/{conversation_id}")
def delete_conversation(
    conversation_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_required_user),
) -> MessageResponse:
    """Delete an existing conversation"""
    conversation_crud.remove(
        db,
        id=conversation_id,
        user=current_user,
        not_found=_not_found(conversation_id),
    )
    return MessageResponse(message="Conversation deleted successfully")
