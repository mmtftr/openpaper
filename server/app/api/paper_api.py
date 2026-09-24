import logging
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List

from dotenv import load_dotenv
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.auth.dependencies import get_required_user
from app.database.crud.conversation_crud import conversation_crud
from app.database.crud.paper_crud import PaperUpdate, paper_crud
from app.database.crud.projects.project_paper_crud import project_paper_crud
from app.database.database import get_db
from app.database.models import JobStatus, Paper, PaperStatus, PaperUploadJob
from app.database.telemetry import track_event
from app.helpers.paper_search import get_doi, get_enriched_data
from app.helpers.parser import parse_publication_date
from app.helpers.s3 import s3_service
from app.ingest import content
from app.llm.paper_outline import OutlineEntry, cached_outline
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

CHECK_METADATA_INTERVAL_DAYS = 30


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
    papers: List[Paper] = paper_crud.get_multi_uploads_completed(db, user=current_user)

    # Bulk retrieve presigned URLs for all papers (optimized with parallelization)
    file_urls = {}
    if detailed:
        file_urls = s3_service.get_cached_presigned_urls_bulk(
            db=db,
            papers=papers,
        )

    return LibraryPapersResponse(
        papers=[
            LibraryPaper(
                **_list_item_fields(paper),
                publish_date=paper.publish_date,  # pyright: ignore[reportArgumentType]
                file_url=file_urls.get(str(paper.id)),
                tags=paper.tags,  # pyright: ignore[reportArgumentType]
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
    papers: List[Paper] = paper_crud.get_multi_uploads_completed(
        db, user=current_user, status=PaperStatus.reading
    )
    return ActivePapersResponse(
        papers=[
            ActivePaper(**_list_item_fields(paper), publish_date=paper.publish_date)  # pyright: ignore[reportArgumentType]
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

    updated_paper = paper_crud.update(
        db=db, db_obj=target_paper, obj_in=update_data, user=current_user
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
    conversations = sorted(conversations, key=lambda c: c.updated_at, reverse=True)  # type: ignore[arg-type]

    return [
        PaperConversationSummary(
            id=c.id,  # type: ignore[arg-type]
            title=c.title,  # type: ignore[arg-type]
            created_at=c.created_at,  # type: ignore[arg-type]
            updated_at=c.updated_at,  # type: ignore[arg-type]
        )
        for c in conversations
    ]


@paper_router.get("/{paper_id}/supplementary")
def list_supplementary_materials(
    paper_id: uuid.UUID,
    current_user: CurrentUser = Depends(get_required_user),
    db: Session = Depends(get_db),
) -> List[SupplementaryMaterialItem]:
    """List supplementary materials attached to a paper, plus any in-flight upload jobs."""
    parent = paper_crud.get(db, id=paper_id, user=current_user)
    if not parent:
        raise HTTPException(status_code=404, detail="Parent paper not found")

    # In-flight upload jobs targeting this parent
    in_flight_jobs = (
        db.query(PaperUploadJob)
        .filter(
            PaperUploadJob.supplementary_of_paper_id == paper_id,
            PaperUploadJob.user_id == current_user.id,
            PaperUploadJob.status.notin_([JobStatus.COMPLETED, JobStatus.FAILED]),
        )
        .all()
    )

    supplementary_papers = paper_crud.list_supplementary_for(
        db, parent_paper_id=paper_id, user=current_user
    )

    job_items = [
        SupplementaryMaterialItem(
            id=job.id,  # type: ignore[arg-type]
            created_at=job.started_at,  # type: ignore[arg-type]
            status=job.status,  # type: ignore[arg-type]
        )
        for job in in_flight_jobs
    ]

    paper_items = [
        SupplementaryMaterialItem(
            id=paper.id,  # type: ignore[arg-type]
            title=paper.title,  # type: ignore[arg-type]
            preview_url=paper.preview_url,  # type: ignore[arg-type]
            page_count=paper.page_count,  # type: ignore[arg-type]
            created_at=paper.created_at,  # type: ignore[arg-type]
            status=JobStatus.COMPLETED,
        )
        for paper in sorted(
            supplementary_papers,
            key=lambda p: p.created_at or datetime.min.replace(tzinfo=timezone.utc),  # type: ignore[arg-type]
        )
    ]

    return job_items + paper_items


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

    # Snapshot the row before the metadata refresh below writes to it; the
    # refreshed journal/publisher/date are reported through `overrides`.
    detail = PaperDetail.model_validate(paper)
    overrides: Dict[str, Any] = {}

    signed_url = s3_service.get_cached_presigned_url(
        db,
        paper_id=str(paper.id),
        object_key=str(paper.s3_object_key),
        current_user=current_user,
    )
    if not signed_url:
        raise HTTPException(status_code=404, detail="File not found")

    should_check_doi = (not paper.doi) and (paper.title is not None)
    is_cache_stale = (not paper.attempted_metadata_at) or (
        paper.attempted_metadata_at
        and (datetime.now(timezone.utc) - paper.attempted_metadata_at).days
        >= CHECK_METADATA_INTERVAL_DAYS
    )

    try:
        if should_check_doi and is_cache_stale:
            doi = get_doi(
                str(paper.title),
                list(paper.authors) if paper.authors else None,  # pyright: ignore[reportArgumentType]
            )  # type: ignore
            if doi:
                paper_crud.update(
                    db=db, db_obj=paper, obj_in=PaperUpdate(doi=doi), user=current_user
                )
            paper_crud.update(
                db=db,
                db_obj=paper,
                obj_in=PaperUpdate(attempted_metadata_at=datetime.now(timezone.utc)),
                user=current_user,
            )

        if paper.doi and (not paper.journal and not paper.publisher) and is_cache_stale:
            enriched_data = get_enriched_data(str(paper.doi))
            if enriched_data:
                publish_datetime = (
                    parse_publication_date(enriched_data.publication_date)
                    if enriched_data.publication_date
                    else paper.publish_date
                )
                overrides["journal"] = enriched_data.journal
                overrides["publisher"] = enriched_data.publisher
                overrides["publish_date"] = publish_datetime

                paper_crud.update(
                    db=db,
                    db_obj=paper,
                    obj_in=PaperUpdate(
                        journal=enriched_data.journal,
                        publisher=enriched_data.publisher,
                        publish_date=(
                            publish_datetime.isoformat() if publish_datetime else None
                        ),
                    ),
                    user=current_user,
                )
            paper_crud.update(
                db=db,
                db_obj=paper,
                obj_in=PaperUpdate(attempted_metadata_at=datetime.now(timezone.utc)),
                user=current_user,
            )
    except Exception:
        logger.exception("Error updating enriched data for paper %s", id, exc_info=True)

    overrides["file_url"] = signed_url
    return detail.model_copy(update=overrides)


@paper_router.get("/outline", response_model=List[OutlineEntry])
def get_paper_outline(
    id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_required_user),
):
    paper = paper_crud.get(db, id=id, user=current_user)
    if not paper:
        raise HTTPException(status_code=404, detail="Document not found")
    return cached_outline(db, paper)


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

    s3_object_key = paper.s3_object_key

    projects = project_paper_crud.get_projects_by_paper_id(
        db, paper_id=id, user=current_user
    )
    if len(projects) > 0:
        raise HTTPException(
            status_code=400,
            detail="Cannot delete document associated with projects. Please remove the document from all projects before deleting.",
        )

    # Delete the document from the database
    removed_paper = paper_crud.remove(db, id=id, user=current_user)
    if not removed_paper:
        raise HTTPException(status_code=500, detail="Failed to delete document")

    try:
        # Delete the file from S3 if s3_object_key exists
        if s3_object_key:
            s3_service.delete_file(str(s3_object_key))
            logger.info(f"Deleted S3 object: {s3_object_key}")

        # The paper_repos row goes with the paper (FK CASCADE), but the
        # ingested snapshot lives on a volume — drop it here.
        from app.llm.repo import storage as repo_storage

        repo_storage.delete_paper_snapshots(str(id))
    except Exception as e:
        logger.error(f"Error deleting document: {str(e)}")
        raise HTTPException(
            status_code=500, detail=f"Error deleting document: {str(e)}"
        )

    return MessageResponse(message="Document deleted")
