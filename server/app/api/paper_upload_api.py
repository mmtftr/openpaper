"""
Paper Upload API - Microservice Integration

This module handles PDF upload and processing by integrating with a separate
PDF processing microservice. The architecture is:

1. Client uploads PDF to this API
2. API creates a PaperUploadJob record with status 'pending'
3. API submits the PDF to the separate jobs service via Celery/HTTP
4. Jobs service processes PDF (S3 upload, metadata extraction, preview generation)
5. Jobs service sends results back via webhook
6. Webhook handler updates PaperUploadJob status and creates Paper record

The client can poll the job status using the same job_id throughout the process.
"""

import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional
from uuid import UUID

from app.api.webhook_api import handle_failed_upload
from app.auth.dependencies import get_required_user
from app.database.crud.paper_crud import paper_crud
from app.database.crud.paper_upload_crud import (
    PaperUploadJobCreate,
    PaperUploadJobUpdate,
    paper_upload_job_crud,
)
from app.database.database import get_db
from app.database.models import JobStatus, PaperUploadJob
from app.database.telemetry import track_event
from app.helpers.parser import validate_pdf_content, validate_url_and_fetch_pdf
from app.helpers.pdf_jobs import jobs_client
from app.schemas.paper import (
    UploadFromUrlRequest,
    UploadJobStatusResponse,
    UploadStartedResponse,
)
from app.schemas.user import CurrentUser
from dotenv import load_dotenv
from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    HTTPException,
    Request,
    UploadFile,
)
from sqlalchemy.orm import Session

load_dotenv()

logger = logging.getLogger(__name__)

# Create API router with prefix
paper_upload_router = APIRouter()


@paper_upload_router.get(
    "/status/{job_id}",
    response_model=UploadJobStatusResponse,
    # The celery_* keys are only sent while a Celery task is being tracked.
    response_model_exclude_unset=True,
)
def get_upload_status(
    job_id: UUID,
    current_user: CurrentUser = Depends(get_required_user),
    db: Session = Depends(get_db),
) -> UploadJobStatusResponse:
    """
    Get the status of a paper upload job, including real-time Celery task status.
    """
    paper_upload_job = paper_upload_job_crud.get(db=db, id=job_id, user=current_user)

    if not paper_upload_job:
        raise HTTPException(status_code=404, detail="Job not found")

    paper = paper_crud.get_by_upload_job_id(
        db=db, upload_job_id=str(paper_upload_job.id), user=current_user
    )

    if paper_upload_job.status == JobStatus.COMPLETED:
        # Verify the paper exists
        if not paper:
            raise HTTPException(status_code=404, detail="Paper not found")

    # Get real-time Celery task status if we have a task_id and job is still in progress
    # (completed/failed jobs no longer have active Celery tasks)
    celery_task_status = None
    if paper_upload_job.task_id and paper_upload_job.status not in (
        JobStatus.COMPLETED,
        JobStatus.FAILED,
    ):
        try:
            celery_task_status = jobs_client.check_celery_task_status(
                str(paper_upload_job.task_id)
            )
        except Exception as e:
            logger.warning(
                f"Failed to get Celery task status for {paper_upload_job.task_id}: {e}"
            )

    # If Celery reports failure, clean up and update the job status to match
    if (
        celery_task_status
        and celery_task_status.get("status", "").lower() == JobStatus.FAILED
    ):
        handle_failed_upload(
            db=db,
            job_id=str(paper_upload_job.id),
            job_user=current_user,
            reason=celery_task_status.get("error", "Celery task failed"),
        )

    # Build response with both job status and task status
    response_content: Dict[str, Any] = {
        "job_id": paper_upload_job.id,
        "status": paper_upload_job.status,
        "task_id": paper_upload_job.task_id,
        "started_at": paper_upload_job.started_at,
        "completed_at": paper_upload_job.completed_at,
        "has_file_url": bool(paper.file_url) if paper else False,
        "has_metadata": bool(paper.abstract) if paper else False,
        "paper_id": paper.id if paper else None,
    }

    # Add Celery task information if available
    if celery_task_status:
        response_content.update(
            {
                "celery_status": celery_task_status.get("status"),
                "celery_progress_message": celery_task_status.get("progress_message"),
                "celery_error": celery_task_status.get("error"),
            }
        )

    return UploadJobStatusResponse(**response_content)


