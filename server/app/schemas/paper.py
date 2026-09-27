from typing import List, Literal, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, HttpUrl

from app.database.models import PaperStatus
from app.schemas.common import MessageResponse  # noqa: F401  (re-export)
from app.schemas.json_datetime import IsoDatetime, StrDatetime


class BulkTagRequest(BaseModel):
    paper_ids: List[UUID]
    tag_ids: List[UUID]


class ArchivePapersRequest(BaseModel):
    paper_ids: List[UUID] = Field(min_length=1)
    # False unarchives.
    archived: bool = True


class ArchivePapersResponse(BaseModel):
    """The papers that were (un)archived, and their `archived_at` now."""

    paper_ids: List[UUID]
    archived_at: Optional[IsoDatetime] = None


# -- tags -----------------------------------------------------------------


class PaperTagResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    color: Optional[str] = None


class TaggedPaper(BaseModel):
    id: UUID
    title: Optional[str] = None
    authors: Optional[List[str]] = None
    publish_date: Optional[IsoDatetime] = None


# -- paper lists ----------------------------------------------------------
# Three list endpoints return successively richer items; the field order
# follows the JSON the client has always received.


class RelevantPaper(BaseModel):
    """Item of `GET /api/paper/relevant`."""

    id: UUID
    title: Optional[str] = None
    created_at: Optional[StrDatetime] = None
    abstract: Optional[str] = None
    authors: Optional[List[str]] = None
    institutions: Optional[List[str]] = None
    keywords: Optional[List[str]] = None
    status: PaperStatus
    preview_url: Optional[str] = None
    size_in_kb: Optional[int] = None


class ActivePaper(RelevantPaper):
    """Item of `GET /api/paper/active`."""

    publish_date: Optional[StrDatetime] = None


class LibraryPaper(ActivePaper):
    """Item of `GET /api/paper/all`; `file_url` is only set with `detailed=true`."""

    file_url: Optional[str] = None
    tags: List[PaperTagResponse] = []
    # Ingest is still running (some stage pending/queued/running).
    processing: bool = False
    # Only set in the archived view (`archived=true`).
    archived_at: Optional[IsoDatetime] = None


class RelevantPapersResponse(BaseModel):
    papers: List[RelevantPaper]


class ActivePapersResponse(BaseModel):
    papers: List[ActivePaper]


class LibraryPapersResponse(BaseModel):
    papers: List[LibraryPaper]


# -- single paper ---------------------------------------------------------


class PaperRecord(BaseModel):
    """A paper row's metadata columns.

    Large or internal columns (`ts_vector`, `generated_outline`, storage
    keys, upload sources and the presigned-URL cache) are left out.
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    status: PaperStatus
    file_url: str
    preview_url: Optional[str] = None
    authors: Optional[List[str]] = None
    title: Optional[str] = None
    abstract: Optional[str] = None
    institutions: Optional[List[str]] = None
    keywords: Optional[List[str]] = None
    publish_date: Optional[StrDatetime] = None
    last_accessed_at: Optional[StrDatetime] = None
    doi: Optional[str] = None
    journal: Optional[str] = None
    publisher: Optional[str] = None
    size_in_kb: Optional[int] = None
    page_count: Optional[int] = None
    supplementary_of_paper_id: Optional[UUID] = None
    created_at: Optional[StrDatetime] = None
    updated_at: Optional[StrDatetime] = None
    # Archived papers stay openable; they're only left out of the lists.
    archived_at: Optional[IsoDatetime] = None


class PaperDetail(PaperRecord):
    """`GET /api/paper`: the row plus a presigned `file_url` and its tags."""

    tags: List[PaperTagResponse] = []


class UpdatePaperFieldsRequest(BaseModel):
    title: Optional[str] = None
    authors: Optional[List[str]] = None
    abstract: Optional[str] = None
    institutions: Optional[List[str]] = None
    keywords: Optional[List[str]] = None
    publish_date: Optional[str] = None
    doi: Optional[str] = None
    journal: Optional[str] = None
    publisher: Optional[str] = None


class PaperMarkdown(BaseModel):
    markdown: str
    source: Literal["mistral", "pymupdf"]


class PaperConversationSummary(BaseModel):
    id: UUID
    title: Optional[str] = None
    created_at: Optional[IsoDatetime] = None
    updated_at: Optional[IsoDatetime] = None


class SupplementaryMaterialItem(BaseModel):
    """A supplementary material (its own paper row; see `/ingest` for progress)."""

    id: UUID
    title: Optional[str] = None
    preview_url: Optional[str] = None
    page_count: Optional[int] = None
    created_at: Optional[IsoDatetime] = None


class PaperFigureSummary(BaseModel):
    id: Optional[str] = None
    label: Optional[str] = None
    caption: Optional[str] = None
    page: Optional[int] = None
    available: bool


# -- uploads --------------------------------------------------------------


class UploadFromUrlRequest(BaseModel):
    url: HttpUrl


class UploadedPaper(BaseModel):
    """The new paper: readable at once, the rest of ingest runs in the worker."""

    paper_id: UUID
