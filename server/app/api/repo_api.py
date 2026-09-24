"""Companion-repo endpoints: connect, status, disconnect, browse.

Auth is paper ownership on every route (the `paper_repos` row has no
`user_id` of its own — the paper is the ownership anchor).

Ingestion runs in a FastAPI `BackgroundTask` on a FRESH session — never the
request session, which FastAPI closes as soon as the response is sent. No
Celery: a single tarball fetch + prune is seconds of work, and the status
column already carries the state a poller needs.

The tree/file endpoints serve OUR snapshot, not GitHub: the user sees the
exact bytes the agent read, and private-repo support stays possible later.
"""

import logging
import threading
import uuid
from typing import List, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.auth.dependencies import get_required_user
from app.database.crud.paper_crud import paper_crud
from app.database.crud.paper_repo_crud import (
    PaperRepoCreate,
    is_stale_ingest,
    paper_repo_crud,
)
from app.database.database import get_db
from app.database.models import PaperRepo, RepoStatus
from app.database.telemetry import track_event
from app.llm.repo import storage
from app.llm.repo.ingest import (
    IngestError,
    github_blob_url,
    ingest_repo,
    parse_repo_url,
)
from app.schemas.json_datetime import IsoDatetime
from app.schemas.user import CurrentUser

logger = logging.getLogger(__name__)

repo_router = APIRouter()

# Files past this size are served as a truncated preview: the viewer is for
# reading code, not for shipping a megabyte into the browser.
MAX_FILE_RESPONSE_BYTES = 512 * 1024

# Concurrent ingestions per worker process. Each one holds an anyio
# threadpool slot for the duration of a multi-MB download + extraction.
MAX_CONCURRENT_INGESTS = 2
_ingest_slots = threading.Semaphore(MAX_CONCURRENT_INGESTS)


class ConnectRepoRequest(BaseModel):
    url: str


class RepoStatusResponse(BaseModel):
    status: RepoStatus
    owner: str
    repo: str
    ref: str
    commit_sha: Optional[str] = None
    error: Optional[str] = None
    file_count: Optional[int] = None
    total_bytes: Optional[int] = None
    updated_at: Optional[IsoDatetime] = None


class RepoTreeFile(BaseModel):
    path: str
    size: int


class RepoTreeResponse(BaseModel):
    owner: str
    repo: str
    ref: str
    commit_sha: str
    files: List[RepoTreeFile]


class RepoFileResponse(BaseModel):
    path: str
    content: str
    size: int
    github_url: str


def _serialize(row: PaperRepo) -> RepoStatusResponse:
    return RepoStatusResponse(
        status=str(row.status or RepoStatus.PENDING.value),
        owner=str(row.owner or ""),
        repo=str(row.repo or ""),
        ref=str(row.ref or ""),
        commit_sha=str(row.commit_sha) if row.commit_sha else None,
        error=str(row.error) if row.error else None,
        file_count=int(row.file_count) if row.file_count is not None else None,
        total_bytes=int(row.total_bytes) if row.total_bytes is not None else None,
        updated_at=row.updated_at,  # type: ignore[arg-type]
    )


def _require_paper(db: Session, paper_id: uuid.UUID, current_user: CurrentUser):
    paper = paper_crud.get(db, id=paper_id, user=current_user)
    if not paper:
        raise HTTPException(status_code=404, detail="Paper not found")
    return paper, paper_id


def _require_ready_repo(db: Session, paper_uuid: uuid.UUID) -> PaperRepo:
    row = paper_repo_crud.get_by_paper_id(db, paper_id=paper_uuid)
    if not row:
        raise HTTPException(status_code=404, detail="No repository connected")
    if str(row.status) != RepoStatus.READY.value or not row.commit_sha:
        raise HTTPException(
            status_code=409, detail=f"Repository is not ready (status={row.status})"
        )
    return row


def _mark_checked(session: Session, row: PaperRepo, **fields) -> None:
    """Write a status transition, retrying once and logging on failure.

    `CRUDBase.update` swallows exceptions and returns None, so an unchecked
    call can silently strand a row in `ingesting` and leave the UI polling
    for fifteen minutes.
    """
    for attempt in (1, 2):
        if paper_repo_crud.mark(session, row=row, **fields) is not None:
            return
        logger.warning(
            "Repo status write failed (attempt %d) for row %s: %s",
            attempt, getattr(row, "id", "?"), fields.get("status"),
        )
        try:
            session.rollback()
        except Exception:
            pass
    logger.error(
        "Giving up on repo status write for row %s (%s)",
        getattr(row, "id", "?"), fields.get("status"),
    )


