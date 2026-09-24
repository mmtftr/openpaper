import logging
import uuid
from datetime import datetime, timezone
from typing import List, Optional

from app.auth.dependencies import get_current_user, get_required_user
from app.database.crud.annotation_crud import annotation_crud
from app.database.crud.conversation_crud import conversation_crud
from app.database.crud.highlight_crud import highlight_crud
from app.database.crud.paper_crud import PaperUpdate, paper_crud
from app.database.crud.projects.project_paper_crud import project_paper_crud
from app.database.database import get_db
from app.database.models import JobStatus, Paper, PaperStatus, PaperUploadJob
from app.database.telemetry import track_event
from app.helpers.paper_search import get_doi, get_enriched_data
from app.helpers.parser import parse_publication_date
from app.helpers.s3 import s3_service
from app.helpers.subscription_limits import can_user_upload_paper
from app.llm.paper_outline import OutlineEntry, cached_outline
from app.schemas.responses import ResponseCitation
from app.schemas.user import CurrentUser
from dotenv import load_dotenv
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

load_dotenv()

logger = logging.getLogger(__name__)

# Create API router with prefix
paper_router = APIRouter()

CHECK_METADATA_INTERVAL_DAYS = 30


class SharePaperSchemaResponse(BaseModel):
    paper_data: dict
    highlight_data: dict
    annotations_data: dict


class UpdatePaperFieldsSchema(BaseModel):
    title: Optional[str] = None
    authors: Optional[List[str]] = None
    abstract: Optional[str] = None
    institutions: Optional[List[str]] = None
    keywords: Optional[List[str]] = None
    publish_date: Optional[str] = None
    doi: Optional[str] = None
    journal: Optional[str] = None
    publisher: Optional[str] = None


def _paper_markdown_payload(paper: Paper) -> dict:
    parser = str(getattr(paper, "parser", "") or "")
    if parser == "mistral":
        pages = (getattr(paper, "ocr", None) or {}).get("pages") or []
        markdown = "\n\n".join(
            str(page.get("markdown") or "").strip() for page in pages
        ).strip()
        return {"markdown": markdown, "source": "mistral"}

    return {"markdown": str(getattr(paper, "raw_content", "") or ""), "source": "pymupdf"}


@paper_router.get("/all")
async def get_paper_ids(
    db: Session = Depends(get_db),
    detailed: bool = False,
    current_user: CurrentUser = Depends(get_required_user),
):
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

    data = [
        {
            "id": str(paper.id),
            "title": paper.title,
            "created_at": str(paper.created_at),
            "abstract": paper.abstract,
            "authors": paper.authors,
            "institutions": paper.institutions,
            "keywords": paper.keywords,
            "status": paper.status,
            "preview_url": paper.preview_url,
            "size_in_kb": paper.size_in_kb,
            "publish_date": (str(paper.publish_date) if paper.publish_date else None),
            "file_url": file_urls.get(str(paper.id)),
            "tags": [{"id": str(tag.id), "name": tag.name, "color": tag.color} for tag in paper.tags],  # type: ignore
        }
        for paper in papers
    ]
    return JSONResponse(
        status_code=200,
        content={"papers": data},
    )


@paper_router.get("/active")
async def get_active_paper_ids(
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_required_user),
):
    """
    Get all active paper IDs
    """
    papers: List[Paper] = paper_crud.get_multi_uploads_completed(
        db, user=current_user, status=PaperStatus.reading
    )
    if not papers:
        return JSONResponse(
            status_code=404, content={"message": "No active papers found"}
        )

    data = [
        {
            "id": str(paper.id),
            "title": paper.title,
            "created_at": str(paper.created_at),
            "abstract": paper.abstract,
            "authors": paper.authors,
            "institutions": paper.institutions,
            "keywords": paper.keywords,
            "status": paper.status,
            "preview_url": paper.preview_url,
            "size_in_kb": paper.size_in_kb,
            "publish_date": (str(paper.publish_date) if paper.publish_date else None),
        }
        for paper in papers
    ]

    return JSONResponse(
        status_code=200,
        content={"papers": data},
    )


@paper_router.post("/status")
async def set_paper_status(
    paper_id: str,
    status: PaperStatus,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_required_user),
):
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
        db=db,
    )

    return JSONResponse(content=updated_paper.to_dict(), status_code=200)


@paper_router.patch("")
async def update_paper_fields(
    paper_id: str,
    request: UpdatePaperFieldsSchema,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_required_user),
):
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
        db=db,
    )

    return JSONResponse(content=updated_paper.to_dict(), status_code=200)


