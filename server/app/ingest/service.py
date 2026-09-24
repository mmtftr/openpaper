"""Ingest operations the rest of the server calls (design §4, §8).

- `enqueue_paper` — create a new paper's stage rows and queue what can run.
- `complete_stage` — record a stage the request ran itself (`source`).
- `retry_stage` — Retry button on a failed/blocked stage.
- `reprocess` — re-run a stage and everything downstream of it.
- `ingest_status` — stage rows + feature map + worker liveness for the API.

None of these commit: they run inside the caller's transaction (the upload
request creates the paper and its stage rows together). The worker notices
newly queued stages on its next poll (≤ 250 ms).

State rules shared with the worker (`engine.py`) live here too:
- a waiting row (`pending`/`blocked`) is `queued` as soon as every need is
  `succeeded`/`skipped` (`queue_ready`); stages that run in the request
  (`runs_in_request`) are never queued for the worker;
- a waiting row is `blocked` exactly when some stage upstream of it is
  `failed` (`refresh_blocked`), otherwise `pending`.
All transitions lock the paper's stage rows first (`lock_rows`), so the API
and the worker never interleave on one paper.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable, Mapping, Optional

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.ingest import graph
from app.ingest.features import FeatureState, features
from app.ingest.models import (
    WORKER_STALE_AFTER_SECONDS,
    IngestStage,
    IngestWorker,
    StageStatus,
)
from app.ingest.stages.base import Stage

# Stage name -> class. Defaults to `registry.STAGE_CLASSES`; tests inject fakes.
StageClasses = Mapping[str, type[Stage]]

# The only stage the upload request runs itself.
SOURCE = "source"


class IngestConflict(Exception):
    """The operation doesn't fit the stages' current state (shown to the user)."""


def stage_classes(stages: Optional[StageClasses] = None) -> StageClasses:
    if stages is not None:
        return stages
    from app.ingest.registry import STAGE_CLASSES

    return STAGE_CLASSES


# -- row helpers (shared with the worker) ----------------------------------------


def lock_rows(session: Session, paper_id: uuid.UUID) -> dict[str, IngestStage]:
    """The paper's stage rows, locked `FOR UPDATE` and freshly loaded.

    Flushes first: the reload would otherwise discard pending changes.
    """
    session.flush()
    rows = session.scalars(
        select(IngestStage)
        .where(IngestStage.paper_id == paper_id)
        .order_by(IngestStage.name)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).all()
    return {row.name: row for row in rows}


def statuses_of(rows: Mapping[str, IngestStage]) -> dict[str, StageStatus]:
    return {name: StageStatus(row.status) for name, row in rows.items()}


def queue_ready(rows: Mapping[str, IngestStage], stages: StageClasses) -> list[str]:
    """Queue every waiting row whose needs are done; returns their names."""
    queued: list[str] = []
    for name in graph.ready_after(statuses_of(rows)):
        cls = stages.get(name)
        if cls is None or cls.runs_in_request:
            continue
        row = rows[name]
        row.status = StageStatus.QUEUED
        row.next_attempt_at = func.now()
        queued.append(name)
    return queued


def refresh_blocked(rows: Mapping[str, IngestStage]) -> None:
    """Waiting rows: `blocked` if something upstream failed, else `pending`."""
    statuses = statuses_of(rows)
    for name, row in rows.items():
        if statuses[name] not in graph.WAITING_STATUSES:
            continue
        failed_upstream = any(
            statuses.get(up) is StageStatus.FAILED for up in graph.upstream_of(name)
        )
        row.status = StageStatus.BLOCKED if failed_upstream else StageStatus.PENDING


def block_dependents(rows: Mapping[str, IngestStage], failed: str) -> list[str]:
    """Mark the waiting downstream of the failed stage `blocked`."""
    names = graph.blocked_by(failed, statuses_of(rows))
    for name in names:
        rows[name].status = StageStatus.BLOCKED
    return names


def mark_succeeded(
    row: IngestStage,
    *,
    model_used: Optional[str] = None,
    progress: Optional[tuple[int, int]] = None,
) -> None:
    row.status = StageStatus.SUCCEEDED
    row.finished_at = func.now()
    row.next_attempt_at = None
    row.error_message = None
    row.error_kind = None
    if model_used is not None:
        row.model_used = model_used
    if progress is not None:
        row.progress_done, row.progress_total = progress


def reset_row(row: IngestStage, *, keep_progress: bool) -> None:
    """Back to `pending` with a fresh attempt budget."""
    row.status = StageStatus.PENDING
    row.attempt = 0
    row.next_attempt_at = None
    row.error_message = None
    row.error_kind = None
    row.started_at = None
    row.finished_at = None
    if not keep_progress:
        row.progress_done = None
        row.progress_total = None
        row.model_used = None


def _row(rows: Mapping[str, IngestStage], name: str) -> IngestStage:
    if name not in graph.NEEDS:
        raise IngestConflict(f"Unknown ingest step {name!r}")
    row = rows.get(name)
    if row is None:
        raise IngestConflict(f"This document has no {graph.LABELS[name]} step")
    return row


def _labels(names: Iterable[str]) -> str:
    return ", ".join(graph.LABELS[n] for n in names)


# -- operations ------------------------------------------------------------------------


def enqueue_paper(
    session: Session,
    paper_id: uuid.UUID,
    *,
    is_supplementary: bool,
    source_succeeded: bool = False,
    stages: Optional[StageClasses] = None,
) -> list[str]:
    """Create the paper's stage rows and queue the ones that can run.

    Supplementary materials get only `graph.SUPPLEMENTARY_STAGES`. Pass
    `source_succeeded=True` when the upload request already ran `source`
    (the normal case); otherwise `source` stays `pending` until
    `complete_stage(..., "source")`. Rows that already exist are left alone,
    so calling this twice is harmless. Returns the stages queued.
    """
    classes = stage_classes(stages)
    values = [
        {
            "paper_id": paper_id,
            "name": name,
            "status": StageStatus.PENDING.value,
            "attempt": 0,
            "max_attempts": classes[name].max_attempts,
        }
        for name in graph.stages_for(is_supplementary)
    ]
    session.execute(
        pg_insert(IngestStage)
        .values(values)
        .on_conflict_do_nothing(index_elements=["paper_id", "name"])
    )
    rows = lock_rows(session, paper_id)
    if source_succeeded and not graph.is_done(rows[SOURCE].status):
        mark_succeeded(rows[SOURCE])
    return queue_ready(rows, classes)


def complete_stage(
    session: Session,
    paper_id: uuid.UUID,
    name: str,
    *,
    model_used: Optional[str] = None,
    stages: Optional[StageClasses] = None,
) -> list[str]:
    """Mark a stage the caller ran itself succeeded and queue what's ready."""
    rows = lock_rows(session, paper_id)
    mark_succeeded(_row(rows, name), model_used=model_used)
    return queue_ready(rows, stage_classes(stages))


