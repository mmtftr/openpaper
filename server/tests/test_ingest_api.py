"""`app.ingest.api`: HTTP behaviour with stubbed service calls, plus a few
calls against a disposable Postgres (the engine tests' fixtures)."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
import test_ingest_engine as engine_tests
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.auth.dependencies import get_required_user
from app.database.database import get_db
from app.ingest import api as ingest_api
from app.ingest import graph
from app.ingest.features import FEATURES, features
from app.ingest.models import IngestStage, StageStatus
from app.ingest.service import IngestConflict, IngestStatus

USER = SimpleNamespace(id=uuid.uuid4())
PAPER_ID = uuid.uuid4()
NOW = datetime(2026, 9, 24, 12, 0, tzinfo=timezone.utc)


class FakeSession:
    def __init__(self) -> None:
        self.commits = 0
        self.rollbacks = 0

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1


def _row(name: str, status: StageStatus, **fields) -> IngestStage:
    return IngestStage(
        paper_id=PAPER_ID,
        name=name,
        status=status,
        attempt=fields.pop("attempt", 1),
        max_attempts=5,
        **fields,
    )


def _status(rows: list[IngestStage], *, online: bool = True) -> IngestStatus:
    by_name = {row.name: row for row in rows}
    return IngestStatus(
        stages=[by_name[n] for n in graph.topo_sorted(by_name)],
        features=features(by_name),
        active=any(
            r.status in (StageStatus.PENDING, StageStatus.QUEUED, StageStatus.RUNNING)
            for r in rows
        ),
        worker_online=online,
        worker_last_seen=NOW if online else None,
    )


@pytest.fixture
def session() -> FakeSession:
    return FakeSession()


@pytest.fixture
def client(monkeypatch, session) -> TestClient:
    monkeypatch.setattr(
        ingest_api.paper_crud,
        "get",
        lambda db, id, user: object() if id == PAPER_ID else None,
    )
    app = FastAPI()
    app.include_router(ingest_api.ingest_router, prefix="/api/paper")
    app.dependency_overrides[get_required_user] = lambda: USER
    app.dependency_overrides[get_db] = lambda: session
    return TestClient(app)


def _in_progress() -> list[IngestStage]:
    ok = StageStatus.SUCCEEDED
    return [
        _row("source", ok, finished_at=NOW),
        _row("text_layer", ok, finished_at=NOW),
        _row("preview", ok, finished_at=NOW),
        _row(
            "ocr",
            StageStatus.RUNNING,
            progress_done=32,
            progress_total=48,
            model_used="mistral-ocr-latest",
            started_at=NOW,
        ),
        _row("figures", StageStatus.PENDING, attempt=0),
        _row("ocr_repair", StageStatus.PENDING, attempt=0),
        _row("metadata", ok, finished_at=NOW),
        _row("metadata_fallback", StageStatus.SKIPPED, finished_at=NOW),
        _row("outline", StageStatus.PENDING, attempt=0),
        _row("highlights", StageStatus.PENDING, attempt=0),
    ]


def test_status_lists_stages_and_features(monkeypatch, client):
    monkeypatch.setattr(
        ingest_api, "ingest_status", lambda db, pid: _status(_in_progress())
    )
    response = client.get(f"/api/paper/{PAPER_ID}/ingest")
    assert response.status_code == 200
    body = response.json()
    assert [s["name"] for s in body["stages"]] == list(graph.STAGES)
    ocr = body["stages"][3]
    assert ocr["label"] == "OCR"
    assert ocr["status"] == "running"
    assert (ocr["progress_done"], ocr["progress_total"]) == (32, 48)
    assert ocr["model_used"] == "mistral-ocr-latest"
    assert body["active"] is True
    assert body["worker_online"] is True
    assert body["legacy"] is False
    assert set(body["features"]) == set(FEATURES)
    assert body["features"]["metadata"]["enabled"] is True
    chat = body["features"]["chat"]
    assert chat["enabled"] is False
    assert chat["waiting_on"] == ["ocr_repair"]
    assert chat["cause"] == "ocr"
    assert chat["reason"] == "Waiting for OCR (32/48)"


def test_paper_without_rows_is_legacy_and_fully_enabled(monkeypatch, client):
    monkeypatch.setattr(ingest_api, "ingest_status", lambda db, pid: _status([]))
    body = client.get(f"/api/paper/{PAPER_ID}/ingest").json()
    assert body["legacy"] is True
    assert body["stages"] == []
    assert body["active"] is False
    assert all(f["enabled"] for f in body["features"].values())


def test_missing_paper_is_404(client):
    response = client.get(f"/api/paper/{uuid.uuid4()}/ingest")
    assert response.status_code == 404
    assert response.json() == {"detail": "Paper not found"}


def test_retry_commits_and_returns_status(monkeypatch, client, session):
    calls = []
    monkeypatch.setattr(
        ingest_api, "retry_stage", lambda db, pid, name: calls.append(name)
    )
    monkeypatch.setattr(
        ingest_api, "ingest_status", lambda db, pid: _status(_in_progress())
    )
    response = client.post(f"/api/paper/{PAPER_ID}/ingest/ocr/retry")
    assert response.status_code == 200
    assert calls == ["ocr"]
    assert session.commits == 1
    assert response.json()["stages"][0]["name"] == "source"


@pytest.mark.parametrize("action", ["retry", "reprocess"])
def test_conflict_is_409_with_message(monkeypatch, client, session, action):
    def _conflict(db, pid, name):
        raise IngestConflict("Can't reprocess OCR while OCR is running")

    monkeypatch.setattr(ingest_api, "retry_stage", _conflict)
    monkeypatch.setattr(ingest_api, "reprocess", _conflict)
    response = client.post(f"/api/paper/{PAPER_ID}/ingest/ocr/{action}")
    assert response.status_code == 409
    assert response.json() == {"detail": "Can't reprocess OCR while OCR is running"}
    assert session.commits == 0
    assert session.rollbacks == 1


def test_unknown_stage_is_404(client):
    response = client.post(f"/api/paper/{PAPER_ID}/ingest/nope/retry")
    assert response.status_code == 404
    assert "nope" in response.json()["detail"]


# -- against Postgres (fixtures from the engine tests; skipped without docker) --

# Reused fixtures: a throwaway postgres:17 container with migrations applied.
pg_url = engine_tests.pg_url
db = engine_tests.db


def test_status_from_real_rows(db):
    paper_id = engine_tests.new_paper(db)
    with db() as session:
        body = ingest_api.build_status(session, paper_id)
    assert not body.legacy
    assert [s.name for s in body.stages] == list(graph.STAGES)
    assert body.stages[0].status is StageStatus.SUCCEEDED
    assert body.stages[0].finished_at is not None
    assert body.active
    assert not body.worker_online  # no heartbeat row
    assert body.features.reading.enabled
    assert not body.features.chat.enabled
    assert body.features.chat.cause == "text_layer"


def test_legacy_paper_from_real_db(db):
    paper_id = uuid.uuid4()
    with db() as session:
        engine_tests._insert_paper(session, paper_id, None)
        session.commit()
        body = ingest_api.build_status(session, paper_id)
    assert body.legacy and not body.active and body.stages == []
    assert all(state["enabled"] for state in body.features.model_dump().values())


def test_retry_conflict_rolls_back_real_session(db):
    paper_id = engine_tests.new_paper(db)
    with db() as session:
        with pytest.raises(ingest_api.HTTPException) as caught:
            ingest_api._run(session, paper_id, "ocr", ingest_api.retry_stage)
    assert caught.value.status_code == 409
    assert "only failed or blocked" in caught.value.detail
