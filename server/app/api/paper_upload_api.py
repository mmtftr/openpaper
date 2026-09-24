"""Paper uploads (docs/INGEST_DESIGN.md §2 `source`, §8).

The request itself runs the `source` stage: validate the PDF, store it at
`papers/{id}/{file name}.pdf`, create the `Paper` row and its
`ingest_stages` rows (source already succeeded) in one transaction, and
return the new paper's id. The PDF is readable as soon as this returns; the
ingest worker picks up the queued stages within a poll (≤ 250 ms).

- `POST /api/paper/upload` — multipart file.
- `POST /api/paper/upload/from-url` — the server downloads the PDF.

Both take `project_id` (add the paper to that project) or
`supplementary_of` (attach it to a parent paper as supplementary material).
"""

import asyncio
import logging
import uuid
from typing import Optional
from urllib.parse import unquote, urlparse

import httpx
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.auth.dependencies import get_required_user
from app.core.errors import PermanentError, TemporaryError
from app.core.http import make_client
from app.database.crud.paper_crud import paper_crud
from app.database.database import get_db
from app.database.models import Paper, Project, ProjectPaper
from app.database.telemetry import track_event
from app.helpers.s3 import s3_service
from app.ingest import service, storage
from app.ingest.pdf.document import InvalidPdfError
from app.ingest.stages.source import store_source
from app.schemas.paper import UploadedPaper, UploadFromUrlRequest
from app.schemas.user import CurrentUser
from app.settings import get_settings

logger = logging.getLogger(__name__)

paper_upload_router = APIRouter()

MAX_UPLOAD_SIZE_MB = get_settings().MAX_UPLOAD_SIZE_MB
MAX_UPLOAD_BYTES = MAX_UPLOAD_SIZE_MB * 1024 * 1024
URL_FETCH_TIMEOUT = httpx.Timeout(60.0, connect=10.0)


def _too_large() -> HTTPException:
    return HTTPException(
        status_code=400, detail=f"File too large (max {MAX_UPLOAD_SIZE_MB} MB)"
    )


# -- the shared flow ------------------------------------------------------------


def _check_targets(
    db: Session,
    user: CurrentUser,
    project_id: Optional[uuid.UUID],
    supplementary_of: Optional[uuid.UUID],
) -> None:
    """404 before anything is stored if the project / parent isn't the owner's."""
    if supplementary_of and not paper_crud.get(db, id=supplementary_of, user=user):
        raise HTTPException(status_code=404, detail="Parent paper not found")
    if project_id:
        project = (
            db.query(Project)
            .filter(Project.id == project_id, Project.owner_id == user.id)
            .first()
        )
        if project is None:
            raise HTTPException(status_code=404, detail="Project not found")


def _create_rows(
    db: Session,
    paper: Paper,
    project_id: Optional[uuid.UUID],
    is_supplementary: bool,
) -> None:
    """The paper, its project link and its stage rows, in one commit."""
    db.flush()  # the paper row first: the others reference it
    if project_id:
        db.add(ProjectPaper(paper_id=paper.id, project_id=project_id))
    service.enqueue_paper(
        db,
        paper.id,
        is_supplementary=is_supplementary,
        source_succeeded=True,
    )
    db.commit()


def _discard(db: Session, paper_id: uuid.UUID) -> None:
    db.rollback()
    try:
        storage.delete_paper_objects(s3_service, paper_id)
    except Exception:
        logger.exception("Could not delete the objects of failed upload %s", paper_id)


