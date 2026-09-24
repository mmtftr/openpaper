"""
Webhook handlers for PDF processing service integration.
"""

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database.crud.paper_crud import PaperUpdate, paper_crud
from app.database.crud.paper_upload_crud import paper_upload_job_crud
from app.database.crud.projects.project_paper_crud import project_paper_crud
from app.database.database import get_db
from app.database.models import JobStatus
from app.database.telemetry import track_event
from app.helpers.paper_search import get_doi
from app.helpers.s3 import s3_service
from app.schemas.highlight import ScaledPosition
from app.schemas.responses import PaperMetadataExtraction
from app.schemas.user import CurrentUser

logger = logging.getLogger(__name__)

webhook_router = APIRouter()


def handle_failed_upload(
    db: Session, job_id: str, job_user: CurrentUser, reason: str = "Unknown error"
) -> None:
    """
    Handle cleanup for a failed paper upload job.

    Removes the paper record and any associated ProjectPaper relationships
    that were created during the upload process.

    Args:
        db: Database session
        job_id: The upload job ID
        job_user: The user who owns the job
        reason: Description of why the upload failed
    """
    logger.error(f"PDF processing failed for job {job_id}: {reason}")

    # Clean up the paper record that was created during upload
    existing_paper = paper_crud.get_by_upload_job_id(
        db=db, upload_job_id=job_id, user=job_user
    )
    if existing_paper:
        # First remove any ProjectPaper associations (RESTRICT constraint)
        projects = project_paper_crud.get_projects_by_paper_id(
            db=db, paper_id=uuid.UUID(str(existing_paper.id)), user=job_user
        )
        for project in projects:
            project_paper_crud.remove_by_paper_and_project(
                db=db,
                paper_id=uuid.UUID(str(existing_paper.id)),
                project_id=uuid.UUID(str(project.id)),
                user=job_user,
            )
            logger.info(
                f"Removed ProjectPaper association for paper {existing_paper.id} and project {project.id}"
            )

        logger.info(f"Removing failed paper {existing_paper.id} for job {job_id}")
        paper_crud.remove(db=db, id=str(existing_paper.id), user=job_user)

    paper_upload_job_crud.mark_as_failed(db=db, job_id=job_id, user=job_user)


class AIHighlightAnchor(BaseModel):
    """Where the jobs service anchored an AI highlight in the PDF
    (`jobs/src/highlight_anchor.py`)."""

    page_number: int
    position: ScaledPosition


class PDFProcessingResult(BaseModel):
    """Result of PDF processing"""

    success: bool
    job_id: str
    raw_content: Optional[str] = None
    page_offset_map: Optional[dict[int, list[int]]] = None
    metadata: Optional[PaperMetadataExtraction] = None
    # One entry per metadata highlight, None where PyMuPDF found no match.
    ai_highlight_anchors: Optional[list[Optional[AIHighlightAnchor]]] = None
    s3_object_key: Optional[str] = None
    file_url: Optional[str] = None
    preview_url: Optional[str] = None
    preview_object_key: Optional[str] = None
    error: Optional[str] = None
    duration: Optional[float] = None

    parser: Optional[str] = None
    ocr: Optional[Dict[str, Any]] = None
    figure_count: Optional[int] = None
    page_count: Optional[int] = None


class PdfProcessingWebhookData(BaseModel):
    """Schema for webhook data from PDF processing service"""

    task_id: str
    status: str
    # The jobs service sends `result: None` plus `error` when the task raised.
    result: Optional[PDFProcessingResult] = None
    error: Optional[str] = None


class WebhookStatusResponse(BaseModel):
    status: str


