"""The `Stage` contract every ingest stage implements (design §2, §4, §9).

A stage is one small class:

    class Figures(Stage[list[RenderedFigure]]):
        name = "figures"
        needs = ("ocr",)
        resource = Resource.CPU
        timeout_s = 300

        async def run(self, ctx: StageContext) -> list[RenderedFigure]:
            pages = await ctx.read(lambda s: load_pages(s, ctx.paper_id))
            ...                       # the work; no writes to the database
            return rendered

        def save(self, session, ctx, output) -> None:
            ...                       # write `output` (session.add / upsert)

The worker calls `run()` under the stage's time budget, then `save()` in the
SAME transaction that marks the stage `succeeded` and queues the dependents
that are now ready — so a stage's output and its status never disagree.
`save()` must not commit.

Control flow from inside `run()` (or `save()`):
- `skip(reason)` — the stage isn't needed: status `skipped`, dependents run.
- `fail_permanent(message)` — a retry can't help: status `failed` with that
  message, dependents `blocked`.
- anything else raised is sorted by `app.core.errors.classify`: temporary /
  rate-limited → requeued with backoff, permanent / config → `failed`.

`run()` must be safe to repeat: a crash or retry re-runs it from the top
(stages that save partial progress — OCR batches — skip what's saved).
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import (
    TYPE_CHECKING,
    Any,
    Awaitable,
    Callable,
    ClassVar,
    Iterator,
    NoReturn,
    Optional,
    TypeVar,
)

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.deadline import Deadline
from app.core.errors import ConfigError, PermanentError
from app.ingest.config import Resource

if TYPE_CHECKING:
    from app.helpers.s3 import S3Service
    from app.llm.model_slots import ResolvedSlot

T = TypeVar("T")

ProgressFn = Callable[[int, int], Awaitable[None]]
CpuRunner = Callable[..., Awaitable[Any]]


# -- control-flow exceptions --------------------------------------------------


class StageSkipped(Exception):
    """Raised by `skip()`: the stage is not needed for this paper."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class StageFailed(PermanentError):
    """Raised by `fail_permanent()`: fail now, no retry."""


def skip(reason: str) -> NoReturn:
    raise StageSkipped(reason)


def fail_permanent(message: str) -> NoReturn:
    raise StageFailed(message)


# -- context --------------------------------------------------------------------


async def _run_in_thread(fn: Callable[..., Any], *args: Any) -> Any:
    return await asyncio.to_thread(fn, *args)


def _default_session_factory() -> Session:
    from app.database.database import SessionLocal

    return SessionLocal()


@dataclass
class StageContext:
    """What a running stage gets from the worker.

    Built by the worker per attempt; tests build it directly with fakes.
    """

    paper_id: uuid.UUID
    stage: str
    attempt: int  # 1-based, this attempt
    is_supplementary: bool
    deadline: Deadline
    # Writes progress_done/progress_total to the stage row (the worker's).
    report_progress: Optional[ProgressFn] = None
    # Runs a picklable function in the worker's process pool (pymupdf work).
    # Defaults to a thread so tests and ad-hoc runs need no pool.
    run_cpu: CpuRunner = _run_in_thread
    session_factory: Callable[[], Session] = _default_session_factory
    s3: Optional["S3Service"] = None
    log: logging.LoggerAdapter = field(init=False)
    # Set by `Stage.resolve_model()` (or by the stage for non-slot models,
    # e.g. the OCR service); the worker stores it on the stage row.
    model_used: Optional[str] = None

    def __post_init__(self) -> None:
        self.log = logging.LoggerAdapter(
            logging.getLogger(f"app.ingest.stages.{self.stage}"),
            {"paper_id": str(self.paper_id), "stage": self.stage},
        )

    async def progress(self, done: int, total: int) -> None:
        """Report progress (e.g. pages OCR'd). Cheap; call per batch/page."""
        if self.report_progress is not None:
            await self.report_progress(done, total)

    @contextmanager
    def read_session(self) -> Iterator[Session]:
        """A READ ONLY transaction for loading inputs (sync code only)."""
        session = self.session_factory()
        try:
            session.execute(text("SET TRANSACTION READ ONLY"))
            yield session
        finally:
            session.rollback()
            session.close()

    async def read(self, fn: Callable[[Session], T]) -> T:
        """Run `fn(session)` on a read-only session off the event loop.

        Return plain values (dicts, dataclasses, detached rows' fields) —
        the session is closed when this returns.
        """

        def call() -> T:
            with self.read_session() as session:
                return fn(session)

        return await asyncio.to_thread(call)

    async def cpu(self, fn: Callable[..., T], *args: Any) -> T:
        """Run CPU-heavy `fn(*args)` off the event loop (process pool)."""
        return await self.run_cpu(fn, *args)

    def get_s3(self) -> "S3Service":
        if self.s3 is None:
            from app.helpers.s3 import s3_service

            self.s3 = s3_service
        return self.s3


# -- the stage ------------------------------------------------------------------


class Stage[Output]:
    """Base class. Subclasses set the class attributes and fill in
    `run()` / `save()`; the worker does everything else."""

    name: ClassVar[str]
    needs: ClassVar[tuple[str, ...]] = ()
    resource: ClassVar[Resource] = Resource.CPU
    timeout_s: ClassVar[float] = 300.0
    max_attempts: ClassVar[int] = 5
    # `app.llm.model_slots` slot for stages that call a chat model.
    model_slot: ClassVar[Optional[str]] = None
    applies_to_supplementary: ClassVar[bool] = False
    # Only `source`: run by the upload request itself, never by the worker.
    runs_in_request: ClassVar[bool] = False

    def check_config(self) -> None:
        """Raise `ConfigError` if this stage can't run as configured.

        The worker calls it before `run()`, so a missing key fails the stage
        immediately with a readable reason instead of after retries. The
        default checks the model slot resolves; override to add more (and
        call `super().check_config()`).
        """
        if self.model_slot is not None:
            self.resolve_model()

    def resolve_model(self, ctx: Optional[StageContext] = None) -> "ResolvedSlot":
        """The slot's current model; records it on `ctx.model_used`."""
        from app.llm.model_slots import resolve_slot

        if self.model_slot is None:
            raise ConfigError(f"Stage {self.name} has no model slot")
        try:
            resolved = resolve_slot(self.model_slot)
        except ValueError as exc:
            raise ConfigError(
                f"No model available for {self.model_slot}: {exc}"
            ) from exc
        if ctx is not None:
            ctx.model_used = f"{resolved.spec.provider.value}/{resolved.spec.id}"
        return resolved

    async def run(self, ctx: StageContext) -> Output:
        raise NotImplementedError(f"stage {self.name}: run() not implemented")

    def save(self, session: Session, ctx: StageContext, output: Output) -> None:
        """Write `output`. Runs inside the worker's transaction; don't commit."""
        raise NotImplementedError(f"stage {self.name}: save() not implemented")

    def reset_outputs(self, session: Session, paper_id: uuid.UUID) -> None:
        """Drop partial results a re-run would otherwise resume from.

        Called when the owner reprocesses this stage (or one upstream), in
        that request's transaction; don't commit. Plain retries don't call
        it, so they resume. Default: nothing — `save()` overwrites. OCR
        overrides it to forget its saved batches.
        """

    def __repr__(self) -> str:
        return f"<Stage {self.name}>"