@paper_router.get("/relevant")
async def get_relevant_papers(
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_required_user),
):
    """
    Get the most relevant papers uploaded by the user
    """
    papers: List[Paper] = paper_crud.get_top_relevant_papers(db, user=current_user)
    if not papers:
        return JSONResponse(
            status_code=404, content={"message": "No relevant papers found"}
        )

    return JSONResponse(
        status_code=200,
        content={
            "papers": [
                {
                    "id": str(paper.id),
                    "title": paper.title,
                    "created_at": str(paper.created_at),
                    "abstract": paper.abstract,
                    "authors": paper.authors,
                    "institutions": paper.institutions,
                    "keywords": paper.keywords,
                    "status": paper.status,
                    "preview_url": paper.preview_url,
                    "size_in_kb": paper.size_in_kb,
                }
                for paper in papers
            ]
        },
    )


@paper_router.get("/conversations")
async def get_paper_conversations(
    paper_id: str,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_required_user),
) -> JSONResponse:
    """List every conversation tied to this paper (newest-updated first)."""
    casted_paper_id = uuid.UUID(paper_id)

    document = paper_crud.get(db, id=paper_id, user=current_user)
    if not document:
        return JSONResponse(status_code=404, content={"message": "Document not found"})

    conversations = conversation_crud.get_document_conversations(
        db, paper_id=casted_paper_id, current_user=current_user
    )
    conversations = sorted(conversations, key=lambda c: c.updated_at, reverse=True)  # type: ignore[arg-type]

    return JSONResponse(
        status_code=200,
        content=[
            {
                "id": str(c.id),
                "title": c.title,
                "created_at": c.created_at.isoformat() if c.created_at else None,  # type: ignore[union-attr]
                "updated_at": c.updated_at.isoformat() if c.updated_at else None,  # type: ignore[union-attr]
            }
            for c in conversations
        ],
    )


@paper_router.get("/{paper_id}/supplementary")
async def list_supplementary_materials(
    paper_id: str,
    current_user: CurrentUser = Depends(get_required_user),
    db: Session = Depends(get_db),
):
    """List supplementary materials attached to a paper, plus any in-flight upload jobs."""
    casted_paper_id = uuid.UUID(paper_id)

    parent = paper_crud.get(db, id=casted_paper_id, user=current_user)
    if not parent:
        return JSONResponse(
            status_code=404, content={"message": "Parent paper not found"}
        )

    # In-flight upload jobs targeting this parent
    in_flight_jobs = (
        db.query(PaperUploadJob)
        .filter(
            PaperUploadJob.supplementary_of_paper_id == casted_paper_id,
            PaperUploadJob.user_id == current_user.id,
            PaperUploadJob.status.notin_([JobStatus.COMPLETED, JobStatus.FAILED]),
        )
        .all()
    )

    supplementary_papers = paper_crud.list_supplementary_for(
        db, parent_paper_id=casted_paper_id, user=current_user
    )

    job_items = [
        {
            "id": str(job.id),
            "title": None,
            "preview_url": None,
            "page_count": None,
            "created_at": (
                job.started_at.isoformat() if job.started_at else None  # type: ignore[union-attr]
            ),
            "status": (
                job.status.value
                if hasattr(job.status, "value")
                else str(job.status)
            ),
        }
        for job in in_flight_jobs
    ]

    paper_items = [
        {
            "id": str(paper.id),
            "title": paper.title,
            "preview_url": paper.preview_url,
            "page_count": paper.page_count,
            "created_at": (
                paper.created_at.isoformat() if paper.created_at else None  # type: ignore[union-attr]
            ),
            "status": "completed",
        }
        for paper in sorted(
            supplementary_papers,
            key=lambda p: p.created_at or datetime.min.replace(tzinfo=timezone.utc),  # type: ignore[arg-type]
        )
    ]

    items = job_items + paper_items
    return JSONResponse(status_code=200, content=items)


