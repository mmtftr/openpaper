"""The ingest worker's scheduler (design §4).

One asyncio loop in one process:

1. Start: stages left `running` by a crash go back to `queued` (or `failed`
   when that was their last attempt); the heartbeat row is written and kept
   fresh.
2. Poll every `POLL_INTERVAL_SECONDS` — or right away when a stage finishes —
   for due `queued` rows, claim as many as each resource has free slots
   (`queued` → `running`, `attempt + 1`) and run each as an asyncio task.
3. A task runs `check_config()` then `run(ctx)` under the stage's time
   budget, and records the outcome in one transaction:
   - success: `save()` + `succeeded` + queue the dependents now ready;
   - `StageSkipped`: `skipped` (reason in `error_message`) + queue dependents;
   - error: `classify()` → retryable and attempts left: `queued` again at
     now + backoff (Retry-After aware); otherwise `failed` and the waiting
     downstream `blocked`.
4. `stop()` (SIGTERM): no new claims; running stages get `shutdown_grace`
   seconds to finish, the rest are cancelled and requeued without counting
   the interrupted attempt.

Database work is synchronous SQLAlchemy, run in threads so the loop stays
responsive. Every outcome is written only if the row is still `running` with
the attempt we claimed, so a late result can never overwrite a newer state.
"""

from __future__ import annotations

import asyncio
import functools
import logging
import multiprocessing
import os
import socket
import time
import uuid
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Callable, Mapping, Optional

from sqlalchemy import func, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.core.deadline import Deadline
from app.core.errors import TemporaryError, classify
from app.core.retry import STAGE_BACKOFF, BackoffPolicy, next_stage_delay
from app.database.models import Paper
from app.ingest import config
from app.ingest.config import Resource
from app.ingest.models import IngestStage, IngestWorker, StageStatus
from app.ingest.service import (
    StageClasses,
    block_dependents,
    lock_rows,
    mark_succeeded,
    queue_ready,
    stage_classes,
)
from app.ingest.stages.base import CpuRunner, Stage, StageContext, StageSkipped

logger = logging.getLogger(__name__)

SessionFactory = Callable[[], Session]

# Running stages get this long to finish after SIGTERM before being requeued.
SHUTDOWN_GRACE_SECONDS = 10.0
# Progress rows are written at most this often per stage (plus the last one).
PROGRESS_INTERVAL_SECONDS = 1.0

INTERRUPTED_MESSAGE = "Interrupted: the ingest worker stopped while this ran"


@dataclass(frozen=True)
class Claim:
    """A stage row this worker moved to `running`."""

    paper_id: uuid.UUID
    name: str
    attempt: int  # the attempt now running (1-based)
    max_attempts: int
    is_supplementary: bool


def _default_session_factory() -> Session:
    from app.database.database import SessionLocal

    return SessionLocal()


def _still_ours(row: Optional[IngestStage], claim: Claim) -> bool:
    return (
        row is not None
        and row.status is StageStatus.RUNNING
        and row.attempt == claim.attempt
    )


class _Progress:
    """`ctx.progress` for one attempt: remembers the latest value, writes the
    row at most every `interval` seconds (and always the final value)."""

    def __init__(self, engine: "Engine", claim: Claim) -> None:
        self._engine = engine
        self._claim = claim
        self._written_at = float("-inf")
        self.last: Optional[tuple[int, int]] = None

    async def report(self, done: int, total: int) -> None:
        self.last = (done, total)
        now = time.monotonic()
        if done < total and now - self._written_at < self._engine.progress_interval:
            return
        self._written_at = now
        try:
            await asyncio.to_thread(
                self._engine._write_progress, self._claim, done, total
            )
        except Exception:
            logger.warning("progress write failed for %s", self._claim, exc_info=True)