@paper_upload_router.post("/from-url", status_code=202)
async def upload_pdf_from_url(
    request: UploadFromUrlRequest,
    background_tasks: BackgroundTasks,
    current_user: CurrentUser = Depends(get_required_user),
    db: Session = Depends(get_db),
    project_id: Optional[UUID] = None,
    supplementary_of: Optional[UUID] = None,
) -> UploadStartedResponse:
    """
    Upload a document from a given URL, rather than the raw file.
    """

    # If this is a supplementary upload, verify the parent paper belongs to
    # the user.
    if supplementary_of:
        parent_paper = paper_crud.get(db, id=supplementary_of, user=current_user)
        if not parent_paper:
            raise HTTPException(status_code=404, detail="Parent paper not found")

    # Validate the URL and fetch PDF content
    url = str(request.url)
    is_valid, pdf_bytes, error_message = await validate_url_and_fetch_pdf(url)
    if not is_valid:
        raise HTTPException(status_code=400, detail=error_message)

    # Create the paper upload job
    paper_upload_job_obj = PaperUploadJobCreate(
        started_at=datetime.now(timezone.utc),
        supplementary_of_paper_id=supplementary_of,
    )

    paper_upload_job: PaperUploadJob = paper_upload_job_crud.create(
        db=db,
        obj_in=paper_upload_job_obj,
        user=current_user,
    )

    if not paper_upload_job:
        raise HTTPException(
            status_code=500, detail="Failed to create paper upload job"
        )

    # Get filename from URL
    filename = url.split("/")[-1]

    # Pass file contents and filename instead of the UploadFile object
    background_tasks.add_task(
        upload_raw_file_microservice,
        file_contents=pdf_bytes,
        filename=filename,
        paper_upload_job=paper_upload_job,
        current_user=current_user,
        db=db,
        project_id=project_id,
    )

    return UploadStartedResponse(
        message="File upload started",
        job_id=paper_upload_job.id,  # type: ignore[arg-type]
    )


@paper_upload_router.post("", status_code=202)
async def upload_pdf(
    request: Request,
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    current_user: CurrentUser = Depends(get_required_user),
    db: Session = Depends(get_db),
    project_id: Optional[UUID] = None,
    supplementary_of: Optional[UUID] = None,
) -> UploadStartedResponse:
    """
    Upload a PDF file
    """
    # If this is a supplementary upload, verify the parent paper belongs to
    # the user.
    if supplementary_of:
        parent_paper = paper_crud.get(db, id=supplementary_of, user=current_user)
        if not parent_paper:
            raise HTTPException(status_code=404, detail="Parent paper not found")

    # Read the file contents BEFORE adding to background task. We need this because the UploadFile object becomes inaccessible after the request is processed.
    try:
        file_contents = await file.read()
        filename = file.filename
    except Exception as e:
        logger.error(f"Error reading uploaded file: {str(e)}", exc_info=True)
        raise HTTPException(status_code=400, detail="Error reading uploaded file")

    # Validate PDF content
    is_valid, error_message = await validate_pdf_content(file_contents, source="upload")
    if not is_valid:
        raise HTTPException(status_code=400, detail=error_message)

    # Create the paper upload job
    paper_upload_job_obj = PaperUploadJobCreate(
        started_at=datetime.now(timezone.utc),
        supplementary_of_paper_id=supplementary_of,
    )

    paper_upload_job: PaperUploadJob = paper_upload_job_crud.create(
        db=db,
        obj_in=paper_upload_job_obj,
        user=current_user,
    )

    if not paper_upload_job:
        raise HTTPException(
            status_code=500, detail="Failed to create paper upload job"
        )

    # Pass file contents and filename instead of the UploadFile object
    background_tasks.add_task(
        upload_raw_file_microservice,
        file_contents=file_contents,
        filename=str(filename),
        paper_upload_job=paper_upload_job,
        current_user=current_user,
        db=db,
        project_id=project_id,
    )

    return UploadStartedResponse(
        message="File upload started",
        job_id=paper_upload_job.id,  # type: ignore[arg-type]
    )


async def upload_raw_file_microservice(
    file_contents: bytes,
    filename: str,
    paper_upload_job: PaperUploadJob,
    current_user: CurrentUser,
    db: Session,
    project_id: Optional[UUID] = None,
) -> None:
    """
    Helper function to upload a raw file using the microservice.
    """

    paper_upload_job_crud.mark_as_running(
        db=db,
        job_id=str(paper_upload_job.id),
        user=current_user,
    )

    try:
        # Submit to microservice
        task_id = await jobs_client.submit_pdf_processing_job_with_upload(
            pdf_bytes=file_contents,
            paper_upload_job=paper_upload_job,
            db=db,
            user=current_user,
            project_id=project_id,
        )

        # Update job with task_id
        paper_upload_job_crud.update(
            db=db,
            db_obj=paper_upload_job,
            obj_in=PaperUploadJobUpdate(task_id=task_id),
            user=current_user,
        )

        # Track paper upload event
        track_event(
            "paper_upload_submitted_to_microservice",
            properties={
                "task_id": task_id,
            },
            user_id=str(current_user.id),
        )

    except Exception as e:
        logger.error(f"Error submitting file to microservice: {str(e)}", exc_info=True)
        paper_upload_job_crud.mark_as_failed(
            db=db,
            job_id=str(paper_upload_job.id),
            user=current_user,
        )