@paper_router.get("")
async def get_pdf(
    request: Request,
    id: str,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_required_user),
):
    """
    Get a document by ID
    """
    # Fetch the document from the database
    paper = paper_crud.get(db, id=id, user=current_user, update_last_accessed=True)

    if not paper:
        return JSONResponse(status_code=404, content={"message": "Document not found"})

    paper_data = paper.to_dict()
    # `ocr` jsonb can be hundreds of KB on long papers — strip it from the
    # paper detail response. The chat layer reads it server-side; the client
    # only needs the parser flag and counts.
    paper_data.pop("ocr", None)

    signed_url = s3_service.get_cached_presigned_url(
        db,
        paper_id=str(paper.id),
        object_key=str(paper.s3_object_key),
        current_user=current_user,
    )
    if not signed_url:
        return JSONResponse(status_code=404, content={"message": "File not found"})

    should_check_doi = (not paper.doi) and (paper.title is not None)
    is_cache_stale = (not paper.attempted_metadata_at) or (
        paper.attempted_metadata_at
        and (datetime.now(timezone.utc) - paper.attempted_metadata_at).days
        >= CHECK_METADATA_INTERVAL_DAYS
    )

    try:
        if should_check_doi and is_cache_stale:
            doi = get_doi(str(paper.title), list(paper.authors) if paper.authors else None)  # type: ignore
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
                paper_data["journal"] = enriched_data.journal
                paper_data["publisher"] = enriched_data.publisher

                publish_datetime = (
                    parse_publication_date(enriched_data.publication_date)
                    if enriched_data.publication_date
                    else paper.publish_date
                )
                paper_data["publish_date"] = (
                    publish_datetime.isoformat() if publish_datetime else None
                )

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

    paper_data["file_url"] = signed_url

    paper_data["tags"] = [  # type: ignore
        {"id": str(t.id), "name": t.name, "color": t.color} for t in paper.tags  # type: ignore
    ]

    # Return the file URL
    return JSONResponse(status_code=200, content=paper_data)


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
async def get_paper_markdown(
    id: str,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_required_user),
):
    paper = paper_crud.get(db, id=id, user=current_user, update_last_accessed=True)
    if not paper:
        return JSONResponse(status_code=404, content={"message": "Document not found"})

    return JSONResponse(status_code=200, content=_paper_markdown_payload(paper))


@paper_router.post("/share")
async def share_pdf(
    request: Request,
    id: str,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_required_user),
):
    """
    Share a document by ID
    """
    # Fetch the document from the database
    paper = paper_crud.get(db, id=id, user=current_user)

    paper_crud.make_public(db, paper_id=id, user=current_user)
    if not paper:
        return JSONResponse(status_code=404, content={"message": "Document not found"})

    track_event(
        "paper_share",
        properties={
            "paper_id": str(paper.id),
            "share_id": paper.share_id,
        },
        user_id=str(current_user.id),
        db=db,
    )

    # Return the generated share id
    return JSONResponse(
        status_code=200,
        content={
            "message": "Document shared successfully",
            "share_id": paper.share_id,
        },
    )


@paper_router.post("/unshare")
async def unshare_pdf(
    request: Request,
    id: str,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_required_user),
):
    """
    Unshare a document by ID
    """
    # Fetch the document from the database
    paper = paper_crud.get(db, id=id, user=current_user)

    if not paper:
        return JSONResponse(status_code=404, content={"message": "Document not found"})

    paper_crud.make_private(db, paper_id=id, user=current_user)

    track_event(
        "paper_unshare",
        properties={
            "paper_id": str(paper.id),
            "share_id": paper.share_id,
        },
        user_id=str(current_user.id),
        db=db,
    )

    # Return the generated share id
    return JSONResponse(
        status_code=200,
        content={
            "message": "Document unshared successfully",
        },
    )


@paper_router.get("/share")
async def get_shared_pdf(
    request: Request,
    id: str,
    db: Session = Depends(get_db),
    current_user: Optional[CurrentUser] = Depends(get_current_user),
):
    """
    Get a shared document by ID
    """
    # Fetch the document from the database
    response = {}

    paper = paper_crud.get_public_paper(db, share_id=id)

    if not paper:
        return JSONResponse(status_code=404, content={"message": "Document not found"})

    paper_data = paper.to_dict()
    paper_data.pop("ocr", None)

    signed_url = s3_service.get_cached_presigned_url_by_owner(
        db,
        paper_id=str(paper.id),
        object_key=str(paper.s3_object_key),
        owner_id=str(paper.user_id),
    )
    if not signed_url:
        return JSONResponse(status_code=404, content={"message": "File not found"})

    annotations = annotation_crud.get_public_annotations_data_by_paper_id(
        db, share_id=uuid.UUID(id)
    )

    highlights = highlight_crud.get_public_highlights_data_by_paper_id(db, share_id=id)

    paper_data["file_url"] = signed_url
    response["paper"] = paper_data
    response["highlights"] = [highlight.to_dict() for highlight in highlights]
    response["annotations"] = [annotation.to_dict() for annotation in annotations]
    response["owner"] = {"name": paper.user.name, "picture": paper.user.picture, "id": str(paper.user.id)}  # type: ignore

    track_event(
        "paper_shared_view",
        properties={
            "paper_id": str(paper.id),
            "share_id": paper.share_id,
        },
        user_id=str(current_user.id) if current_user else None,
        db=db,
    )

    # Return the file URL
    return JSONResponse(status_code=200, content=response)


@paper_router.get("/share/markdown")
async def get_shared_paper_markdown(
    id: str,
    db: Session = Depends(get_db),
):
    paper = paper_crud.get_public_paper(db, share_id=id)
    if not paper:
        return JSONResponse(status_code=404, content={"message": "Document not found"})

    return JSONResponse(status_code=200, content=_paper_markdown_payload(paper))


