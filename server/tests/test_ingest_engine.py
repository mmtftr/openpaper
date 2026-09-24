"""Ingest worker engine + service against a disposable Postgres.

Fake stages (one per real graph name, behaviour scripted per test) run the
real graph through the real `Engine`, so this checks scheduling and state
transitions, not stage bodies.

Postgres: `INGEST_TEST_DATABASE_URL` if set (must be disposable — tables are
truncated), else a throwaway `postgres:17` container started here; skipped
when neither is available. Migrations are applied with alembic.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import sys
import time
import uuid
from collections import Counter
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator, Awaitable, Callable, Iterator, Optional

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

from app.core.errors import ConfigError, ErrorKind, PermanentError, TemporaryError
from app.core.retry import BackoffPolicy
from app.ingest import graph
from app.ingest.config import Resource
from app.ingest.engine import INTERRUPTED_MESSAGE, Engine
from app.ingest.models import IngestStage, StageStatus
from app.ingest.service import (
    IngestConflict,
    enqueue_paper,
    ingest_status,
    reprocess,
    retry_stage,
)
from app.ingest.stages.base import Stage, StageContext, skip

SERVER_ROOT = Path(__file__).resolve().parent.parent
WORKER_STAGES = [s for s in graph.STAGES if s != "source"]
SUPP_WORKER_STAGES = [s for s in WORKER_STAGES if s in graph.SUPPLEMENTARY_STAGES]
FAST_BACKOFF = BackoffPolicy(delays=(0.05,))


# -- database ---------------------------------------------------------------------


def _migrate(url: str) -> None:
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=SERVER_ROOT,
        env={**os.environ, "DATABASE_URL": url},
        check=True,
        capture_output=True,
    )


def _wait_for_postgres(url: str, timeout: float = 60.0) -> None:
    engine = create_engine(url)
    deadline = time.monotonic() + timeout
    try:
        while True:
            try:
                with engine.connect() as conn:
                    conn.execute(text("SELECT 1"))
                return
            except Exception:
                if time.monotonic() > deadline:
                    raise
                time.sleep(0.3)
    finally:
        engine.dispose()


@pytest.fixture(scope="module")
def pg_url() -> Iterator[str]:
    url = os.environ.get("INGEST_TEST_DATABASE_URL")
    if url:
        _migrate(url)
        yield url
        return
    if shutil.which("docker") is None:
        pytest.skip("needs docker or INGEST_TEST_DATABASE_URL")
    name = f"ingest-test-{uuid.uuid4().hex[:8]}"
    started = subprocess.run(
        [
            "docker", "run", "--rm", "-d", "--name", name,
            "-e", "POSTGRES_PASSWORD=postgres",
            "-p", "127.0.0.1::5432", "postgres:17",
        ],
        capture_output=True,
        text=True,
    )  # fmt: skip
    if started.returncode != 0:
        pytest.skip(f"can't start postgres: {started.stderr.strip()[:200]}")
    try:
        port = (
            subprocess.run(
                ["docker", "port", name, "5432/tcp"],
                capture_output=True,
                text=True,
                check=True,
            )
            .stdout.strip()
            .splitlines()[0]
            .rsplit(":", 1)[1]
        )
        url = f"postgresql://postgres:postgres@127.0.0.1:{port}/postgres"
        _wait_for_postgres(url)
        _migrate(url)
        yield url
    finally:
        subprocess.run(["docker", "rm", "-f", name], capture_output=True)


@pytest.fixture
def db(pg_url: str) -> Iterator[sessionmaker[Session]]:
    engine = create_engine(pg_url, pool_size=20, max_overflow=10)
    with engine.begin() as conn:
        conn.execute(text("TRUNCATE papers, ingest_worker CASCADE"))
    yield sessionmaker(bind=engine, autoflush=False)
    engine.dispose()


def new_paper(
    db: sessionmaker[Session],
    *,
    supplementary: bool = False,
    source_succeeded: bool = True,
    stages: Optional[dict[str, type[Stage[Any]]]] = None,
) -> uuid.UUID:
    paper_id = uuid.uuid4()
    with db() as session:
        parent = None
        if supplementary:
            parent = uuid.uuid4()
            _insert_paper(session, parent, None)
        _insert_paper(session, paper_id, parent)
        enqueue_paper(
            session,
            paper_id,
            is_supplementary=supplementary,
            source_succeeded=source_succeeded,
            stages=stages,
        )
        session.commit()
    return paper_id


def _insert_paper(session: Session, paper_id: uuid.UUID, parent: Any) -> None:
    session.execute(
        text(
            "INSERT INTO papers (id, file_url, status, supplementary_of_paper_id)"
            " VALUES (:id, 'x', 'todo', :parent)"
        ),
        {"id": paper_id, "parent": parent},
    )


def rows(db: sessionmaker[Session], paper_id: uuid.UUID) -> dict[str, IngestStage]:
    with db() as session:
        found = session.query(IngestStage).filter_by(paper_id=paper_id).all()
        session.expunge_all()
    return {r.name: r for r in found}


def statuses(db: sessionmaker[Session], paper_id: uuid.UUID) -> dict[str, str]:
    return {n: r.status.value for n, r in rows(db, paper_id).items()}


async def wait_until(
    check: Callable[[], bool], timeout: float = 10.0, what: str = "condition"
) -> None:
    deadline = time.monotonic() + timeout
    while not await asyncio.to_thread(check):
        if time.monotonic() > deadline:
            raise AssertionError(f"timed out waiting for {what}")
        await asyncio.sleep(0.02)


def all_status(db, paper_id, status: str, names=None) -> Callable[[], bool]:
    def check() -> bool:
        current = statuses(db, paper_id)
        return all(current[n] == status for n in (names or current))

    return check


# -- fake stages --------------------------------------------------------------------

Behavior = Callable[[StageContext], Awaitable[Any]]


async def ok(ctx: StageContext) -> Any:
    await asyncio.sleep(0.01)
    return f"{ctx.stage}-output"


class Script:
    """What every fake stage does, and what happened."""

    def __init__(self) -> None:
        self.behaviors: dict[str, Behavior] = {}
        self.config_errors: dict[str, Exception] = {}
        self.save_hooks: dict[str, Callable[[Session, StageContext, Any], None]] = {}
        self.runs: list[tuple[uuid.UUID, str, int, bool]] = []
        self.events: list[tuple[float, str, str, uuid.UUID]] = []  # start/end
        self.saved: list[tuple[uuid.UUID, str, Any]] = []
        self.resets: list[tuple[uuid.UUID, str]] = []
        self.active: Counter[Resource] = Counter()
        self.max_active: Counter[Resource] = Counter()


class FakeStage(Stage[Any]):
    script: Script

    def check_config(self) -> None:
        error = self.script.config_errors.get(self.name)
        if error is not None:
            raise error

    async def run(self, ctx: StageContext) -> Any:
        s = self.script
        s.runs.append((ctx.paper_id, self.name, ctx.attempt, ctx.is_supplementary))
        s.events.append((time.monotonic(), "start", self.name, ctx.paper_id))
        s.active[self.resource] += 1
        s.max_active[self.resource] = max(
            s.max_active[self.resource], s.active[self.resource]
        )
        try:
            return await s.behaviors.get(self.name, ok)(ctx)
        finally:
            s.active[self.resource] -= 1
            s.events.append((time.monotonic(), "end", self.name, ctx.paper_id))

    def save(self, session: Session, ctx: StageContext, output: Any) -> None:
        hook = self.script.save_hooks.get(self.name)
        if hook is not None:
            hook(session, ctx, output)
        self.script.saved.append((ctx.paper_id, self.name, output))

    def reset_outputs(self, session: Session, paper_id: uuid.UUID) -> None:
        self.script.resets.append((paper_id, self.name))


def fake_registry(
    script: Script,
    resource: Callable[[str], Resource] = lambda name: Resource.NETWORK,
    max_attempts: int = 3,
) -> dict[str, type[Stage[Any]]]:
    return {
        name: type(
            f"Fake_{name}",
            (FakeStage,),
            {
                "name": name,
                "needs": graph.NEEDS[name],
                "resource": resource(name),
                "timeout_s": 5.0,
                "max_attempts": 1 if name == "source" else max_attempts,
                "applies_to_supplementary": name in graph.SUPPLEMENTARY_STAGES,
                "runs_in_request": name == "source",
                "script": script,
            },
        )
        for name in graph.STAGES
    }


@asynccontextmanager
async def running_engine(
    db: sessionmaker[Session], stages: dict[str, type[Stage[Any]]], **kwargs: Any
) -> AsyncIterator[Engine]:
    kwargs.setdefault("backoff", FAST_BACKOFF)
    kwargs.setdefault("poll_interval", 0.05)
    engine = Engine(session_factory=db, stages=stages, **kwargs)
    task = asyncio.create_task(engine.run())
    try:
        yield engine
    finally:
        engine.stop()
        await asyncio.wait_for(task, 10)


# -- service (no worker) -------------------------------------------------------------


def test_enqueue_creates_rows_and_queues_ready(db) -> None:
    stages = fake_registry(Script())
    paper = new_paper(db, stages=stages)
    current = statuses(db, paper)
    assert set(current) == set(graph.STAGES)
    assert current["source"] == "succeeded"
    queued = {n for n, s in current.items() if s == "queued"}
    assert queued == {"text_layer", "preview", "ocr"}
    assert all(
        s == "pending" for n, s in current.items() if n not in queued | {"source"}
    )
    assert rows(db, paper)["ocr"].max_attempts == 3

    with db() as session:  # idempotent
        assert (
            enqueue_paper(
                session, paper, is_supplementary=False, source_succeeded=True,
                stages=stages,
            )
            == []
        )  # fmt: skip
        session.commit()
    assert statuses(db, paper) == current


def test_enqueue_without_source_queues_nothing(db) -> None:
    paper = new_paper(db, source_succeeded=False, stages=fake_registry(Script()))
    assert set(statuses(db, paper).values()) == {"pending"}


def test_supplementary_gets_stage_subset(db) -> None:
    paper = new_paper(db, supplementary=True, stages=fake_registry(Script()))
    assert set(statuses(db, paper)) == graph.SUPPLEMENTARY_STAGES


# -- worker -------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_success_chain_starts_dependents_immediately(db) -> None:
    script = Script()

    async def highlights(ctx: StageContext) -> str:
        ctx.model_used = "fake/model"
        return "hl"

    script.behaviors["highlights"] = highlights
    stages = fake_registry(script)
    paper = new_paper(db, stages=stages)
    # A 60 s poll: only "start dependents on completion" can finish in time.
    async with running_engine(db, stages, poll_interval=60):
        await wait_until(all_status(db, paper, "succeeded"), timeout=5, what="chain")

    result = rows(db, paper)
    assert all(result[n].attempt == 1 for n in WORKER_STAGES)
    assert all(result[n].finished_at is not None for n in WORKER_STAGES)
    assert result["highlights"].model_used == "fake/model"
    assert result["source"].attempt == 0  # never run by the worker
    assert sorted(n for _, n, _ in script.saved) == sorted(WORKER_STAGES)
    # Every stage started only after all its needs ended.
    ended = {n: t for t, kind, n, _ in script.events if kind == "end"}
    started = {n: t for t, kind, n, _ in script.events if kind == "start"}
    for name in WORKER_STAGES:
        for need in graph.NEEDS[name]:
            if need != "source":
                assert started[name] >= ended[need], (name, need)


@pytest.mark.asyncio
async def test_skip_counts_as_done_for_dependents(db) -> None:
    script = Script()

    async def metadata(ctx: StageContext) -> None:
        skip("no identifier found")

    script.behaviors["metadata"] = metadata
    stages = fake_registry(script)
    paper = new_paper(db, stages=stages)
    async with running_engine(db, stages):
        await wait_until(
            lambda: graph.is_done(statuses(db, paper)["metadata_fallback"]),
            what="metadata_fallback",
        )
    result = rows(db, paper)
    assert result["metadata"].status is StageStatus.SKIPPED
    assert result["metadata"].error_message == "no identifier found"
    assert result["metadata_fallback"].status is StageStatus.SUCCEEDED


@pytest.mark.asyncio
async def test_save_can_skip_another_stage(db) -> None:
    """`metadata.save` marks `metadata_fallback` skipped in the same
    transaction; the worker must see that, not overwrite it."""
    script = Script()

    def mark_fallback_skipped(session: Session, ctx: StageContext, output: Any) -> None:
        row = session.get(IngestStage, (ctx.paper_id, "metadata_fallback"))
        assert row is not None
        row.status = StageStatus.SKIPPED
        row.error_message = "resolved by metadata"

    script.save_hooks["metadata"] = mark_fallback_skipped
    stages = fake_registry(script)
    paper = new_paper(db, stages=stages)
    async with running_engine(db, stages):
        await wait_until(
            lambda: statuses(db, paper)["highlights"] == "succeeded", what="all"
        )
        await asyncio.sleep(0.2)
    current = statuses(db, paper)
    assert current["metadata_fallback"] == "skipped"
    assert "metadata_fallback" not in {n for _, n, _, _ in script.runs}


@pytest.mark.asyncio
async def test_temporary_failure_backs_off_then_succeeds(db) -> None:
    script = Script()
    seen: list[tuple[str, Optional[str]]] = []

    async def flaky(ctx: StageContext) -> str:
        if ctx.attempt < 3:
            raise TemporaryError(
                "upstream busy", retry_after=0.3 if ctx.attempt == 2 else None
            )
        return "ocr"

    script.behaviors["ocr"] = flaky
    stages = fake_registry(script)
    paper = new_paper(db, stages=stages)
    async with running_engine(db, stages):

        def queued_again() -> bool:
            row = rows(db, paper)["ocr"]
            if row.status is StageStatus.QUEUED and row.attempt >= 1:
                seen.append((row.status.value, row.error_message))
            return row.status is StageStatus.SUCCEEDED

        await wait_until(queued_again, what="ocr")
        await wait_until(all_status(db, paper, "succeeded"), what="all")

    ocr = rows(db, paper)["ocr"]
    assert ocr.attempt == 3
    assert ocr.error_message is None and ocr.error_kind is None
    assert ("queued", "upstream busy") in seen
    starts = [t for t, kind, n, _ in script.events if kind == "start" and n == "ocr"]
    assert starts[2] - starts[1] >= 0.29  # the provider's hint was honoured


@pytest.mark.asyncio
async def test_retryable_failure_gives_up_after_max_attempts(db) -> None:
    script = Script()

    async def always_busy(ctx: StageContext) -> None:
        raise TemporaryError("still busy")

    script.behaviors["text_layer"] = always_busy
    stages = fake_registry(script, max_attempts=2)
    paper = new_paper(db, stages=stages)
    async with running_engine(db, stages):
        await wait_until(lambda: statuses(db, paper)["text_layer"] == "failed")
    row = rows(db, paper)["text_layer"]
    assert row.attempt == 2
    assert row.error_kind is ErrorKind.TEMPORARY
    assert row.error_message == "still busy (gave up after 2 attempts)"
    current = statuses(db, paper)
    for name in (
        "metadata",
        "ocr_repair",
        "highlights",
        "outline",
        "metadata_fallback",
    ):
        assert current[name] == "blocked", name


@pytest.mark.asyncio
async def test_permanent_failure_blocks_then_retry_unblocks(db) -> None:
    script = Script()
    broken = {"ocr": True}

    async def ocr(ctx: StageContext) -> str:
        if broken["ocr"]:
            raise PermanentError("PDF page 3 is corrupt")
        return "ocr"

    script.behaviors["ocr"] = ocr
    stages = fake_registry(script)
    paper = new_paper(db, stages=stages)
    async with running_engine(db, stages):
        await wait_until(
            all_status(db, paper, "succeeded", ["text_layer", "preview", "metadata"])
        )
        await wait_until(lambda: statuses(db, paper)["ocr"] == "failed")
        current = statuses(db, paper)
        ocr_row = rows(db, paper)["ocr"]
        assert ocr_row.attempt == 1  # permanent: no retry
        assert ocr_row.error_kind is ErrorKind.PERMANENT
        assert ocr_row.error_message == "PDF page 3 is corrupt"
        for name in graph.downstream_of("ocr"):
            assert current[name] == "blocked", name

        with db() as session:
            with pytest.raises(IngestConflict):
                retry_stage(session, paper, "text_layer", stages=stages)
            broken["ocr"] = False
            # Retrying a blocked stage retries the failed stage upstream of it.
            assert retry_stage(session, paper, "outline", stages=stages) == ["ocr"]
            session.commit()
        after_retry = statuses(db, paper)
        assert after_retry["ocr"] in ("queued", "running", "succeeded")
        assert rows(db, paper)["ocr"].error_message is None
        for name in graph.downstream_of("ocr"):
            assert after_retry[name] in ("pending", "queued", "running", "succeeded")

        await wait_until(all_status(db, paper, "succeeded"), what="all")
    assert rows(db, paper)["ocr"].attempt == 1  # budget reset by the retry


@pytest.mark.asyncio
async def test_config_error_fails_immediately(db) -> None:
    script = Script()
    script.config_errors["outline"] = ConfigError("No model for ingest.outline")
    stages = fake_registry(script)
    paper = new_paper(db, stages=stages)
    async with running_engine(db, stages):
        await wait_until(lambda: statuses(db, paper)["outline"] == "failed")
    row = rows(db, paper)["outline"]
    assert (row.attempt, row.error_kind) == (1, ErrorKind.CONFIG)
    assert row.error_message == "No model for ingest.outline"
    assert "outline" not in {n for _, n, _, _ in script.runs}


@pytest.mark.asyncio
async def test_timeout_is_temporary_with_readable_message(db) -> None:
    script = Script()

    async def hang(ctx: StageContext) -> None:
        await asyncio.sleep(30)

    script.behaviors["preview"] = hang
    stages = fake_registry(script, max_attempts=1)
    stages["preview"].timeout_s = 0.3
    paper = new_paper(db, stages=stages)
    async with running_engine(db, stages):
        await wait_until(lambda: statuses(db, paper)["preview"] == "failed")
    row = rows(db, paper)["preview"]
    assert row.error_kind is ErrorKind.TEMPORARY
    assert row.error_message == "Timed out after 0.3 s (gave up after 1 attempt)"


@pytest.mark.asyncio
async def test_progress_is_recorded(db) -> None:
    script = Script()

    async def ocr(ctx: StageContext) -> str:
        for done in range(1, 6):
            await ctx.progress(done, 5)
            await asyncio.sleep(0.01)
        return "ocr"

    script.behaviors["ocr"] = ocr
    stages = fake_registry(script)
    paper = new_paper(db, stages=stages)
    async with running_engine(db, stages):
        await wait_until(lambda: statuses(db, paper)["ocr"] == "succeeded")
    row = rows(db, paper)["ocr"]
    assert (row.progress_done, row.progress_total) == (5, 5)


@pytest.mark.asyncio
async def test_reprocess_resets_downstream(db) -> None:
    script = Script()
    stages = fake_registry(script)
    paper = new_paper(db, stages=stages)
    async with running_engine(db, stages):
        await wait_until(all_status(db, paper, "succeeded"), what="first run")

    with db() as session:
        session.execute(
            text(
                "UPDATE ingest_stages SET status = 'running'"
                " WHERE paper_id = :p AND name = 'outline'"
            ),
            {"p": paper},
        )
        session.commit()
        with pytest.raises(IngestConflict, match="Outline is running"):
            reprocess(session, paper, "ocr", stages=stages)
        session.rollback()
        # Outside the running stage's subgraph is fine.
        assert reprocess(session, paper, "preview", stages=stages) == ["preview"]
        session.execute(
            text(
                "UPDATE ingest_stages SET status = 'succeeded'"
                " WHERE paper_id = :p AND name = 'outline'"
            ),
            {"p": paper},
        )
        assert reprocess(session, paper, "ocr", stages=stages) == ["ocr"]
        session.commit()

    subgraph = {"ocr", *graph.downstream_of("ocr")}
    assert {n for p, n in script.resets if p == paper} == subgraph | {"preview"}
    result = rows(db, paper)
    for name in subgraph - {"ocr"}:
        assert result[name].status is StageStatus.PENDING, name
        assert result[name].attempt == 0 and result[name].finished_at is None
    for name in ("text_layer", "metadata"):
        assert result[name].status is StageStatus.SUCCEEDED

    async with running_engine(db, stages):
        await wait_until(all_status(db, paper, "succeeded"), what="second run")
    ran = Counter(n for p, n, _, _ in script.runs if p == paper)
    assert {n: ran[n] for n in subgraph | {"preview"}} == dict.fromkeys(
        subgraph | {"preview"}, 2
    )
    assert ran["text_layer"] == 1 and ran["metadata"] == 1


def test_reprocess_source_keeps_source(db) -> None:
    stages = fake_registry(Script())
    paper = new_paper(db, stages=stages)
    with db() as session:
        session.execute(
            text("UPDATE ingest_stages SET status = 'succeeded' WHERE paper_id = :p"),
            {"p": paper},
        )
        queued = reprocess(session, paper, "source", stages=stages)
        session.commit()
    assert queued == ["text_layer", "preview", "ocr"]
    assert statuses(db, paper)["source"] == "succeeded"


@pytest.mark.asyncio
async def test_crash_recovery_on_start(db) -> None:
    script = Script()
    stages = fake_registry(script, max_attempts=3)
    paper = new_paper(db, stages=stages)
    with db() as session:
        # A crash mid-OCR (attempt 1 of 3) and mid-preview on its last attempt.
        session.execute(
            text(
                "UPDATE ingest_stages SET status = 'running', attempt = 1"
                " WHERE paper_id = :p AND name = 'ocr';"
                "UPDATE ingest_stages SET status = 'running', attempt = 3"
                " WHERE paper_id = :p AND name = 'preview'"
            ),
            {"p": paper},
        )
        session.commit()
    async with running_engine(db, stages):
        await wait_until(lambda: statuses(db, paper)["highlights"] == "succeeded")
    result = rows(db, paper)
    assert result["ocr"].status is StageStatus.SUCCEEDED
    assert result["ocr"].attempt == 2
    assert result["preview"].status is StageStatus.FAILED
    assert result["preview"].error_message == INTERRUPTED_MESSAGE


@pytest.mark.asyncio
async def test_per_resource_concurrency_limit(db) -> None:
    script = Script()

    async def slow(ctx: StageContext) -> str:
        await asyncio.sleep(0.1)
        return "x"

    for name in graph.STAGES:
        script.behaviors[name] = slow
    cpu = {"text_layer", "preview", "figures"}
    stages = fake_registry(
        script, resource=lambda n: Resource.CPU if n in cpu else Resource.LLM
    )
    papers = [new_paper(db, stages=stages) for _ in range(4)]
    async with running_engine(
        db, stages, concurrency={Resource.CPU: 1, Resource.LLM: 3}
    ):
        for paper in papers:
            await wait_until(all_status(db, paper, "succeeded"), timeout=20)
    assert script.max_active[Resource.CPU] == 1
    assert script.max_active[Resource.LLM] == 3


@pytest.mark.asyncio
async def test_supplementary_runs_only_its_stages(db) -> None:
    script = Script()
    stages = fake_registry(script)
    paper = new_paper(db, supplementary=True, stages=stages)
    async with running_engine(db, stages):
        await wait_until(all_status(db, paper, "succeeded"))
    ran = {(n, supp) for p, n, _, supp in script.runs if p == paper}
    assert ran == {(n, True) for n in SUPP_WORKER_STAGES}


@pytest.mark.asyncio
async def test_shutdown_requeues_unfinished_without_counting(db) -> None:
    script = Script()
    started = asyncio.Event()

    async def long_ocr(ctx: StageContext) -> None:
        started.set()
        await asyncio.sleep(30)

    script.behaviors["ocr"] = long_ocr
    stages = fake_registry(script)
    paper = new_paper(db, stages=stages)
    async with running_engine(db, stages, shutdown_grace=0.2):
        await asyncio.wait_for(started.wait(), 5)
    row = rows(db, paper)["ocr"]
    assert row.status is StageStatus.QUEUED
    assert row.attempt == 0


@pytest.mark.asyncio
async def test_ingest_status(db) -> None:
    script = Script()
    stages = fake_registry(script)
    paper = new_paper(db, stages=stages)
    with db() as session:
        status = ingest_status(session, paper)
    assert [r.name for r in status.stages] == list(graph.STAGES)
    assert status.active and not status.worker_online
    assert status.features["reading"].enabled
    assert status.features["chat"].waiting_on == ["ocr_repair"]

    async with running_engine(db, stages):
        await wait_until(all_status(db, paper, "succeeded"))
        with db() as session:
            status = ingest_status(session, paper)
    assert not status.active and status.worker_online
    assert all(f.enabled for f in status.features.values())


@pytest.mark.asyncio
async def test_crashed_cpu_child_replaces_the_process_pool(db) -> None:
    """A child dying (segfault, OOM kill) breaks a `ProcessPoolExecutor` for
    good; the attempt must be a temporary failure and later CPU work must
    get a fresh pool."""
    script = Script()
    crashed = asyncio.Event()

    async def preview(ctx: StageContext) -> Any:
        if ctx.attempt == 1:
            await ctx.cpu(os._exit, 1)
        crashed.set()
        return await ctx.cpu(abs, -2)

    async def figures(ctx: StageContext) -> Any:
        await crashed.wait()  # not in the pool the crash breaks
        return await ctx.cpu(abs, -3)

    script.behaviors["preview"] = preview
    script.behaviors["figures"] = figures
    stages = fake_registry(script)
    paper = new_paper(db, stages=stages)
    async with running_engine(db, stages):  # no cpu_runner: the real pool
        await wait_until(all_status(db, paper, "succeeded"), timeout=60, what="all")
    result = rows(db, paper)
    assert result["preview"].attempt == 2
    assert (paper, "figures", 3) in script.saved
    assert result["figures"].attempt == 1


class FlakyDb:
    """A session factory that refuses connections until `down_until`."""

    def __init__(self, db: sessionmaker[Session]) -> None:
        self.db = db
        self.down_until = 0.0
        self.refused = 0

    def go_down(self, seconds: float) -> None:
        self.down_until = time.monotonic() + seconds

    def __call__(self) -> Session:
        if time.monotonic() < self.down_until:
            self.refused += 1
            raise OperationalError("connect", None, Exception("database is down"))
        return self.db()


@pytest.mark.asyncio
async def test_outcome_write_retries_while_database_is_down(db) -> None:
    script = Script()
    flaky = FlakyDb(db)

    async def preview(ctx: StageContext) -> str:
        flaky.go_down(1.0)  # right before the success write
        return "preview-output"

    script.behaviors["preview"] = preview
    stages = fake_registry(script)
    paper = new_paper(db, stages=stages)
    engine = Engine(
        session_factory=flaky, stages=stages, backoff=FAST_BACKOFF, poll_interval=0.05
    )
    task = asyncio.create_task(engine.run())
    try:
        await wait_until(all_status(db, paper, "succeeded"), timeout=15, what="all")
    finally:
        engine.stop()
        await asyncio.wait_for(task, 10)
    assert flaky.refused > 0
    assert rows(db, paper)["preview"].attempt == 1  # the result wasn't lost
    assert [n for p, n, _ in script.saved if n == "preview"] == ["preview"]


@pytest.mark.asyncio
async def test_dropped_outcome_is_requeued_by_reconciliation(db) -> None:
    """When the outage outlasts the write retries the result is dropped;
    the row left `running` is requeued once the database is back."""
    script = Script()
    flaky = FlakyDb(db)

    async def preview(ctx: StageContext) -> str:
        if ctx.attempt == 1:
            flaky.go_down(1.0)
        return "preview-output"

    script.behaviors["preview"] = preview
    stages = fake_registry(script)
    paper = new_paper(db, stages=stages)
    engine = Engine(
        session_factory=flaky,
        stages=stages,
        backoff=FAST_BACKOFF,
        poll_interval=0.05,
        outcome_write_timeout=0.2,
        reconcile_interval=0.1,
    )
    task = asyncio.create_task(engine.run())
    try:
        await wait_until(all_status(db, paper, "succeeded"), timeout=15, what="all")
    finally:
        engine.stop()
        await asyncio.wait_for(task, 10)
    preview_row = rows(db, paper)["preview"]
    assert preview_row.attempt == 2
    assert [a for p, n, a, _ in script.runs if n == "preview"] == [1, 2]


@pytest.mark.asyncio
async def test_paper_deleted_while_stages_run_is_swept(db) -> None:
    """Stages finishing after their paper was deleted record nothing and
    sweep the paper's S3 prefix (they may have uploaded after the delete)."""
    script = Script()
    swept: list[uuid.UUID] = []
    deleted = asyncio.Event()

    async def preview(ctx: StageContext) -> str:
        def delete(session: Session) -> None:
            session.execute(
                text("DELETE FROM papers WHERE id = :p"), {"p": ctx.paper_id}
            )

        await ctx.write(delete)
        deleted.set()
        return "preview"

    async def text_layer(ctx: StageContext) -> str:
        await deleted.wait()
        raise TemporaryError("upstream busy")

    async def ocr(ctx: StageContext) -> str:
        await deleted.wait()
        return "ocr"

    script.behaviors.update(preview=preview, text_layer=text_layer, ocr=ocr)
    stages = fake_registry(script)
    paper = new_paper(db, stages=stages)
    async with running_engine(db, stages, sweep_paper=swept.append):
        await wait_until(lambda: len(swept) == 3, what="three sweeps")
    assert swept == [paper] * 3
    assert rows(db, paper) == {}
    assert script.saved == []
