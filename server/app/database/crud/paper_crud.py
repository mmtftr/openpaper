"""CRUD for `papers` rows: by id (via `CRUDBase`) and a paper's
supplementary materials. The owner's paper lists are in
`app.database.queries.library`."""

import uuid
from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database.crud.base_crud import CRUDBase
from app.database.models import Paper, PaperStatus
from app.schemas.user import CurrentUser


class PaperBase(BaseModel):
    file_url: Optional[str] = None
    s3_object_key: Optional[str] = None
    authors: Optional[List[str]] = None
    title: Optional[str] = None
    abstract: Optional[str] = None
    institutions: Optional[List[str]] = None
    keywords: Optional[List[str]] = None
    publish_date: Optional[str] = None
    preview_url: Optional[str] = None
    size_in_kb: Optional[int] = None
    page_count: Optional[int] = None


class PaperCreate(PaperBase):
    # Only mandate required fields for creation, others are optional
    file_url: str  # type: ignore
    s3_object_key: Optional[str] = None
    preview_url: Optional[str] = None
    supplementary_of_paper_id: Optional[uuid.UUID] = None


class PaperUpdate(PaperBase):
    status: Optional[PaperStatus] = PaperStatus.todo
    cached_presigned_url: Optional[str] = None
    presigned_url_expires_at: Optional[datetime] = None
    preview_url: Optional[str] = None
    doi: Optional[str] = None
    size_in_kb: Optional[int] = None
    journal: Optional[str] = None
    publisher: Optional[str] = None
    attempted_metadata_at: Optional[datetime] = None
    supplementary_of_paper_id: Optional[uuid.UUID] = None


class PaperCRUD(CRUDBase[Paper, PaperCreate, PaperUpdate]):
    def list_supplementary_for(
        self,
        db: Session,
        parent_paper_id: uuid.UUID,
        user: CurrentUser,
    ) -> List[Paper]:
        """Return supplementary Paper rows for a given parent, owned by the user."""
        return (
            db.query(Paper)
            .filter(
                Paper.supplementary_of_paper_id == parent_paper_id,
                Paper.user_id == user.id,
            )
            .order_by(Paper.created_at.asc())
            .all()
        )


paper_crud = PaperCRUD(Paper)
