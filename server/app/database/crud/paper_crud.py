import logging
import uuid
from datetime import datetime
from typing import List, Optional, Tuple

from pydantic import BaseModel
from sqlalchemy import true
from sqlalchemy.orm import Session, selectinload

from app.database.crud.base_crud import CRUDBase
from app.database.models import Paper, PaperStatus
from app.ingest import content
from app.schemas.user import CurrentUser

logger = logging.getLogger(__name__)


# Define Pydantic models for type safety
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


class PaperDocumentMetadata(BaseModel):
    raw_content: Optional[str] = None
    page_offsets: Optional[dict[int, Tuple[int, int]]] = None


# Paper CRUD that inherits from the base CRUD
class PaperCRUD(CRUDBase["Paper", PaperCreate, PaperUpdate]):
    """CRUD operations specifically for Document model"""

    def read_raw_document_content(
        self,
        db: Session,
        *,
        paper_id: str,
        current_user: CurrentUser,
    ) -> PaperDocumentMetadata:
        """The paper's full text (its pages' markdown, see
        `content.full_text`) and each page's offsets in it."""
        paper: Paper | None = self.get(db, paper_id, user=current_user)
        if paper is None:
            raise ValueError(f"Paper with ID {paper_id} not found.")

        text, offsets = content.full_text(content.pages(db, paper.id))
        if not text:
            raise ValueError(f"Raw content for paper {paper_id} is not set.")

        return PaperDocumentMetadata(raw_content=text, page_offsets=offsets)

    def get_top_relevant_papers(
        self, db: Session, *, user: CurrentUser, limit: int = 9
    ) -> List[Paper]:
        """
        Get recent papers with priority logic:
        1. Order by most recently uploaded
        2. First get papers with 'reading' status
        3. If under limit, fill with 'todo' status papers
        4. Return up to limit papers
        """
        # First, get reading papers
        reading_papers = (
            db.query(Paper)
            .filter(
                Paper.user_id == user.id,
                Paper.status == PaperStatus.reading,
                Paper.supplementary_of_paper_id.is_(None),
            )
            .order_by(Paper.last_accessed_at.desc())
            .limit(limit)
            .all()
        )

        # If we have enough reading papers, return them
        if len(reading_papers) >= limit:
            return reading_papers

        # Calculate how many more papers we need
        remaining_limit = limit - len(reading_papers)

        # Get todo papers to fill the remaining slots
        todo_papers = (
            db.query(Paper)
            .filter(
                Paper.user_id == user.id,
                Paper.status == PaperStatus.todo,
                Paper.supplementary_of_paper_id.is_(None),
            )
            .order_by(Paper.last_accessed_at.desc())
            .limit(remaining_limit)
            .all()
        )

        # Combine and return
        return reading_papers + todo_papers

    def get_library(
        self,
        db: Session,
        *,
        user: CurrentUser,
        skip: int = 0,
        limit: int = 500,
        status: Optional[PaperStatus] = None,
    ) -> List[Paper]:
        """The owner's papers (not supplementary materials), newest first.

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

    def list_supplementary_for(
        self,
        db: Session,
        parent_paper_id: uuid.UUID,
        user: CurrentUser,
    ) -> List[Paper]:
        """Return supplementary Paper rows for a given parent, owned by the user."""
        return (
            db.query(self.model)
            .filter(
                self.model.supplementary_of_paper_id == parent_paper_id,
                self.model.user_id == user.id,
            )
            .order_by(self.model.created_at.asc())
            .all()
        )


# Create a single instance to use throughout the application
paper_crud = PaperCRUD(Paper)