def run_ingestion(paper_id: str, url: str) -> None:
    """Background ingestion on its own session.

    The row is claimed with a conditional UPDATE, so if two POSTs race only
    one job proceeds. Every exit path writes a terminal status, so a poller
    never hangs on `ingesting` forever (and a crash mid-run is recovered by
    the staleness rule on the next POST).
    """
    from app.database.database import SessionLocal

    session = SessionLocal()
    try:
        existing = paper_repo_crud.get_by_paper_id(
            db=session, paper_id=uuid.UUID(paper_id)
        )
        if existing is None:
            logger.warning("Repo row vanished before ingestion: paper=%s", paper_id)
            return
        row = paper_repo_crud.claim_for_ingestion(session, row_id=existing.id)
        if row is None:
            logger.info(
                "Repo ingestion for paper %s already claimed elsewhere", paper_id
            )
            return

        try:
            # Bound how many repos this worker ingests at once: each job
            # holds a threadpool slot, downloads up to 200 MB and writes up
            # to 100 MB to the volume.
            with _ingest_slots:
                result = ingest_repo(paper_id=paper_id, url=url)
        except IngestError as exc:
            logger.info("Repo ingestion failed for paper %s: %s", paper_id, exc)
            _mark_checked(session, row, status=RepoStatus.ERROR.value, error=str(exc))
            return
        except Exception as exc:
            logger.error(
                "Unexpected repo ingestion failure for paper %s: %s",
                paper_id, exc, exc_info=True,
            )
            _mark_checked(
                session,
                row,
                status=RepoStatus.ERROR.value,
                error=f"Unexpected ingestion failure ({type(exc).__name__}).",
            )
            return

        # The row may have been deleted while we were ingesting (DELETE on a
        # stale `ingesting` row, or the paper itself going away). Publishing
        # a snapshot no row references would orphan it forever — there is no
        # eviction sweeper by design.
        session.expire_all()
        still_there = paper_repo_crud.get_by_paper_id(
            db=session, paper_id=uuid.UUID(paper_id)
        )
        if still_there is None:
            logger.info(
                "Repo row for paper %s disappeared during ingestion; "
                "discarding the snapshot", paper_id,
            )
            storage.delete_paper_snapshots(paper_id)
            return
        if still_there.id != row.id:
            # Superseded: a disconnect + reconnect created a new row while
            # this (stale) job was still running. The new row owns its own
            # snapshot — drop only ours, never the whole paper directory.
            logger.info(
                "Repo row for paper %s was replaced during ingestion; "
                "discarding this job's snapshot", paper_id,
            )
            storage.delete_snapshot(paper_id, result.commit_sha)
            return

        _mark_checked(
            session,
            still_there,
            status=RepoStatus.READY.value,
            owner=result.owner,
            repo=result.repo,
            ref=result.ref,
            commit_sha=result.commit_sha,
            file_count=result.file_count,
            total_bytes=result.total_bytes,
            storage_prefix=result.storage_prefix,
            error=None,
        )
        # Only the confirmed owner prunes older snapshots (see `ingest_repo`).
        try:
            storage.prune_other_snapshots(paper_id, result.commit_sha)
        except storage.SnapshotPathError as exc:
            logger.warning("Skipping snapshot prune for paper %s: %s", paper_id, exc)
        logger.info(
            "Repo ready for paper %s: %s/%s@%s (%d files)",
            paper_id, result.owner, result.repo, result.commit_sha[:8],
            result.file_count,
        )
    finally:
        session.close()


@repo_router.get("/{paper_id}/repo")
def get_repo(
    paper_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_required_user),
) -> RepoStatusResponse:
    """Current repo row for a paper (404 when nothing is connected)."""
    _, paper_uuid = _require_paper(db, paper_id, current_user)
    row = paper_repo_crud.get_by_paper_id(db, paper_id=paper_uuid)
    if not row:
        raise HTTPException(status_code=404, detail="No repository connected")
    return _serialize(row)


