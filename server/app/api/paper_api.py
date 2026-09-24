import logging
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List

from dotenv import load_dotenv
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.dependencies import get_required_user
from app.database.crud.conversation_crud import conversation_crud
from app.database.crud.paper_crud import PaperUpdate, paper_crud
from app.database.crud.projects.project_paper_crud import project_paper_crud
from app.database.database import get_db
from app.database.models import Paper, PaperStatus
from app.database.telemetry import track_event
from app.helpers.s3 import s3_service
from app.ingest import content, storage
from app.ingest.models import METADATA_FIELDS, IngestStage, MetadataSource, StageStatus
from app.llm.paper_outline import OutlineEntry
from app.schemas.paper import (
    ActivePaper,
    ActivePapersResponse,
    LibraryPaper,
    LibraryPapersResponse,
    MessageResponse,
    PaperConversationSummary,
    PaperDetail,
    PaperMarkdown,
    PaperRecord,
    RelevantPaper,
    RelevantPapersResponse,
    SupplementaryMaterialItem,
    UpdatePaperFieldsRequest,
)
from app.schemas.user import CurrentUser

load_dotenv()

logger = logging.getLogger(__name__)

# Create API router with prefix
paper_router = APIRouter()


# `![alt](src)` / `![alt](src "title")` image references in page markdown.
_MARKDOWN_IMAGE_RE = re.compile(r"(!\[[^\]]*\]\()([^)\s]+)((?:\s+\"[^\"]*\")?\))")


def _paper_markdown_payload(db: Session, paper: Paper) -> PaperMarkdown:
    """The whole paper as one markdown document (the markdown reader).

    Mistral numbers images per OCR batch, so "img-0.jpeg" can occur on
    several pages; each page's image references are pointed at the figure's
    row id instead, which the figure endpoint resolves unambiguously.
    """
    figure_ids = {
        (fig.page_no, fig.ocr_image_id): str(fig.id)
        for fig in content.figures(db, paper.id)
    }

    def page_text(page: content.Page) -> str:
        def point_at_row(m: re.Match[str]) -> str:
            row_id = figure_ids.get((page.page_no, m.group(2)))
            return m.group(1) + row_id + m.group(3) if row_id else m.group(0)

        return _MARKDOWN_IMAGE_RE.sub(point_at_row, page.markdown).strip()

    pages = content.pages(db, paper.id)
    markdown = "\n\n".join(page_text(page) for page in pages).strip()
    return PaperMarkdown(markdown=markdown, source="mistral")


def _paper_files(db: Session, paper: Paper) -> tuple[list[str], set[str]]:
    """A paper's S3 objects as (prefixes, keys): everything under
    `papers/{id}/` plus, for papers the old pipeline stored, the PDF /
    preview / figure images it kept elsewhere (`uploads/…`, `figures/{id}/…`)."""
    prefix = storage.paper_prefix(paper.id)
    keys = [
        str(paper.s3_object_key) if paper.s3_object_key else None,
        storage.key_from_public_url(s3_service, paper.preview_url),
        *(f.s3_key for f in content.figures(db, paper.id)),
    ]
    return (
        [prefix, f"figures/{paper.id}/"],
        {k for k in keys if k and not k.startswith(prefix)},
    )


def _processing_ids(db: Session, paper_ids: list[uuid.UUID]) -> set[uuid.UUID]:
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


def _list_item_fields(paper: Paper) -> Dict[str, Any]:
    """Fields shared by every paper-list item (see `RelevantPaper`)."""
    return {
        "id": paper.id,
        "title": paper.title,
        "created_at": paper.created_at,
        "abstract": paper.abstract,
        "authors": paper.authors,
        "institutions": paper.institutions,
        "keywords": paper.keywords,
        "status": paper.status,
        "preview_url": paper.preview_url,
        "size_in_kb": paper.size_in_kb,
    }


@paper_router.get("/all")
def get_paper_ids(
    db: Session = Depends(get_db),
    detailed: bool = False,
    current_user: CurrentUser = Depends(get_required_user),
) -> LibraryPapersResponse:
    """
    Get all paper IDs
    """
    papers: List[Paper] = paper_crud.get_library(db, user=current_user)

    # Bulk retrieve presigned URLs for all papers (optimized with parallelization)
    file_urls = {}
    if detailed:
        file_urls = s3_service.get_cached_presigned_urls_bulk(
            db=db,
            papers=papers,
        )

    processing = _processing_ids(db, [p.id for p in papers])
    return LibraryPapersResponse(
        papers=[
            LibraryPaper(
                **_list_item_fields(paper),
                publish_date=paper.publish_date,
                file_url=file_urls.get(str(paper.id)),
                tags=paper.tags,  # pyright: ignore[reportArgumentType]
                processing=paper.id in processing,
            )
            for paper in papers
        ]
    )


