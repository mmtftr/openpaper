"""
FastAPI application for the Celery PDF processing service.
Provides endpoints for submitting tasks and checking status.
"""
import os
import tempfile

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import Dict, Any, Optional
import logging

import logfire

from src.celery_app import celery_app  # configures logfire as a side effect
from src.figure_renderer import render_and_upload_figures
from src.mistral_client import MistralOCRUnavailable, mistral_ocr_client
from src.parser_mistral import extract_with_mistral
from src.s3_service import s3_service

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="PDF Processing Service",
    description="Celery-based service for processing PDF files",
    version="1.0.0"
)


logfire.instrument_fastapi(app, capture_headers=True)


class TaskSubmission(BaseModel):
    pdf_base64: str
    webhook_url: str
    processing_options: Optional[Dict[str, Any]] = {}


class TaskResponse(BaseModel):
    task_id: str
    status: str
    message: str


class TaskStatus(BaseModel):
    task_id: str
    status: str
    result: Optional[Dict[str, Any]] = None
    meta: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    progress_message: Optional[str] = None  # Human-readable progress message


@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {"status": "healthy", "service": "pdf-processing"}


class OCRRunRequest(BaseModel):
    s3_object_key: str
    # Where to upload re-rendered figure bitmaps. The backfill script passes
    # `figures/{paper_id}` here so the keys are stable across re-runs.
    key_prefix: Optional[str] = None


class OCRRunResponse(BaseModel):
    success: bool
    parser: str
    ocr: Optional[Dict[str, Any]] = None
    raw_content: Optional[str] = None
    page_offset_map: Optional[Dict[int, list[int]]] = None
    figure_count: Optional[int] = None
    page_count: Optional[int] = None
    error: Optional[str] = None


@app.post("/ocr/run", response_model=OCRRunResponse)
async def run_ocr_sync(req: OCRRunRequest) -> OCRRunResponse:
    """Synchronous Mistral OCR + figure re-render against an existing PDF in S3.

    Used by the backfill script to re-OCR papers without going through the
    full Celery upload pipeline (which also re-runs LLM metadata extraction
    and overwrites the conversation). This endpoint just returns the OCR
    jsonb and the rendered figures map; the caller updates the DB.
    """
    if not mistral_ocr_client.is_configured:
        raise HTTPException(status_code=400, detail="MISTRAL_API_KEY not set on jobs service")

    try:
        pdf_bytes = s3_service.download_file_to_bytes(req.s3_object_key)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"S3 download failed: {e}")

    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
        tmp.write(pdf_bytes)
        tmp_path = tmp.name

    try:
        result = extract_with_mistral(tmp_path, client=mistral_ocr_client)
        result.ocr_jsonb = render_and_upload_figures(
            tmp_path,
            result.ocr_jsonb,
            job_id="backfill",
            key_prefix=req.key_prefix,
        )
        return OCRRunResponse(
            success=True,
            parser="mistral",
            ocr=result.ocr_jsonb,
            raw_content=result.raw_content,
            page_offset_map=result.page_offset_map,
            figure_count=result.figure_count,
            page_count=result.page_count,
        )
    except MistralOCRUnavailable as e:
        return OCRRunResponse(success=False, parser="mistral", error=str(e))
    except Exception as e:
        logger.exception("OCR run failed")
        return OCRRunResponse(success=False, parser="mistral", error=str(e))
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass

@app.get("/task/{task_id}/status", response_model=TaskStatus)
async def get_task_status(task_id: str):
    """
    Get the status of a processing task by ID.
    """
    try:
        # Get task result from Celery
        task_result = celery_app.AsyncResult(task_id) # type: ignore

        if task_result.state == "PENDING":
            # Task is waiting or doesn't exist
            status_response = TaskStatus(
                task_id=task_id,
                status="pending",
                meta={"message": "Task is pending or does not exist"}
            )
        elif task_result.state == "PROGRESS":
            # Task is in progress - extract progress details
            progress_info = task_result.info or {}
            status_response = TaskStatus(
                task_id=task_id,
                status="running",
                meta=progress_info,
                progress_message=progress_info.get("status", "Processing...")
            )
        elif task_result.state == "SUCCESS":
            # Task completed successfully
            status_response = TaskStatus(
                task_id=task_id,
                status="completed",
                result=task_result.result,
                meta={"completed_at": str(task_result.date_done)}
            )
        elif task_result.state == "FAILURE":
            # Task failed
            status_response = TaskStatus(
                task_id=task_id,
                status="failed",
                error=str(task_result.info),
                meta={"failed_at": str(task_result.date_done)}
            )
        else:
            # Unknown state
            status_response = TaskStatus(
                task_id=task_id,
                status=task_result.state.lower(),
                meta={"info": str(task_result.info)}
            )

        return status_response

    except Exception as e:
        logger.error(f"Failed to get task status for {task_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to get task status: {str(e)}")


@app.delete("/task/{task_id}")
async def cancel_task(task_id: str):
    """
    Cancel a pending or running task.
    """
    try:
        celery_app.control.revoke(task_id, terminate=True) # type: ignore
        logger.info(f"Cancelled task {task_id}")
        return {"message": f"Task {task_id} has been cancelled"}

    except Exception as e:
        logger.error(f"Failed to cancel task {task_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to cancel task: {str(e)}")


@app.get("/worker/status")
async def get_worker_status():
    """
    Get status of Celery workers.
    """
    try:
        # Inspect active tasks and worker status
        inspect = celery_app.control.inspect()

        active_tasks = inspect.active()
        registered_tasks = inspect.registered()
        worker_stats = inspect.stats()

        return {
            "active_tasks": active_tasks,
            "registered_tasks": registered_tasks,
            "worker_stats": worker_stats
        }

    except Exception as e:
        logger.error(f"Failed to get worker status: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to get worker status: {str(e)}")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