@repo_router.post("/{paper_id}/repo", status_code=202)
def connect_repo(
    paper_id: uuid.UUID,
    body: ConnectRepoRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_required_user),
) -> RepoStatusResponse:
    """Connect a public GitHub repo and kick ingestion.

    Re-POSTing recovers a failed or stranded ingestion; a fresh `ingesting`
    or a `ready` row is a 409 (disconnect first to change repos).
    """
    _, paper_uuid = _require_paper(db, paper_id, current_user)
    try:
        ref = parse_repo_url(body.url)
    except IngestError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    row = paper_repo_crud.get_by_paper_id(db, paper_id=paper_uuid)
    if row is not None:
        status = str(row.status)
        if status == RepoStatus.READY.value:
            raise HTTPException(
                status_code=409,
                detail="A repository is already connected. Disconnect it first.",
            )
        # `pending` counts as in-flight: the background task for the first
        # POST has been scheduled but may not have claimed the row yet.
        if status in (RepoStatus.INGESTING.value, RepoStatus.PENDING.value) and (
            not is_stale_ingest(row)
        ):
            raise HTTPException(
                status_code=409, detail="A repository ingestion is already running."
            )
        updated = paper_repo_crud.mark(
            db,
            row=row,
            user=current_user,
            owner=ref.owner,
            repo=ref.repo,
            status=RepoStatus.PENDING.value,
            error=None,
            commit_sha=None,
            file_count=None,
            total_bytes=None,
            storage_prefix=None,
        )
        row = updated or row
    else:
        row = paper_repo_crud.create(
            db,
            obj_in=PaperRepoCreate(
                paper_id=paper_uuid,
                owner=ref.owner,
                repo=ref.repo,
                status=RepoStatus.PENDING.value,
            ),
            user=current_user,
        )
        if row is None:
            raise HTTPException(
                status_code=500, detail="Failed to create the repository record"
            )

    background_tasks.add_task(run_ingestion, str(paper_uuid), body.url)
    track_event(
        "repo_connected",
        properties={"paper_id": str(paper_uuid), "repo": ref.slug},
        user_id=str(current_user.id),
    )
    return _serialize(row)


@repo_router.delete("/{paper_id}/repo", status_code=204)
def disconnect_repo(
    paper_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_required_user),
) -> None:
    """Drop the row and every snapshot on disk for this paper."""
    _, paper_uuid = _require_paper(db, paper_id, current_user)
    row = paper_repo_crud.get_by_paper_id(db, paper_id=paper_uuid)
    if not row:
        raise HTTPException(status_code=404, detail="No repository connected")
    # `pending` is in-flight too: deleting then would let the background job
    # publish a snapshot that no row references (and nothing evicts).
    if str(row.status) in (
        RepoStatus.INGESTING.value,
        RepoStatus.PENDING.value,
    ) and not is_stale_ingest(row):
        raise HTTPException(
            status_code=409,
            detail="Ingestion is running — wait for it to finish before disconnecting.",
        )
    paper_repo_crud.remove(db, id=row.id)
    storage.delete_paper_snapshots(str(paper_uuid))
    track_event(
        "repo_disconnected",
        properties={"paper_id": str(paper_uuid)},
        user_id=str(current_user.id),
    )
    return None


@repo_router.get("/{paper_id}/repo/tree")
def get_repo_tree(
    paper_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_required_user),
) -> RepoTreeResponse:
    """The snapshot manifest: every ingested path with its size."""
    _, paper_uuid = _require_paper(db, paper_id, current_user)
    row = _require_ready_repo(db, paper_uuid)
    manifest = storage.load_manifest(str(paper_uuid), str(row.commit_sha))
    if not manifest:
        raise HTTPException(status_code=404, detail="Repository snapshot is missing")
    files = [
        RepoTreeFile(path=str(entry.get("path")), size=int(entry.get("size") or 0))
        for entry in manifest.get("files") or []
        if isinstance(entry, dict) and entry.get("path")
    ]
    return RepoTreeResponse(
        owner=str(row.owner or ""),
        repo=str(row.repo or ""),
        ref=str(row.ref or ""),
        commit_sha=str(row.commit_sha or ""),
        files=files,
    )


@repo_router.get("/{paper_id}/repo/file")
def get_repo_file(
    paper_id: uuid.UUID,
    path: str,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_required_user),
) -> RepoFileResponse:
    """One file's contents from the snapshot.

    Traversal defense is two-layered: the resolved path must stay inside the
    snapshot root AND the requested path must appear in the manifest.
    """
    _, paper_uuid = _require_paper(db, paper_id, current_user)
    row = _require_ready_repo(db, paper_uuid)
    manifest = storage.load_manifest(str(paper_uuid), str(row.commit_sha))
    if not manifest:
        raise HTTPException(status_code=404, detail="Repository snapshot is missing")

    requested = str(path or "").strip().lstrip("/")
    known = set(storage.manifest_paths(manifest))
    if requested not in known:
        raise HTTPException(status_code=404, detail="File not found in this snapshot")

    try:
        root = storage.tree_dir(str(paper_uuid), str(row.commit_sha))
        host_path = storage.resolve_within(root, requested)
    except storage.SnapshotPathError:
        raise HTTPException(status_code=404, detail="File not found in this snapshot")

    try:
        raw = host_path.read_bytes()
    except OSError:
        raise HTTPException(status_code=404, detail="File not found in this snapshot")

    size = len(raw)
    content = raw[:MAX_FILE_RESPONSE_BYTES].decode("utf-8", errors="replace")
    if size > MAX_FILE_RESPONSE_BYTES:
        content += "\n… [file truncated for display]"

    return RepoFileResponse(
        path=requested,
        content=content,
        size=size,
        github_url=github_blob_url(
            str(row.owner or ""), str(row.repo or ""), str(row.commit_sha or ""),
            requested,
        ),
    )
