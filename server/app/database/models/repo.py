"""A paper's companion GitHub repository."""

from __future__ import annotations

import uuid
from enum import Enum
from typing import TYPE_CHECKING, Optional

from sqlalchemy import UUID, BigInteger, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.models.base import Base

if TYPE_CHECKING:
    from app.database.models.paper import Paper


class RepoStatus(str, Enum):
    PENDING = "pending"
    INGESTING = "ingesting"
    READY = "ready"
    ERROR = "error"


class PaperRepo(Base):
    """A paper's companion GitHub repository, ingested into a local snapshot.

    One repo per paper (UNIQUE paper_id). The snapshot itself lives on disk
    under `{REPO_STORAGE_DIR}/{paper_id}/{commit_sha}/` — this row is the
    index into it plus the ingestion state machine
    (`pending` → `ingesting` → `ready` | `error`).
    """

    __tablename__ = "paper_repos"
    __table_args__ = (
        # In the schema since the baseline, next to the UNIQUE constraint.
        Index("ix_paper_repos_paper_id", "paper_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    paper_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("papers.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )

    owner: Mapped[str] = mapped_column(String, nullable=False)
    repo: Mapped[str] = mapped_column(String, nullable=False)
    # Resolved default branch; the SHA is what everything is pinned to.
    ref: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    commit_sha: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    status: Mapped[str] = mapped_column(
        String, nullable=False, default=RepoStatus.PENDING.value
    )  # a `RepoStatus`
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    file_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    total_bytes: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    # `{paper_id}/{commit_sha}` under REPO_STORAGE_DIR.
    storage_prefix: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    paper: Mapped[Paper] = relationship()