@webhook_router.post("/paper-processing/{job_id}")
def handle_paper_processing_webhook(
    job_id: str, webhook_data: PdfProcessingWebhookData, db: Session = Depends(get_db)
) -> WebhookStatusResponse:
    """Handle webhook from paper processing jobs service."""

    # Get the job from your database (without user filtering since this is a webhook)
    job = paper_upload_job_crud.get_by(db=db, task_id=webhook_data.task_id, id=job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    job_id = str(job.id)

    # Supplementary uploads attach to a parent paper and skip the
    # auto-generated conversation/annotations that normal uploads receive.
    is_supplementary = job.supplementary_of_paper_id is not None
    supplementary_of_paper_id = job.supplementary_of_paper_id

    if job.status == JobStatus.COMPLETED:
        logger.warning(f"Received webhook for already completed job {job_id}, ignoring")
        return WebhookStatusResponse(status="webhook ignored - job already completed")

    # Get the user object from the relationship
    user = job.user
    if not user:
        logger.error(f"No user found for job {job_id}")
        raise HTTPException(status_code=500, detail="User not found for job")

    job_user: CurrentUser = CurrentUser(
        id=user.id,
        email=user.email,
        name=user.name,
        is_admin=user.is_admin,
    )
    status = webhook_data.status
    result = webhook_data.result

    try:
        if status == "completed" and result is not None and result.success:
            # Processing was successful
            metadata = result.metadata
            file_url = result.file_url
            preview_url = result.preview_url

            if not file_url:
                logger.error(f"No file_url in webhook result for job {job_id}")
                handle_failed_upload(
                    db=db, job_id=job_id, job_user=job_user, reason="Missing file_url"
                )
                return WebhookStatusResponse(
                    status="webhook processed - failed due to missing file_url"
                )

            if not metadata:
                logger.error(f"No metadata in webhook result for job {job_id}")
                handle_failed_upload(
                    db=db, job_id=job_id, job_user=job_user, reason="Missing metadata"
                )
                return WebhookStatusResponse(
                    status="webhook processed - failed due to missing metadata"
                )

            if not result.raw_content:
                logger.error(f"No raw_content in webhook result for job {job_id}")
                handle_failed_upload(
                    db=db,
                    job_id=job_id,
                    job_user=job_user,
                    reason="Missing raw_content",
                )
                return WebhookStatusResponse(
                    status="webhook processed - failed due to missing raw_content"
                )

            if not metadata or not metadata.title:
                logger.error(f"No metadata in webhook result for job {job_id}")
                handle_failed_upload(
                    db=db, job_id=job_id, job_user=job_user, reason="Missing metadata"
                )
                return WebhookStatusResponse(
                    status="webhook processed - failed due to missing metadata"
                )

            if not result.raw_content:
                logger.error(f"No raw_content in webhook result for job {job_id}")
                handle_failed_upload(
                    db=db,
                    job_id=job_id,
                    job_user=job_user,
                    reason="Missing raw_content",
                )
                return WebhookStatusResponse(
                    status="webhook processed - failed due to missing raw_content"
                )

            size_in_kb = (
                s3_service.get_file_size_in_kb(result.s3_object_key)
                if result.s3_object_key
                else None
            )

            publish_date = metadata.publish_date if metadata.publish_date else None

            existing_paper = paper_crud.get_by_upload_job_id(
                db=db, upload_job_id=job_id, user=job_user
            )

            # A new parse means new headings: drop any outline built from the old one.
            if existing_paper is not None:
                existing_paper.generated_outline = None

            # Create paper record
            paper = paper_crud.update(
                db=db,
                obj_in=PaperUpdate(
                    upload_job_id=job_id,
                    preview_url=preview_url,
                    title=metadata.title,
                    authors=metadata.authors,
                    abstract=metadata.abstract,
                    keywords=metadata.keywords,
                    institutions=metadata.institutions,
                    publish_date=publish_date,
                    raw_content=result.raw_content,
                    page_offset_map=result.page_offset_map,
                    size_in_kb=size_in_kb,
                    parser=result.parser,
                    ocr=result.ocr,
                    figure_count=result.figure_count,
                    page_count=result.page_count,
                    supplementary_of_paper_id=supplementary_of_paper_id,
                ),
                db_obj=existing_paper,
                user=job_user,
            )

            # Supplementary uploads attach to a parent paper and don't get
            # auto-generated AI annotations or a dedicated conversation.
            if not is_supplementary:
                # Create highlights/annotations if any
                if metadata.highlights and paper:
                    try:
                        paper_crud.create_ai_annotations(
                            db=db,
                            paper_id=str(paper.id),
                            extract_metadata=metadata,
                            ai_highlight_anchors=(
                                [
                                    {
                                        "page_number": anchor.page_number,
                                        "position": anchor.position.to_json(),
                                    }
                                    if anchor
                                    else None
                                    for anchor in result.ai_highlight_anchors
                                ]
                                if result.ai_highlight_anchors is not None
                                else None
                            ),
                            current_user=job_user,
                        )
                    except Exception as e:
                        logger.error(
                            f"Error creating annotations for job {job_id}: {str(e)}",
                            exc_info=True,
                        )
                        # Don't fail the whole process for annotation errors

            # Post-processing: attempt to get DOI
            doi = get_doi(metadata.title, metadata.authors)

            if doi and paper:
                paper_crud.update(
                    db=db,
                    obj_in=PaperUpdate(doi=doi),
                    db_obj=paper,
                    user=job_user,
                )

            # Track metadata extraction event
            track_event(
                "extracted_metadata",
                properties={
                    "has_title": bool(metadata.title),
                    "has_authors": bool(metadata.authors),
                    "has_abstract": bool(metadata.abstract),
                    "has_ai_highlights": bool(metadata.highlights),
                    "has_doi": bool(doi),
                },
                user_id=str(user.id),
            )

            start_time = job.created_at
            end_time = datetime.now(timezone.utc)

            track_event(
                "paper_upload",
                properties={
                    "has_metadata": bool(metadata),
                    "duration": (end_time - start_time).total_seconds(),
                    "worker_duration": result.duration,
                },
                user_id=str(user.id),
            )

            # Mark job as completed
            paper_upload_job_crud.mark_as_completed(db=db, job_id=job_id, user=job_user)

        else:
            # Processing failed
            error_message = (
                (result.error if result is not None else None)
                or webhook_data.error
                or "Unknown error"
            )
            handle_failed_upload(
                db=db, job_id=job_id, job_user=job_user, reason=error_message
            )

    except Exception as e:
        logger.error(
            f"Error processing webhook for job {job_id}: {str(e)}", exc_info=True
        )

        # Clean up the paper record on exception as well
        try:
            handle_failed_upload(db=db, job_id=job_id, job_user=job_user, reason=str(e))
        except Exception as cleanup_error:
            logger.error(
                f"Failed to cleanup paper for job {job_id}: {str(cleanup_error)}"
            )
            # Still mark job as failed even if cleanup fails
            paper_upload_job_crud.mark_as_failed(db=db, job_id=job_id, user=job_user)

        raise HTTPException(status_code=500, detail="Error processing webhook")

    return WebhookStatusResponse(status="webhook processed")