class Engine:
    def __init__(
        self,
        *,
        session_factory: SessionFactory = _default_session_factory,
        stages: Optional[StageClasses] = None,
        concurrency: Optional[Mapping[Resource, int]] = None,
        poll_interval: float = config.POLL_INTERVAL_SECONDS,
        heartbeat_interval: float = config.HEARTBEAT_INTERVAL_SECONDS,
        backoff: BackoffPolicy = STAGE_BACKOFF,
        shutdown_grace: float = SHUTDOWN_GRACE_SECONDS,
        progress_interval: float = PROGRESS_INTERVAL_SECONDS,
        cpu_runner: Optional[CpuRunner] = None,
    ) -> None:
        self.session_factory = session_factory
        self.stages = stage_classes(stages)
        self.concurrency = dict(concurrency or config.CONCURRENCY)
        self.poll_interval = poll_interval
        self.heartbeat_interval = heartbeat_interval
        self.backoff = backoff
        self.shutdown_grace = shutdown_grace
        self.progress_interval = progress_interval
        self._cpu_runner = cpu_runner
        self._pool: Optional[ProcessPoolExecutor] = None
        self._busy: dict[Resource, int] = {res: 0 for res in Resource}
        self._tasks: dict[asyncio.Task[None], Claim] = {}
        self._wake = asyncio.Event()
        self._stopping = asyncio.Event()

    # -- lifecycle -------------------------------------------------------------------

    def stop(self) -> None:
        """Ask `run()` to finish (signal-handler safe)."""
        self._stopping.set()
        self._wake.set()

    async def run(self) -> None:
        """Run until `stop()`; then drain running stages and return."""
        recovered = await asyncio.to_thread(self.recover)
        if recovered:
            logger.info("requeued %d stage(s) left running", recovered)
        await asyncio.to_thread(self._beat, True)
        heartbeat = asyncio.create_task(self._heartbeat_loop())
        logger.info(
            "ingest worker started (concurrency %s)",
            ", ".join(f"{res.value} {n}" for res, n in self.concurrency.items()),
        )
        try:
            while not self._stopping.is_set():
                self._wake.clear()
                try:
                    await self._fill()
                except Exception:
                    logger.exception("ingest poll failed")
                try:
                    await asyncio.wait_for(self._wake.wait(), self.poll_interval)
                except TimeoutError:
                    pass
        finally:
            heartbeat.cancel()
            await self._drain()
            if self._pool is not None:
                self._pool.shutdown(wait=False, cancel_futures=True)
                self._pool = None
            logger.info("ingest worker stopped")

    @property
    def running(self) -> list[Claim]:
        return list(self._tasks.values())

    # -- start-up recovery, heartbeat --------------------------------------------

    def recover(self) -> int:
        """Stages left `running` → `queued` (the lost attempt counts), or
        `failed` if it was the last one. Returns how many were touched."""
        with self.session_factory() as session:
            paper_ids = session.scalars(
                select(IngestStage.paper_id)
                .where(IngestStage.status == StageStatus.RUNNING)
                .distinct()
            ).all()
            touched = 0
            for paper_id in paper_ids:
                rows = lock_rows(session, paper_id)
                for name, row in rows.items():
                    if row.status is not StageStatus.RUNNING:
                        continue
                    touched += 1
                    row.error_message = INTERRUPTED_MESSAGE
                    row.error_kind = None
                    if row.attempt >= row.max_attempts:
                        row.status = StageStatus.FAILED
                        row.finished_at = func.now()
                        block_dependents(rows, name)
                    else:
                        row.status = StageStatus.QUEUED
                        row.next_attempt_at = func.now()
            session.commit()
            return touched

    def _beat(self, starting: bool = False) -> None:
        values: dict[str, Any] = {"id": 1, "last_seen": func.now()}
        if starting:
            values.update(
                started_at=func.now(), pid=os.getpid(), hostname=socket.gethostname()
            )
        stmt = pg_insert(IngestWorker).values(values)
        stmt = stmt.on_conflict_do_update(
            index_elements=["id"],
            set_={k: v for k, v in values.items() if k != "id"},
        )
        with self.session_factory() as session:
            session.execute(stmt)
            session.commit()

    async def _heartbeat_loop(self) -> None:
        while True:
            await asyncio.sleep(self.heartbeat_interval)
            try:
                await asyncio.to_thread(self._beat)
            except Exception:
                logger.warning("heartbeat failed", exc_info=True)

    # -- claiming ----------------------------------------------------------------------

    async def _fill(self) -> None:
        free = {
            res: limit - self._busy[res]
            for res, limit in self.concurrency.items()
            if limit - self._busy[res] > 0
        }
        if not free or self._stopping.is_set():
            return
        for claim in await asyncio.to_thread(self._claim, free):
            self._start(claim)

    def _names_for(self, resource: Resource) -> list[str]:
        return [
            name
            for name, cls in self.stages.items()
            if cls.resource == resource and not cls.runs_in_request
        ]

    def _claim(self, free: Mapping[Resource, int]) -> list[Claim]:
        """Due `queued` rows → `running`, up to `free[resource]` each."""
        claims: list[Claim] = []
        with self.session_factory() as session:
            for resource, slots in free.items():
                names = self._names_for(resource)
                if not names:
                    continue
                due = session.execute(
                    select(
                        IngestStage.paper_id,
                        IngestStage.name,
                        Paper.supplementary_of_paper_id,
                    )
                    .join(Paper, Paper.id == IngestStage.paper_id)
                    .where(
                        IngestStage.status == StageStatus.QUEUED,
                        or_(
                            IngestStage.next_attempt_at.is_(None),
                            IngestStage.next_attempt_at <= func.now(),
                        ),
                        IngestStage.name.in_(names),
                    )
                    .order_by(
                        IngestStage.next_attempt_at.asc().nulls_first(),
                        IngestStage.paper_id,
                        IngestStage.name,
                    )
                    .limit(slots)
                    .with_for_update(of=IngestStage, skip_locked=True)
                ).all()
                for paper_id, name, parent_id in due:
                    attempt, max_attempts = session.execute(
                        update(IngestStage)
                        .where(
                            IngestStage.paper_id == paper_id,
                            IngestStage.name == name,
                        )
                        .values(
                            status=StageStatus.RUNNING,
                            attempt=IngestStage.attempt + 1,
                            started_at=func.now(),
                            finished_at=None,
                            next_attempt_at=None,
                        )
                        .returning(IngestStage.attempt, IngestStage.max_attempts)
                    ).one()
                    claims.append(
                        Claim(
                            paper_id=paper_id,
                            name=name,
                            attempt=attempt,
                            max_attempts=max_attempts,
                            is_supplementary=parent_id is not None,
                        )
                    )
            session.commit()
        return claims

    def _start(self, claim: Claim) -> None:
        stage = self.stages[claim.name]()
        resource = stage.resource
        self._busy[resource] += 1
        task = asyncio.create_task(
            self._execute(claim, stage), name=f"ingest:{claim.name}:{claim.paper_id}"
        )
        self._tasks[task] = claim

        def done(t: asyncio.Task[None]) -> None:
            self._busy[resource] -= 1
            self._tasks.pop(t, None)
            if not t.cancelled() and t.exception() is not None:
                logger.error("ingest task crashed: %s", claim, exc_info=t.exception())
            self._wake.set()  # a slot is free and dependents may be queued

        task.add_done_callback(done)

    # -- running one stage ------------------------------------------------------------

    async def _run_cpu(self, fn: Callable[..., Any], *args: Any) -> Any:
        if self._cpu_runner is not None:
            return await self._cpu_runner(fn, *args)
        if self._pool is None:
            # spawn: forking a process that holds DB connections and threads
            # is unsafe.
            self._pool = ProcessPoolExecutor(
                max_workers=self.concurrency.get(Resource.CPU, 2),
                mp_context=multiprocessing.get_context("spawn"),
            )
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(self._pool, functools.partial(fn, *args))

    async def _execute(self, claim: Claim, stage: Stage[Any]) -> None:
        progress = _Progress(self, claim)
        ctx = StageContext(
            paper_id=claim.paper_id,
            stage=claim.name,
            attempt=claim.attempt,
            is_supplementary=claim.is_supplementary,
            deadline=Deadline(stage.timeout_s),
            report_progress=progress.report,
            run_cpu=self._run_cpu,
            session_factory=self.session_factory,
        )
        ctx.log.info("start (attempt %d/%d)", claim.attempt, claim.max_attempts)
        started = time.monotonic()
        budget = asyncio.timeout(stage.timeout_s)
        try:
            await asyncio.to_thread(stage.check_config)
            async with budget:
                output = await stage.run(ctx)
            await asyncio.to_thread(self._succeed, claim, stage, ctx, output, progress)
            ctx.log.info("succeeded in %.1fs", time.monotonic() - started)
        except StageSkipped as skipped:
            await asyncio.to_thread(self._skip, claim, skipped.reason)
            ctx.log.info("skipped: %s", skipped.reason)
        except Exception as exc:
            if isinstance(exc, TimeoutError) and budget.expired():
                exc = TemporaryError(f"Timed out after {stage.timeout_s:g} s")
            await asyncio.to_thread(self._fail, claim, exc, progress)

    def _succeed(
        self,
        claim: Claim,
        stage: Stage[Any],
        ctx: StageContext,
        output: Any,
        progress: _Progress,
    ) -> None:
        with self.session_factory() as session:
            if not _still_ours(
                lock_rows(session, claim.paper_id).get(claim.name), claim
            ):
                logger.warning("dropping result of %s: row changed meanwhile", claim)
                return
            stage.save(session, ctx, output)
            # Re-read: save() may have changed other rows (metadata marks
            # metadata_fallback skipped).
            rows = lock_rows(session, claim.paper_id)
            mark_succeeded(
                rows[claim.name], model_used=ctx.model_used, progress=progress.last
            )
            queued = queue_ready(rows, self.stages)
            session.commit()
        if queued:
            logger.debug("%s done; queued %s", claim, queued)

    def _skip(self, claim: Claim, reason: str) -> None:
        with self.session_factory() as session:
            rows = lock_rows(session, claim.paper_id)
            row = rows.get(claim.name)
            if not _still_ours(row, claim):
                return
            assert row is not None
            row.status = StageStatus.SKIPPED
            row.finished_at = func.now()
            row.error_message = reason
            row.error_kind = None
            queue_ready(rows, self.stages)
            session.commit()

    def _fail(self, claim: Claim, exc: BaseException, progress: _Progress) -> None:
        classified = classify(exc)
        delay = next_stage_delay(
            classified, claim.attempt, claim.max_attempts, self.backoff
        )
        with self.session_factory() as session:
            rows = lock_rows(session, claim.paper_id)
            row = rows.get(claim.name)
            if not _still_ours(row, claim):
                return
            assert row is not None
            row.error_kind = classified.kind
            if progress.last is not None:
                row.progress_done, row.progress_total = progress.last
            if delay is not None:
                row.status = StageStatus.QUEUED
                row.next_attempt_at = func.now() + timedelta(seconds=delay)
                row.error_message = classified.message
                logger.info(
                    "%s/%s attempt %d failed (%s), retry in %.0fs: %s",
                    claim.paper_id,
                    claim.name,
                    claim.attempt,
                    classified.kind.value,
                    delay,
                    classified.message,
                )
            else:
                row.status = StageStatus.FAILED
                row.finished_at = func.now()
                attempts = f"{claim.attempt} attempt{'s' if claim.attempt != 1 else ''}"
                row.error_message = (
                    f"{classified.message} (gave up after {attempts})"
                    if classified.retryable
                    else classified.message
                )
                blocked = block_dependents(rows, claim.name)
                logger.warning(
                    "%s/%s failed (%s): %s; blocked %s",
                    claim.paper_id,
                    claim.name,
                    classified.kind.value,
                    classified.message,
                    blocked,
                )
            session.commit()

    def _write_progress(self, claim: Claim, done: int, total: int) -> None:
        with self.session_factory() as session:
            session.execute(
                update(IngestStage)
                .where(
                    IngestStage.paper_id == claim.paper_id,
                    IngestStage.name == claim.name,
                    IngestStage.status == StageStatus.RUNNING,
                    IngestStage.attempt == claim.attempt,
                )
                .values(progress_done=done, progress_total=total)
            )
            session.commit()

    # -- shutdown ---------------------------------------------------------------------

    async def _drain(self) -> None:
        if not self._tasks:
            return
        claims = dict(self._tasks)
        logger.info(
            "waiting up to %.0fs for %d stage(s)", self.shutdown_grace, len(claims)
        )
        _, pending = await asyncio.wait(list(claims), timeout=self.shutdown_grace)
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        interrupted = [claims[t] for t in pending]
        if interrupted:
            await asyncio.to_thread(self._requeue, interrupted)
            logger.info("requeued %d interrupted stage(s)", len(interrupted))

    def _requeue(self, claims: list[Claim]) -> None:
        """Interrupted by our own shutdown: back to `queued`, attempt not counted."""
        with self.session_factory() as session:
            for claim in claims:
                session.execute(
                    update(IngestStage)
                    .where(
                        IngestStage.paper_id == claim.paper_id,
                        IngestStage.name == claim.name,
                        IngestStage.status == StageStatus.RUNNING,
                        IngestStage.attempt == claim.attempt,
                    )
                    .values(
                        status=StageStatus.QUEUED,
                        attempt=IngestStage.attempt - 1,
                        next_attempt_at=func.now(),
                    )
                )
            session.commit()
