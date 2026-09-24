from typing import List, Literal, Optional
from uuid import UUID

from app.database.models import JobStatus, PaperStatus
from app.schemas.json_datetime import IsoDatetime, StrDatetime
from pydantic import BaseModel, ConfigDict, HttpUrl


class BulkTagRequest(BaseModel):
    paper_ids: List[UUID]
    tag_ids: List[UUID]


class EnrichedData(BaseModel):
    publisher: Optional[str]
    journal: Optional[str]
    publication_date: Optional[str]


class MessageResponse(BaseModel):
    message: str


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


class RelevantPapersResponse(BaseModel):
    papers: List[RelevantPaper]


class ActivePapersResponse(BaseModel):
    papers: List[ActivePaper]


class LibraryPapersResponse(BaseModel):
    papers: List[LibraryPaper]


# -- single paper ---------------------------------------------------------


class PaperRecord(BaseModel):
    """A paper row's metadata columns.

    Large or internal columns (`raw_content`, `ocr`, `ts_vector`,
    `page_offset_map`, `generated_outline`, storage keys and the
    presigned-URL cache) are left out.
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
    # "mistral" | "pymupdf": selects the chat context modes for this paper.
    parser: Optional[str] = None
    figure_count: Optional[int] = None
    page_count: Optional[int] = None
    supplementary_of_paper_id: Optional[UUID] = None
    created_at: Optional[StrDatetime] = None
    updated_at: Optional[StrDatetime] = None


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
    """A finished supplementary paper, or an upload job still producing one."""

    id: UUID
    title: Optional[str] = None
    preview_url: Optional[str] = None
    page_count: Optional[int] = None
    created_at: Optional[IsoDatetime] = None
    status: JobStatus


class PaperFigureSummary(BaseModel):
    id: Optional[str] = None
    label: Optional[str] = None
    caption: Optional[str] = None
    page: Optional[int] = None
    available: bool


# -- uploads --------------------------------------------------------------


class UploadFromUrlRequest(BaseModel):
    url: HttpUrl


class UploadStartedResponse(BaseModel):
    message: str
    job_id: UUID


class UploadJobStatusResponse(BaseModel):
    """The `celery_*` keys are present only while a Celery task is live."""

    job_id: UUID
    status: JobStatus
    task_id: Optional[str] = None
    started_at: IsoDatetime
    completed_at: Optional[IsoDatetime] = None
    has_file_url: bool
    has_metadata: bool
    paper_id: Optional[UUID] = None
    celery_status: Optional[str] = None
    celery_progress_message: Optional[str] = None
    celery_error: Optional[str] = None
