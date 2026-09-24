"""Papers (supplementary materials included) and their tags."""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING, Any, Optional

from sqlalchemy import (
    ARRAY,
    UUID,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.models.base import Base

if TYPE_CHECKING:
    from app.database.models.conversation import Conversation
    from app.database.models.project import ProjectPaper
    from app.database.models.user import User


class PaperStatus(str, Enum):
    todo = "todo"
    reading = "reading"
    completed = "completed"


class Paper(Base):
    __tablename__ = "papers"
    __table_args__ = (
        # Full-text search.
        Index("ix_papers_ts_vector", "ts_vector", postgresql_using="gin"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    # Every upload starts as "reading" (there is no bulk upload to a todo list).
    status: Mapped[str] = mapped_column(
        String, nullable=False, default=PaperStatus.reading
    )
    file_url: Mapped[str] = mapped_column(String, nullable=False)
    preview_url: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    s3_object_key: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    authors: Mapped[Optional[list[str]]] = mapped_column(ARRAY(String), nullable=True)
    title: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    abstract: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    institutions: Mapped[Optional[list[str]]] = mapped_column(
        ARRAY(String), nullable=True
    )
    keywords: Mapped[Optional[list[str]]] = mapped_column(ARRAY(String), nullable=True)
    publish_date: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    # Kept by triggers from the title + the pages' markdown (search).
    ts_vector: Mapped[Optional[str]] = mapped_column(TSVECTOR, nullable=True)
    user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
    # Bumped when the owner opens the paper (the "relevant papers" order).
    last_accessed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    # Cached presigned URL for the PDF.
    cached_presigned_url: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    presigned_url_expires_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    doi: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    arxiv_id: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True
    )  # e.g. "2512.11949" (no version)
    openalex_id: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True
    )  # e.g. "W4388230712"
    # Where each metadata field came from: {"title": "crossref", ...}; values
    # are `app.ingest.models.MetadataSource`. "user" = edited by the owner,
    # which ingest must never overwrite.
    metadata_source: Mapped[dict[str, str]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    journal: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    publisher: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    attempted_metadata_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    size_in_kb: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    # Written by the ingest `outline` stage (`/api/paper/outline` serves it):
    # a list of `app.llm.paper_outline.OutlineEntry` dicts.
    generated_outline: Mapped[Optional[list[dict[str, Any]]]] = mapped_column(
        JSONB, nullable=True
    )
    page_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    # What the upload came from; hints for the ingest `metadata` stage
    # (arXiv ids / DOIs are often in file names and URLs).
    source_filename: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    source_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Supplementary materials are themselves Paper rows that point back to
    # their parent paper. Library listings filter rows where this is non-null
    # so supplementaries don't surface as standalone library items.
    supplementary_of_paper_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("papers.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )

    user: Mapped[Optional[User]] = relationship(back_populates="papers")
    conversations: Mapped[list[Conversation]] = relationship(
        back_populates="paper",
        cascade="all, delete-orphan",
        primaryjoin="and_(Paper.id == foreign(Conversation.conversable_id), "
        "Conversation.conversable_type == 'paper')",
    )

    project_papers: Mapped[list[ProjectPaper]] = relationship(back_populates="paper")

    tags: Mapped[list[PaperTag]] = relationship(
        secondary="paper_tag_association", back_populates="papers"
    )

    # Self-referential link for supplementary materials.
    supplementary_materials: Mapped[list[Paper]] = relationship(
        foreign_keys=[supplementary_of_paper_id],
        back_populates="parent_supplementary",
        cascade="all, delete-orphan",
        single_parent=True,
    )
    parent_supplementary: Mapped[Optional[Paper]] = relationship(
        foreign_keys=[supplementary_of_paper_id],
        back_populates="supplementary_materials",
        remote_side=[id],
    )


class PaperTag(Base):
    __tablename__ = "paper_tags"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(String, nullable=False)
    color: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )

    user: Mapped[User] = relationship(back_populates="paper_tags")
    papers: Mapped[list[Paper]] = relationship(
        secondary="paper_tag_association", back_populates="tags"
    )


class PaperTagAssociation(Base):
    __tablename__ = "paper_tag_association"

    paper_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("papers.id", ondelete="CASCADE"),
        primary_key=True,
    )
    tag_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("paper_tags.id", ondelete="CASCADE"),
        primary_key=True,
    )
