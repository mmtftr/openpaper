"""User-and-agent-editable markdown documents (notes)."""

from __future__ import annotations

import uuid
from enum import Enum
from typing import Optional

from sqlalchemy import UUID, ForeignKey, Index, Integer, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from app.database.models.base import Base


class DocumentKind(str, Enum):
    MAIN = "main"  # the paper's main writeup; exactly one per (paper_id, user_id)
    NOTE = "note"  # any other doc, paper-scoped or root-level


class Document(Base):
    """A markdown document.

    MAIN rows are one per paper per user; NOTE rows are any number of other
    docs. The schema also allows folder trees and root-level user docs
    (`paper_id` NULL), which nothing creates yet.
    """

    __tablename__ = "documents"
    __table_args__ = (
        Index(
            "ux_documents_main_per_paper",
            "paper_id",
            "user_id",
            unique=True,
            postgresql_where=text("kind = 'main' AND paper_id IS NOT NULL"),
        ),
        Index("ix_documents_paper_user", "paper_id", "user_id"),
        Index("ix_documents_parent", "parent_document_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )

    # NULL paper_id = root-level user doc.
    paper_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("papers.id", ondelete="CASCADE"),
        nullable=True,
    )

    # Folder hierarchy. NULL = top of its scope (paper or root).
    parent_document_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=True,
    )

    kind: Mapped[str] = mapped_column(
        String, nullable=False, default=DocumentKind.NOTE
    )  # a `DocumentKind`
    title: Mapped[str] = mapped_column(String, nullable=False, default="Untitled")
    content: Mapped[str] = mapped_column(Text, nullable=False, default="")

    # Bumped on every successful write; used for optimistic locking against
    # concurrent agent + user edits.
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