@paper_router.get("/active")
def get_active_paper_ids(
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_required_user),
) -> ActivePapersResponse:
    """
    Get all active paper IDs
    """
    papers: List[Paper] = paper_crud.get_library(
        db, user=current_user, status=PaperStatus.reading
    )
    return ActivePapersResponse(
        papers=[
            ActivePaper(**_list_item_fields(paper), publish_date=paper.publish_date)
            for paper in papers
        ]
    )


@paper_router.post("/status")
def set_paper_status(
    paper_id: uuid.UUID,
    status: PaperStatus,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_required_user),
) -> PaperRecord:
    """
    Set the status of a paper
    """
    target_paper = paper_crud.get(db, id=paper_id, user=current_user)

    if not target_paper:
        raise HTTPException(status_code=404, detail=f"No document with id {paper_id}")

    paper_update = PaperUpdate(status=status)
    updated_paper = paper_crud.update(
        db=db, db_obj=target_paper, obj_in=paper_update, user=current_user
    )

    if not updated_paper:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to update paper status for document ID {paper_id}",
        )

    track_event(
        "paper_status_updated",
        properties={
            "paper_id": str(updated_paper.id),
            "status": updated_paper.status,
        },
        user_id=str(current_user.id),
    )

    return PaperRecord.model_validate(updated_paper)


@paper_router.patch("")
def update_paper_fields(
    paper_id: uuid.UUID,
    request: UpdatePaperFieldsRequest,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_required_user),
) -> PaperRecord:
    """
    Update editable fields of a paper (title, authors, abstract, etc.)
    """
    target_paper = paper_crud.get(db, id=paper_id, user=current_user)

    if not target_paper:
        raise HTTPException(status_code=404, detail=f"No document with id {paper_id}")

    update_data = request.model_dump(exclude_unset=True)
    if not update_data:
        raise HTTPException(status_code=400, detail="No fields to update")

    # Owner edits win over later metadata lookups (reprocess, fallback).
    # A new dict, so SQLAlchemy sees the JSONB change.
    sources: dict[str, str] = dict(target_paper.metadata_source or {})
    for name in update_data:
        if name in METADATA_FIELDS:
            sources[name] = MetadataSource.USER.value

    updated_paper = paper_crud.update(
        db=db,
        db_obj=target_paper,
        obj_in={**update_data, "metadata_source": sources},
        user=current_user,
    )

    if not updated_paper:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to update paper fields for document ID {paper_id}",
        )

    track_event(
        "paper_fields_updated",
        properties={
            "paper_id": str(updated_paper.id),
            "updated_fields": list(update_data.keys()),
        },
        user_id=str(current_user.id),
    )

    return PaperRecord.model_validate(updated_paper)


@paper_router.get("/relevant")
def get_relevant_papers(
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_required_user),
) -> RelevantPapersResponse:
    """
    Get the most relevant papers uploaded by the user
    """
    papers: List[Paper] = paper_crud.get_top_relevant_papers(db, user=current_user)
    return RelevantPapersResponse(
        papers=[RelevantPaper(**_list_item_fields(paper)) for paper in papers]
    )


@paper_router.get("/conversations")
def get_paper_conversations(
    paper_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_required_user),
) -> List[PaperConversationSummary]:
    """List every conversation tied to this paper (newest-updated first)."""
    document = paper_crud.get(db, id=paper_id, user=current_user)
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")

    conversations = conversation_crud.get_document_conversations(
        db, paper_id=paper_id, current_user=current_user
    )
    conversations = sorted(conversations, key=lambda c: c.updated_at, reverse=True)

    return [
        PaperConversationSummary(
            id=c.id,
            title=c.title,
            created_at=c.created_at,
            updated_at=c.updated_at,
        )
        for c in conversations
    ]