def retry_stage(
    session: Session,
    paper_id: uuid.UUID,
    name: str,
    *,
    stages: Optional[StageClasses] = None,
) -> list[str]:
    """Retry a `failed` or `blocked` stage with a fresh attempt budget.

    For a blocked stage, the failed stages upstream of it are what actually
    get retried. Blocked stages downstream go back to `pending` (they run
    when their needs succeed). Returns the stages queued.
    """
    classes = stage_classes(stages)
    rows = lock_rows(session, paper_id)
    row = _row(rows, name)
    status = StageStatus(row.status)
    if status not in (StageStatus.FAILED, StageStatus.BLOCKED):
        raise IngestConflict(
            f"{graph.LABELS[name]} is {status.value}; only failed or blocked"
            " steps can be retried"
        )
    targets = [
        n
        for n in (*graph.upstream_of(name), name)
        if n in rows and rows[n].status in (StageStatus.FAILED, StageStatus.BLOCKED)
    ]
    in_request = [n for n in targets if classes[n].runs_in_request]
    if in_request:
        raise IngestConflict(
            f"{_labels(in_request)} runs during upload; upload the file again"
        )
    for n in targets:
        # Keep progress: OCR resumes from its saved batches.
        reset_row(rows[n], keep_progress=True)
    refresh_blocked(rows)
    return queue_ready(rows, classes)


def reprocess(
    session: Session,
    paper_id: uuid.UUID,
    name: str,
    *,
    stages: Optional[StageClasses] = None,
) -> list[str]:
    """Run `name` and everything downstream of it again from scratch.

    Refused while any of those stages is running. Each reset stage's
    `reset_outputs()` drops partial results a re-run would otherwise resume
    from. `source` itself runs in the upload request, so reprocessing it
    keeps it and re-runs everything after it. Returns the stages queued.
    """
    classes = stage_classes(stages)
    rows = lock_rows(session, paper_id)
    _row(rows, name)
    subgraph = [n for n in (name, *graph.downstream_of(name)) if n in rows]
    running = [n for n in subgraph if rows[n].status is StageStatus.RUNNING]
    if running:
        raise IngestConflict(
            f"Can't reprocess {graph.LABELS[name]} while {_labels(running)}"
            f" {'is' if len(running) == 1 else 'are'} running"
        )
    for n in subgraph:
        if classes[n].runs_in_request:
            continue
        classes[n]().reset_outputs(session, paper_id)
        reset_row(rows[n], keep_progress=False)
    refresh_blocked(rows)
    return queue_ready(rows, classes)


@dataclass(frozen=True)
class IngestStatus:
    stages: list[IngestStage]  # topological order
    features: dict[str, FeatureState]
    # Anything still pending/queued/running (the client keeps polling).
    active: bool
    worker_online: bool
    worker_last_seen: Optional[datetime]


def ingest_status(session: Session, paper_id: uuid.UUID) -> IngestStatus:
    """Everything `GET /api/papers/{id}/ingest` returns, read-only."""
    rows = {
        row.name: row
        for row in session.scalars(
            select(IngestStage).where(IngestStage.paper_id == paper_id)
        )
    }
    ordered = [rows[name] for name in graph.topo_sorted(rows)]
    active = any(
        row.status in (StageStatus.PENDING, StageStatus.QUEUED, StageStatus.RUNNING)
        for row in ordered
    )
    worker = session.get(IngestWorker, 1)
    last_seen = worker.last_seen if worker is not None else None
    online = (
        last_seen is not None
        and (datetime.now(timezone.utc) - last_seen).total_seconds()
        < WORKER_STALE_AFTER_SECONDS
    )
    return IngestStatus(
        stages=ordered,
        features=features(rows),
        active=active,
        worker_online=online,
        worker_last_seen=last_seen,
    )
