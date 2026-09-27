"""Paper uploads (docs/INGEST_DESIGN.md §2 `source`, §8).

The request itself runs the `source` stage: validate the PDF, store it at
`papers/{id}/{file name}.pdf`, create the `Paper` row and its
`ingest_stages` rows (source already succeeded) in one transaction, and
return the new paper's id. The PDF is readable as soon as this returns; the
ingest worker picks up the queued stages within a poll (≤ 250 ms).

- `POST /api/paper/upload` — multipart file.
- `POST /api/paper/upload/from-url` — the server downloads the PDF.
- `POST /api/paper/upload/import` — a pasted link (arXiv abs/pdf page,
  `arXiv:ID`, or a direct PDF URL): resolved to the PDF, downloaded, and
  deduplicated against the library (an arXiv paper already imported, or
  the same URL, returns the existing paper instead).

The first two take `project_id` (add the paper to that project) or
`supplementary_of` (attach it to a parent paper as supplementary material);
`import` takes `project_id`.
"""

import asyncio
import logging
import re
import uuid
from typing import Optional
from urllib.parse import unquote, urlparse

import httpx
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy import func, or_
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
from app.ingest.metadata_ids import ArxivLink, arxiv_link
from app.ingest.pdf.document import InvalidPdfError
from app.ingest.stages.source import store_source
from app.schemas.paper import (
    ImportedPaper,
    ImportPaperRequest,
    UploadedPaper,
    UploadFromUrlRequest,
)
from app.schemas.user import CurrentUser
from app.settings import get_settings

logger = logging.getLogger(__name__)

paper_upload_router = APIRouter()

MAX_UPLOAD_SIZE_MB = get_settings().MAX_UPLOAD_SIZE_MB
MAX_UPLOAD_BYTES = MAX_UPLOAD_SIZE_MB * 1024 * 1024
URL_FETCH_TIMEOUT = httpx.Timeout(60.0, connect=10.0)
URL_FETCH_TOTAL_S = 120.0  # the whole download, however slowly it trickles
# PDF readers accept the header anywhere in the first KiB.
PDF_MAGIC = b"%PDF-"
PDF_MAGIC_WINDOW = 1024


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


def _not_a_pdf(content_type: Optional[str]) -> HTTPException:
    got = f" (got {content_type.split(';')[0]})" if content_type else ""
    return HTTPException(status_code=400, detail=f"That link didn't return a PDF{got}")


async def fetch_pdf(url: str) -> bytes:
    """Download `url`, capped at the upload size limit (400 on failure).

    Rejects anything that doesn't start like a PDF (an HTML landing page,
    a login wall) as soon as the first KiB is in.
    """
    try:
        async with asyncio.timeout(URL_FETCH_TOTAL_S):
            return await _download_pdf(url)
    except TimeoutError as exc:
        raise HTTPException(
            status_code=400, detail="Downloading the PDF timed out"
        ) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=400, detail=f"Failed to download the PDF: {exc}"
        ) from exc


async def _download_pdf(url: str) -> bytes:
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
            content_type = response.headers.get("content-type")
            chunks: list[bytes] = []
            total = 0
            checked = False
            async for chunk in response.aiter_bytes():
                total += len(chunk)
                if total > MAX_UPLOAD_BYTES:
                    raise _too_large()
                chunks.append(chunk)
                if not checked and total >= PDF_MAGIC_WINDOW:
                    head = b"".join(chunks)[:PDF_MAGIC_WINDOW]
                    if PDF_MAGIC not in head:
                        raise _not_a_pdf(content_type)
                    checked = True
            body = b"".join(chunks)
            if not checked and PDF_MAGIC not in body[:PDF_MAGIC_WINDOW]:
                raise _not_a_pdf(content_type)
            return body


def _resolve_pdf_url(text: str) -> tuple[str, Optional[ArxivLink]]:
    """The PDF URL a pasted link stands for (arXiv pages → their PDF)."""
    link = arxiv_link(text)
    if link is not None:
        return link.pdf_url, link
    return text.strip(), None


# -- dedupe ----------------------------------------------------------------------


def _find_existing(
    db: Session, user: CurrentUser, url: str, link: Optional[ArxivLink]
) -> Optional[Paper]:
    """The owner's library paper this link was already imported as: the same
    arXiv id (any version; recorded by ingest or in the URL it came from),
    else the same source URL."""
    query = db.query(Paper).filter(
        Paper.user_id == user.id, Paper.supplementary_of_paper_id.is_(None)
    )
    if link is not None:
        from_arxiv = re.escape(link.id)
        match = or_(
            func.lower(Paper.arxiv_id) == link.id.lower(),
            Paper.source_url.regexp_match(
                rf"^https?://([a-z0-9-]+\.)*arxiv\.org/(abs|pdf)/{from_arxiv}"
                r"(v[0-9]+)?(\.pdf)?/?$",
                flags="i",
            ),
        )
    else:
        match = Paper.source_url == url
    return query.filter(match).order_by(Paper.created_at).first()


def _reuse_existing(db: Session, paper: Paper, project_id: Optional[uuid.UUID]) -> None:
    """Bring an archived paper back and link it to `project_id` if asked."""
    if paper.archived_at is not None:
        paper.archived_at = None
    if project_id:
        linked = (
            db.query(ProjectPaper)
            .filter_by(paper_id=paper.id, project_id=project_id)
            .first()
        )
        if linked is None:
            db.add(ProjectPaper(paper_id=paper.id, project_id=project_id))
    db.commit()


# -- routes --------------------------------------------------------------------


@paper_upload_router.post("/from-url", status_code=201)
async def upload_pdf_from_url(
    request: UploadFromUrlRequest,
    current_user: CurrentUser = Depends(get_required_user),
    db: Session = Depends(get_db),
    project_id: Optional[uuid.UUID] = None,
    supplementary_of: Optional[uuid.UUID] = None,
) -> UploadedPaper:
    """Import a PDF from a URL (arXiv pages resolve to their PDF)."""
    url, _ = _resolve_pdf_url(str(request.url))
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


@paper_upload_router.post("/import", status_code=201)
async def import_paper(
    request: ImportPaperRequest,
    current_user: CurrentUser = Depends(get_required_user),
    db: Session = Depends(get_db),
    project_id: Optional[uuid.UUID] = None,
) -> ImportedPaper:
    """Import a pasted link: an arXiv page / id, or a direct PDF URL.

    If the owner already has that paper, returns it (`existing: true`,
    unarchived and linked to `project_id`) instead of importing it again.
    """
    url, link = _resolve_pdf_url(request.url)
    if link is None and urlparse(url).scheme not in ("http", "https"):
        raise HTTPException(status_code=400, detail="Paste an arXiv link or a PDF URL")
    await asyncio.to_thread(_check_targets, db, current_user, project_id, None)
    existing = await asyncio.to_thread(_find_existing, db, current_user, url, link)
    if existing is not None:
        await asyncio.to_thread(_reuse_existing, db, existing, project_id)
        return ImportedPaper(
            paper_id=existing.id,
            existing=True,
            title=existing.title,
            arxiv_id=link.id if link else existing.arxiv_id,
        )

    pdf_bytes = await fetch_pdf(url)
    uploaded = await _ingest_upload(
        db,
        current_user,
        pdf_bytes,
        filename=_url_file_name(url),
        source_url=url,
        project_id=project_id,
        supplementary_of=None,
    )
    return ImportedPaper(
        paper_id=uploaded.paper_id,
        existing=False,
        arxiv_id=link.id if link else None,
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
