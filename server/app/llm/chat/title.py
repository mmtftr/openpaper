"""Conversation titles, generated from the first exchange (`chat.title` slot)."""

import logging
import uuid

from sqlalchemy.orm import Session

from app.database.crud.conversation_crud import ConversationUpdate, conversation_crud
from app.database.crud.message_crud import message_crud
from app.database.models import Conversation
from app.llm import oneshot
from app.llm.prompts import (
    RENAME_CONVERSATION_SYSTEM_PROMPT,
    RENAME_CONVERSATION_USER_MESSAGE,
)
from app.schemas.user import CurrentUser

logger = logging.getLogger(__name__)


def rename_conversation(
    conversation_id: str,
    user: CurrentUser,
    db: Session,
) -> str | None:
    """Title a conversation from its chat history (synchronous LLM call)."""
    casted_uuid = uuid.UUID(conversation_id)
    conversation: Conversation | None = conversation_crud.get_conversation_by_id(
        db, conversation_id=casted_uuid, user_id=user.id
    )

    if not conversation:
        raise ValueError(f"Conversation with ID {conversation_id} not found.")

    # Idempotent: skip if a title already exists. Lets callers fire this
    # after every chat message — only the first exchange triggers the LLM.
    if conversation.title:  # type: ignore[truthy-bool]
        return str(conversation.title)

    chat_history = message_crud.get_conversation_messages(
        db, conversation_id=casted_uuid, current_user=user
    )

    if not chat_history:
        logger.warning(
            f"Conversation with ID {conversation_id} has no messages. Cannot rename."
        )
        return None

    # Format the chat history for the LLM, restrict to the last 4 messages
    formatted_chat_history = "\n".join(
        [f"{msg.role}: {msg.content}" for msg in chat_history[-4:]]
    )

    formatted_prompt = RENAME_CONVERSATION_USER_MESSAGE.format(
        chat_history=formatted_chat_history
    )

    text = oneshot.complete_sync(
        "chat.title",
        formatted_prompt,
        instructions=RENAME_CONVERSATION_SYSTEM_PROMPT,
    )

    if text:
        new_title = text.strip()
        conversation_crud.update(
            db,
            db_obj=conversation,
            obj_in=ConversationUpdate(title=new_title),
            user=user,
        )
        return new_title

    logger.error(f"Failed to generate a new title for conversation {conversation_id}.")
    return None
