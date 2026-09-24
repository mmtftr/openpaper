"""CRUD for `paper_repos` — one companion repo per paper.

Ownership is enforced through the PAPER (the row has no `user_id`), so every
accessor takes the paper and checks it belongs to the caller first.
"""

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from pydantic import BaseModel
from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from app.database.crud.base_crud import CRUDBase
from app.database.models import PaperRepo, RepoStatus
from app.schemas.user import CurrentUser

logger = logging.getLogger(__name__)

# An `ingesting` row older than this was stranded by a container restart —
# a POST is allowed to re-kick it rather than 409 forever.
STALE_INGEST_AFTER = timedelta(minutes=15)


class PaperRepoBase(BaseModel):
    paper_id: uuid.UUID
    owner: str
    repo: str
    ref: Optional[str] = None
    commit_sha: Optional[str] = None
    status: str = RepoStatus.PENDING.value
    error: Optional[str] = None
    file_count: Optional[int] = None
    total_bytes: Optional[int] = None
    storage_prefix: Optional[str] = None


class PaperRepoCreate(PaperRepoBase):
    pass


class PaperRepoUpdate(BaseModel):
    owner: Optional[str] = None
    repo: Optional[str] = None
    ref: Optional[str] = None
    commit_sha: Optional[str] = None
    status: Optional[str] = None
    error: Optional[str] = None
    file_count: Optional[int] = None
    total_bytes: Optional[int] = None
    storage_prefix: Optional[str] = None


class PaperRepoCRUD(CRUDBase[PaperRepo, PaperRepoCreate, PaperRepoUpdate]):
    """CRUD operations specifically for the PaperRepo model."""

    def get_by_paper_id(
        self, db: Session, *, paper_id: uuid.UUID
    ) -> Optional[PaperRepo]:
        """Fetch the repo row for a paper. Callers MUST have already verified
        that the paper belongs to the current user."""
        return db.query(PaperRepo).filter(PaperRepo.paper_id == paper_id).first()

    def get_ready_for_paper(
        self, db: Session, *, paper_id: uuid.UUID
    ) -> Optional[PaperRepo]:
        row = self.get_by_paper_id(db, paper_id=paper_id)
        if not row:
            return None
        if str(row.status) != RepoStatus.READY.value or not row.commit_sha:
            return None
        return row

    def mark(
        self,
        db: Session,
        *,
        row: PaperRepo,
        user: Optional[CurrentUser] = None,
        **fields,
    ) -> Optional[PaperRepo]:
        """Apply a status transition (and any accompanying columns)."""
        return self.update(
            db, db_obj=row, obj_in=PaperRepoUpdate(**fields), user=user
        )

    def claim_for_ingestion(
        self, db: Session, *, row_id: uuid.UUID
    ) -> Optional[PaperRepo]:
        """Atomically move `pending`/`error`/stale-`ingesting` → `ingesting`.

        A read-then-write claim lets two POSTs both decide they own the job
        and then overwrite each other's terminal status. This is a single
        conditional UPDATE: exactly one caller gets a row back.
        """
        cutoff = datetime.now(timezone.utc) - STALE_INGEST_AFTER
        try:
            updated = (
                db.query(PaperRepo)
                .filter(
                    PaperRepo.id == row_id,
                    or_(
                        PaperRepo.status.in_(
                            [RepoStatus.PENDING.value, RepoStatus.ERROR.value]
                        ),
                        and_(
                            PaperRepo.status == RepoStatus.INGESTING.value,
                            PaperRepo.updated_at < cutoff,
                        ),
                    ),
                )
                .update(
                    {
                        PaperRepo.status: RepoStatus.INGESTING.value,
                        PaperRepo.error: None,
                        PaperRepo.updated_at: datetime.now(timezone.utc),
                    },
                    synchronize_session=False,
                )
            )
            db.commit()
        except Exception as exc:
            db.rollback()
            logger.error("Failed to claim repo ingestion %s: %s", row_id, exc)
            return None
        if not updated:
            return None
        return db.query(PaperRepo).filter(PaperRepo.id == row_id).first()


def is_stale_ingest(row: PaperRepo) -> bool:
    """True when an in-flight row was abandoned (e.g. a restart mid-run).

    Covers `pending` as well as `ingesting`: a container that died between
    creating the row and running the background task leaves a `pending` row
    that must not be un-recoverable.
    """
    if str(row.status) not in (
        RepoStatus.INGESTING.value,
        RepoStatus.PENDING.value,
    ):
        return False
    updated_at = getattr(row, "updated_at", None)
    if updated_at is None:
        return True
    if updated_at.tzinfo is None:
        updated_at = updated_at.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - updated_at > STALE_INGEST_AFTER


paper_repo_crud = PaperRepoCRUD(PaperRepo)