@paper_router.get("/{paper_id}/supplementary")
def list_supplementary_materials(
    paper_id: uuid.UUID,
    current_user: CurrentUser = Depends(get_required_user),
    db: Session = Depends(get_db),
) -> List[SupplementaryMaterialItem]:
    """Supplementary materials attached to a paper, oldest first."""
    parent = paper_crud.get(db, id=paper_id, user=current_user)
    if not parent:
        raise HTTPException(status_code=404, detail="Parent paper not found")

    supplementary_papers = paper_crud.list_supplementary_for(
        db, parent_paper_id=paper_id, user=current_user
    )
    return [
        SupplementaryMaterialItem(
            id=paper.id,
            # Supplementaries get no metadata lookup: the PDF's embedded
            # title (text_layer) or else the uploaded file's name.
            title=paper.title or paper.source_filename,
            preview_url=paper.preview_url,
            page_count=paper.page_count,
            created_at=paper.created_at,
        )
        for paper in sorted(
            supplementary_papers,
            key=lambda p: p.created_at or datetime.min.replace(tzinfo=timezone.utc),
        )
    ]


@paper_router.get("")
def get_pdf(
    id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_required_user),
) -> PaperDetail:
    """
    Get a document by ID
    """
    # Fetch the document from the database
    paper = paper_crud.get(db, id=id, user=current_user, update_last_accessed=True)

    if not paper:
        raise HTTPException(status_code=404, detail="Document not found")

    signed_url = s3_service.get_cached_presigned_url(
        db,
        paper_id=str(paper.id),
        object_key=str(paper.s3_object_key),
        current_user=current_user,
    )
    if not signed_url:
        raise HTTPException(status_code=404, detail="File not found")

    return PaperDetail.model_validate(paper).model_copy(update={"file_url": signed_url})


@paper_router.get("/outline", response_model=List[OutlineEntry])
def get_paper_outline(
    id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_required_user),
):
    """The outline the ingest `outline` stage built (empty until it ran)."""
    paper = paper_crud.get(db, id=id, user=current_user)
    if not paper:
        raise HTTPException(status_code=404, detail="Document not found")
    return paper.generated_outline or []


@paper_router.get("/markdown")
def get_paper_markdown(
    id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_required_user),
) -> PaperMarkdown:
    paper = paper_crud.get(db, id=id, user=current_user, update_last_accessed=True)
    if not paper:
        raise HTTPException(status_code=404, detail="Document not found")

    return _paper_markdown_payload(db, paper)


@paper_router.delete("")
def delete_pdf(
    id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_required_user),
) -> MessageResponse:
    """
    Delete a document by ID
    """
    # Fetch the document from the database
    paper = paper_crud.get(db, id=id, user=current_user)

    if not paper:
        raise HTTPException(status_code=404, detail="Document not found")

    projects = project_paper_crud.get_projects_by_paper_id(
        db, paper_id=id, user=current_user
    )
    if len(projects) > 0:
        raise HTTPException(
            status_code=400,
            detail="Cannot delete document associated with projects. Please remove the document from all projects before deleting.",
        )

    # Collect the files while the rows exist (figure keys); supplementary
    # materials go with the paper (FK CASCADE), so their files go too.
    files = [
        _paper_files(db, doc)
        for doc in [
            paper,
            *paper_crud.list_supplementary_for(
                db, parent_paper_id=id, user=current_user
            ),
        ]
    ]

    # Lock the stage rows before the paper row, in the ingest worker's order
    # (it locks a paper's stages, then its `save()` may update the paper);
    # the cascade would otherwise take them the other way round.
    db.execute(
        select(IngestStage.paper_id)
        .join(Paper, Paper.id == IngestStage.paper_id)
        .where((Paper.id == id) | (Paper.supplementary_of_paper_id == id))
        .order_by(IngestStage.paper_id, IngestStage.name)
        .with_for_update(of=IngestStage)
    ).all()
    removed_paper = paper_crud.remove(db, id=id, user=current_user)
    if not removed_paper:
        raise HTTPException(status_code=500, detail="Failed to delete document")

    try:
        for prefixes, keys in files:
            for prefix in prefixes:
                storage.delete_prefix(s3_service, prefix)
            for key in keys:
                storage.delete_key(s3_service, key)

        # The paper_repos row goes with the paper (FK CASCADE), but the
        # ingested snapshot lives on a volume — drop it here.
        from app.llm.repo import storage as repo_storage

        repo_storage.delete_paper_snapshots(str(id))
    except Exception as e:
        logger.error(f"Error deleting document files: {str(e)}")
        raise HTTPException(
            status_code=500, detail=f"Error deleting document: {str(e)}"
        )

    return MessageResponse(message="Document deleted")