async def _ingest_upload(
    db: Session,
    user: CurrentUser,
    pdf_bytes: bytes,
    *,
    filename: Optional[str],
    source_url: Optional[str],
    project_id: Optional[uuid.UUID],
    supplementary_of: Optional[uuid.UUID],
) -> UploadedPaper:
    await asyncio.to_thread(_check_targets, db, user, project_id, supplementary_of)
    if len(pdf_bytes) > MAX_UPLOAD_BYTES:
        raise _too_large()

    paper_id = uuid.uuid4()
    paper = Paper(
        id=paper_id,
        user_id=user.id,
        supplementary_of_paper_id=supplementary_of,
        source_filename=filename or None,
        source_url=source_url,
    )
    try:
        await store_source(db, paper, pdf_bytes, filename)
    except InvalidPdfError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except (TemporaryError, PermanentError) as exc:
        logger.error("Storing upload %s failed: %s", paper_id, exc)
        raise HTTPException(
            status_code=503, detail="Couldn't store the PDF; try again"
        ) from exc

    try:
        await asyncio.to_thread(
            _create_rows, db, paper, project_id, supplementary_of is not None
        )
    except Exception as exc:
        logger.exception("Creating paper %s failed", paper_id)
        await asyncio.to_thread(_discard, db, paper_id)
        raise HTTPException(
            status_code=500, detail="Couldn't save the paper; try again"
        ) from exc

    track_event(
        "paper_upload",
        properties={
            "paper_id": str(paper_id),
            "size_in_kb": len(pdf_bytes) // 1024,
            "from_url": source_url is not None,
            "supplementary": supplementary_of is not None,
        },
        user_id=str(user.id),
    )
    return UploadedPaper(paper_id=paper_id)


# -- downloads -----------------------------------------------------------------


def _url_file_name(url: str) -> Optional[str]:
    name = unquote(urlparse(url).path.rstrip("/").rsplit("/", 1)[-1])
    return name or None


async def fetch_pdf(url: str) -> bytes:
    """Download `url`, capped at the upload size limit (400 on failure)."""
    try:
        async with make_client(
            timeout=URL_FETCH_TIMEOUT, headers={"Accept": "application/pdf, */*"}
        ) as client:
            async with client.stream("GET", url) as response:
                if response.status_code >= 400:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Failed to download the PDF: HTTP {response.status_code}",
                    )
                declared = response.headers.get("content-length")
                if declared and declared.isdigit() and int(declared) > MAX_UPLOAD_BYTES:
                    raise _too_large()
                chunks: list[bytes] = []
                total = 0
                async for chunk in response.aiter_bytes():
                    total += len(chunk)
                    if total > MAX_UPLOAD_BYTES:
                        raise _too_large()
                    chunks.append(chunk)
                return b"".join(chunks)
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=400, detail=f"Failed to download the PDF: {exc}"
        ) from exc


# -- routes --------------------------------------------------------------------


@paper_upload_router.post("/from-url", status_code=201)
async def upload_pdf_from_url(
    request: UploadFromUrlRequest,
    current_user: CurrentUser = Depends(get_required_user),
    db: Session = Depends(get_db),
    project_id: Optional[uuid.UUID] = None,
    supplementary_of: Optional[uuid.UUID] = None,
) -> UploadedPaper:
    """Import a PDF from a URL."""
    url = str(request.url)
    pdf_bytes = await fetch_pdf(url)
    return await _ingest_upload(
        db,
        current_user,
        pdf_bytes,
        filename=_url_file_name(url),
        source_url=url,
        project_id=project_id,
        supplementary_of=supplementary_of,
    )


@paper_upload_router.post("", status_code=201)
async def upload_pdf(
    file: UploadFile = File(...),
    current_user: CurrentUser = Depends(get_required_user),
    db: Session = Depends(get_db),
    project_id: Optional[uuid.UUID] = None,
    supplementary_of: Optional[uuid.UUID] = None,
) -> UploadedPaper:
    """Upload a PDF file."""
    if file.size is not None and file.size > MAX_UPLOAD_BYTES:
        raise _too_large()
    pdf_bytes = await file.read()
    return await _ingest_upload(
        db,
        current_user,
        pdf_bytes,
        filename=file.filename,
        source_url=None,
        project_id=project_id,
        supplementary_of=supplementary_of,
    )
