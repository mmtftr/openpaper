"""The owner's paper lists: the library, reading list and "relevant" papers.

Supplementary materials are Paper rows too; none of these lists show them.
"""

import uuid
from typing import List, Optional

from sqlalchemy import select, true
from sqlalchemy.orm import Session, selectinload

from app.database.models import Paper, PaperStatus
from app.ingest.models import IngestStage, StageStatus
from app.schemas.user import CurrentUser


def library_papers(
    db: Session,
    *,
    user: CurrentUser,
    skip: int = 0,
    limit: int = 500,
    status: Optional[PaperStatus] = None,
) -> List[Paper]:
    """The owner's papers (optionally one status), last updated first, with
    their tags loaded.

    Papers still being ingested are included: they are readable at once.
    """
    return (
        db.query(Paper)
        .options(selectinload(Paper.tags))
        .filter(
            Paper.user_id == user.id,
            Paper.status == status if status else true(),
            Paper.supplementary_of_paper_id.is_(None),
        )
        .order_by(Paper.updated_at.desc())
        .offset(skip)
        .limit(limit)
        .all()
    )


def relevant_papers(db: Session, *, user: CurrentUser, limit: int = 9) -> List[Paper]:
    """Up to `limit` papers the owner is working on: those being read (most
    recently opened first), topped up with to-read ones."""

    def by_status(status: PaperStatus, n: int) -> List[Paper]:
        return (
            db.query(Paper)
            .filter(
                Paper.user_id == user.id,
                Paper.status == status,
                Paper.supplementary_of_paper_id.is_(None),
            )
            .order_by(Paper.last_accessed_at.desc())
            .limit(n)
            .all()
        )

    reading = by_status(PaperStatus.reading, limit)
    if len(reading) >= limit:
        return reading
    return reading + by_status(PaperStatus.todo, limit - len(reading))


def processing_ids(db: Session, paper_ids: list[uuid.UUID]) -> set[uuid.UUID]:
    """The papers among `paper_ids` whose ingest is still running."""
    if not paper_ids:
        return set()
    return set(
        db.scalars(
            select(IngestStage.paper_id)
            .where(
                IngestStage.paper_id.in_(paper_ids),
                IngestStage.status.in_(
                    [StageStatus.PENDING, StageStatus.QUEUED, StageStatus.RUNNING]
                ),
            )
            .distinct()
        )
    )
