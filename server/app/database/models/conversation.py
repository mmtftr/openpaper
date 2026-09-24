"""Chat conversations and their messages."""

from __future__ import annotations

import uuid
from enum import Enum
from typing import TYPE_CHECKING, Any, Optional

from sqlalchemy import UUID, CheckConstraint, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.models.base import Base

if TYPE_CHECKING:
    from app.database.models.paper import Paper
    from app.database.models.user import User


class RoleType(str, Enum):
    USER = "user"
    ASSISTANT = "assistant"


class ConversableType(str, Enum):
    PAPER = "paper"


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("conversations.id", ondelete="CASCADE"),
        nullable=False,
    )
    role: Mapped[str] = mapped_column(String, nullable=False)  # a `RoleType`
    content: Mapped[str] = mapped_column(Text, nullable=False)

    # References from the paper. Key 'citations' maps to list of ResponseCitation dicts
    references: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONB, nullable=True)

    bucket: Mapped[Optional[dict[str, Any]]] = mapped_column(
        JSONB, nullable=True
    )  # For any additional attributes
    sequence: Mapped[int] = mapped_column(
        Integer, nullable=False
    )  # To maintain message order
    user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )

    user: Mapped[Optional[User]] = relationship(back_populates="messages")
    conversation: Mapped[Conversation] = relationship(back_populates="messages")


class Conversation(Base):
    __tablename__ = "conversations"
    __table_args__ = (
        CheckConstraint(
            "conversable_type = 'paper' AND conversable_id IS NOT NULL",
            name="check_conversable_paper",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    title: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )

    # Polymorphic target; only papers today (see the check constraint).
    conversable_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    conversable_type: Mapped[str] = mapped_column(
        String, nullable=False, default=ConversableType.PAPER
    )

    paper: Mapped[Optional[Paper]] = relationship(
        primaryjoin="and_(foreign(Conversation.conversable_id) == Paper.id, "
        "Conversation.conversable_type == 'paper')",
        viewonly=True,
    )

    user: Mapped[Optional[User]] = relationship(back_populates="conversations")

    messages: Mapped[list[Message]] = relationship(
        back_populates="conversation",
        order_by=Message.sequence,
        cascade="all, delete-orphan",
    )