@paper_router.delete("")
async def delete_pdf(
    request: Request,
    id: str,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_required_user),
):
    """
    Delete a document by ID
    """
    # Fetch the document from the database
    paper = paper_crud.get(db, id=id, user=current_user)

    if not paper:
        return JSONResponse(status_code=404, content={"message": "Document not found"})

    s3_object_key = paper.s3_object_key

    # Delete the document from the database
    try:
        projects = project_paper_crud.get_projects_by_paper_id(
            db, paper_id=uuid.UUID(id), user=current_user
        )

        if len(projects) > 0:
            return JSONResponse(
                status_code=400,
                content={
                    "message": "Cannot delete document associated with projects. Please remove the document from all projects before deleting."
                },
            )

        removed_paper = paper_crud.remove(db, id=id, user=current_user)
        if not removed_paper:
            return JSONResponse(
                status_code=500, content={"message": "Failed to delete document"}
            )

        # Delete the file from S3 if s3_object_key exists
        if s3_object_key:
            s3_service.delete_file(str(s3_object_key))
            logger.info(f"Deleted S3 object: {s3_object_key}")

        # The paper_repos row goes with the paper (FK CASCADE), but the
        # ingested snapshot lives on a volume — drop it here.
        from app.llm.repo import storage as repo_storage

        repo_storage.delete_paper_snapshots(id)

        return JSONResponse(status_code=200, content={"message": "Document deleted"})
    except Exception as e:
        logger.error(f"Error deleting document: {str(e)}")
        return JSONResponse(
            status_code=500,
            content={"message": f"Error deleting document: {str(e)}"},
        )


class ForkSharedPaperRequest(BaseModel):
    share_id: str


@paper_router.post("/fork")
async def fork_shared_paper(
    request: ForkSharedPaperRequest,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_required_user),
) -> JSONResponse:
    """
    Fork a shared paper into the current user's library.
    The paper must be publicly shared (via share_id).
    """
    try:
        # Check subscription limits before forking
        can_upload, error_message = can_user_upload_paper(db, current_user)
        if not can_upload:
            return JSONResponse(
                status_code=403,
                content={"message": error_message},
            )

        # Find the shared paper by share_id
        shared_paper = paper_crud.get_public_paper(db, share_id=request.share_id)

        if not shared_paper:
            raise HTTPException(
                status_code=404,
                detail="Shared paper not found or is no longer public.",
            )

        # Skip fork if user is the original owner
        if shared_paper.user_id == current_user.id:
            return JSONResponse(
                status_code=200,
                content={
                    "message": "You already own this paper",
                    "new_paper_id": str(shared_paper.id),
                    "already_exists": True,
                },
            )

        # Check if user already has a fork of this paper
        existing_fork = paper_crud.get_forked_paper_by_parent_id(
            db, parent_paper_id=uuid.UUID(str(shared_paper.id)), user=current_user
        )
        if existing_fork:
            return JSONResponse(
                status_code=200,
                content={
                    "message": "You already have this paper in your library",
                    "new_paper_id": str(existing_fork.id),
                    "already_exists": True,
                },
            )

        # Duplicate the file in S3
        duplicate_paper_key, duplicate_file_url = s3_service.duplicate_file(
            source_object_key=str(shared_paper.s3_object_key),
            new_filename=f"forked_{uuid.uuid4()}.pdf",
        )

        # Duplicate the preview image if it exists
        duplicate_preview_url = None
        if shared_paper.preview_url:
            _, duplicate_preview_url = s3_service.duplicate_file_from_url(
                s3_url=str(shared_paper.preview_url),
                new_filename=f"forked_preview_{uuid.uuid4()}.png",
            )

        # Fork the paper using paper_crud
        new_paper = paper_crud.fork_paper(
            db,
            original_paper=shared_paper,
            new_file_object_key=duplicate_paper_key,
            new_file_url=duplicate_file_url,
            new_preview_url=duplicate_preview_url,
            current_user=current_user,
        )

        if not new_paper:
            raise HTTPException(
                status_code=500,
                detail="Failed to fork paper.",
            )

        track_event(
            "paper_forked_from_share",
            user_id=str(current_user.id),
            properties={
                "share_id": request.share_id,
                "original_paper_id": str(shared_paper.id),
                "new_paper_id": str(new_paper.id),
            },
            db=db,
        )

        return JSONResponse(
            status_code=201,
            content={
                "message": "Paper forked successfully",
                "new_paper_id": str(new_paper.id),
            },
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error forking shared paper: {e}", exc_info=True)
        return JSONResponse(
            status_code=400,
            content={"message": "Failed to fork shared paper"},
        )
