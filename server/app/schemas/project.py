from datetime import datetime
from typing import Optional
from uuid import UUID

from app.database.models import PaperStatus
from pydantic import BaseModel, ConfigDict, Field


class ProjectResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    title: Optional[str] = None
    description: Optional[str] = None
    owner_id: UUID
    created_at: datetime
    updated_at: datetime
    # Only on `GET /api/projects?detailed=true`; absent from the JSON otherwise.
    num_papers: Optional[int] = Field(
        default=None, exclude_if=lambda value: value is None
    )


class ProjectPaperItem(BaseModel):
    """A paper as listed inside a project."""

    id: UUID
    title: Optional[str] = None
    created_at: datetime
    abstract: Optional[str] = None
    authors: Optional[list[str]] = None
    institutions: Optional[list[str]] = None
    keywords: Optional[list[str]] = None
    status: PaperStatus
    journal: Optional[str] = None
    publisher: Optional[str] = None
    doi: Optional[str] = None
    publish_date: Optional[datetime] = None
    file_url: Optional[str] = None


class ProjectPapersResponse(BaseModel):
    papers: list[ProjectPaperItem]
