"""Ingest progress API (design §8): the paper page polls this.

- `GET  /api/paper/{paper_id}/ingest` — every stage's status/progress/error,
  the feature map and whether the worker is alive.
- `POST /api/paper/{paper_id}/ingest/{stage}/retry` — retry a failed/blocked
  stage (for a blocked one, the failed stages upstream of it).
- `POST /api/paper/{paper_id}/ingest/{stage}/reprocess` — run a stage and
  everything downstream of it again.

Both POSTs return the updated status. A request that doesn't fit the stages'
current state (`IngestConflict`) is a 409 with the readable message.

Papers with no stage rows predate ingest v2 (legacy papers not yet migrated,
or uploads still handled by the old jobs pipeline). They report `legacy: true`
and every feature enabled, so nothing on the page is gated for them.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Callable, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.auth.dependencies import get_required_user
from app.core.errors import ErrorKind
from app.database.crud.paper_crud import paper_crud
from app.database.database import get_db
from app.ingest import graph
from app.ingest.features import FEATURES, FeatureState
from app.ingest.models import IngestStage, StageStatus
from app.ingest.service import IngestConflict, ingest_status, reprocess, retry_stage
from app.schemas.user import CurrentUser

ingest_router = APIRouter()


class IngestStageState(BaseModel):
    name: str
    label: str  # human-readable, e.g. "OCR repair"
    status: StageStatus
    attempt: int  # attempts started so far
    max_attempts: int
    # When a queued stage runs next (in the future while backing off).
    next_attempt_at: Optional[datetime] = None
    progress_done: Optional[int] = None
    progress_total: Optional[int] = None
    model_used: Optional[str] = None
    # Last failure (kept while a retry is pending), or the skip reason.
    error_message: Optional[str] = None
    error_kind: Optional[ErrorKind] = None
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None


class IngestFeatureState(BaseModel):
    enabled: bool
    # Needed stages not yet succeeded/skipped.
    waiting_on: list[str] = []
    # The stage to show / retry (a failed upstream stage first).
    cause: Optional[str] = None
    reason: Optional[str] = None


class IngestFeatures(BaseModel):
    """One entry per feature in `app.ingest.features.FEATURES`."""

    reading: IngestFeatureState
    manual_highlights: IngestFeatureState
    notes: IngestFeatureState
    metadata: IngestFeatureState  # title / authors / cite
    thumbnail: IngestFeatureState
    chat: IngestFeatureState
    figures: IngestFeatureState  # figures in chat and in the viewer
    citation_jump: IngestFeatureState
    outline: IngestFeatureState
    ai_highlights: IngestFeatureState


assert set(IngestFeatures.model_fields) == set(FEATURES), (
    "IngestFeatures must list every feature in app.ingest.features.FEATURES"
)


class IngestStatusResponse(BaseModel):
    stages: list[IngestStageState]  # topological order
    features: IngestFeatures
    # Something is still pending/queued/running: keep polling.
    active: bool
    worker_online: bool
    worker_last_seen: Optional[datetime] = None
    # No stage rows: processed before ingest v2; everything is enabled.
    legacy: bool


def _stage_state(row: IngestStage) -> IngestStageState:
    return IngestStageState(
        name=row.name,
        label=graph.LABELS.get(row.name, row.name),
        status=StageStatus(row.status),
        attempt=row.attempt,
        max_attempts=row.max_attempts,
        next_attempt_at=row.next_attempt_at,
        progress_done=row.progress_done,
        progress_total=row.progress_total,
        model_used=row.model_used,
        error_message=row.error_message,
        error_kind=ErrorKind(row.error_kind) if row.error_kind else None,
        started_at=row.started_at,
        finished_at=row.finished_at,
    )


def _feature_state(state: FeatureState) -> IngestFeatureState:
    return IngestFeatureState(
        enabled=state.enabled,
        waiting_on=list(state.waiting_on),
        cause=state.cause,
        reason=state.reason,
    )


def build_status(db: Session, paper_id: uuid.UUID) -> IngestStatusResponse:
    status = ingest_status(db, paper_id)
    legacy = not status.stages
    if legacy:
        feature_map = {name: IngestFeatureState(enabled=True) for name in FEATURES}
    else:
        feature_map = {
            name: _feature_state(state) for name, state in status.features.items()
        }
    return IngestStatusResponse(
        stages=[_stage_state(row) for row in status.stages],
        features=IngestFeatures(**feature_map),
        active=status.active,
        worker_online=status.worker_online,
        worker_last_seen=status.worker_last_seen,
        legacy=legacy,
    )


def _require_paper(db: Session, paper_id: uuid.UUID, user: CurrentUser) -> None:
    if not paper_crud.get(db, id=paper_id, user=user):
        raise HTTPException(status_code=404, detail="Paper not found")


def _require_stage(stage: str) -> None:
    if stage not in graph.NEEDS:
        raise HTTPException(status_code=404, detail=f"Unknown ingest step {stage!r}")


def _run(
    db: Session,
    paper_id: uuid.UUID,
    stage: str,
    operation: Callable[[Session, uuid.UUID, str], object],
) -> IngestStatusResponse:
    try:
        operation(db, paper_id, stage)
    except IngestConflict as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc))
    db.commit()
    return build_status(db, paper_id)


@ingest_router.get("/{paper_id}/ingest")
def get_ingest_status(
    paper_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_required_user),
) -> IngestStatusResponse:
    """Stage progress, feature map and worker liveness for one paper."""
    _require_paper(db, paper_id, current_user)
    return build_status(db, paper_id)


@ingest_router.post("/{paper_id}/ingest/{stage}/retry")
def retry_ingest_stage(
    paper_id: uuid.UUID,
    stage: str,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_required_user),
) -> IngestStatusResponse:
    """Retry a failed or blocked stage with a fresh attempt budget."""
    _require_stage(stage)
    _require_paper(db, paper_id, current_user)
    return _run(db, paper_id, stage, retry_stage)


@ingest_router.post("/{paper_id}/ingest/{stage}/reprocess")
def reprocess_ingest_stage(
    paper_id: uuid.UUID,
    stage: str,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_required_user),
) -> IngestStatusResponse:
    """Run a stage and everything downstream of it again from scratch."""
    _require_stage(stage)
    _require_paper(db, paper_id, current_user)
    return _run(db, paper_id, stage, reprocess)
