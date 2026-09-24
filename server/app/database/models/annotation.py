"""Highlights on a paper and the annotations (notes) attached to them."""

from __future__ import annotations

import uuid
from enum import Enum
from typing import TYPE_CHECKING, Any, Optional

from sqlalchemy import UUID, CheckConstraint, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.models.base import Base

if TYPE_CHECKING:
    from app.database.models.user import User


class HighlightType(str, Enum):
    TOPIC = "topic"
    MOTIVATION = "motivation"
    METHOD = "method"
    EVIDENCE = "evidence"
    RESULT = "result"
    IMPACT = "impact"
    GENERAL = "general"


class Highlight(Base):
    __tablename__ = "highlights"
    __table_args__ = (
        CheckConstraint("origin IN ('user', 'ai')", name="ck_highlights_origin"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    paper_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("papers.id", ondelete="CASCADE"), nullable=False
    )
    raw_text: Mapped[str] = mapped_column(Text, nullable=False)
    type: Mapped[Optional[str]] = mapped_column(
        String, nullable=True
    )  # a `HighlightType`

    # Position (exact for user, hints for AI)
    start_offset: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    end_offset: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    page_number: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    position: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONB, nullable=True)

    # "user" for highlights the owner made, "assistant" for AI-generated ones.
    role: Mapped[str] = mapped_column(String, nullable=False, default="user")
    # Who created it: "user" or "ai" (the ingest `highlights` stage). AI
    # highlights the owner has annotated survive regeneration.
    origin: Mapped[str] = mapped_column(
        Text, nullable=False, default="user", server_default="user"
    )
    user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
    color: Mapped[Optional[str]] = mapped_column(String, nullable=True, default="blue")

    user: Mapped[Optional[User]] = relationship(back_populates="highlights")
    annotations: Mapped[list[Annotation]] = relationship(
        back_populates="highlight", cascade="all, delete-orphan"
    )


class Annotation(Base):
    __tablename__ = "annotations"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    highlight_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("highlights.id"), nullable=False
    )
    paper_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("papers.id", ondelete="CASCADE"), nullable=False
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)

    role: Mapped[str] = mapped_column(
        String, nullable=False, default="user"
    )  # 'user' or 'assistant'
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )

    user: Mapped[User] = relationship(back_populates="annotations")
    highlight: Mapped[Highlight] = relationship(back_populates="annotations")
