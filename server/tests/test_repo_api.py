"""Endpoint tests for the repo API — traversal defense and status shapes.

The handlers are called directly with stubbed CRUD so the tests stay unit
tests (no DB, no network) while still exercising the real path validation.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.api import repo_api
from app.database.crud.paper_repo_crud import is_stale_ingest
from app.llm.repo import storage

PAPER_ID = "77777777-7777-7777-7777-777777777777"
SHA = "a" * 40

FILES = [
    {"path": "pipeline/run.py", "size": 42},
    {"path": "README.md", "size": 10},
]


@pytest.fixture()
def published(monkeypatch, tmp_path: Path):
    """A published snapshot on disk plus a matching `ready` row."""
    monkeypatch.setenv("REPO_STORAGE_DIR", str(tmp_path))
    root = storage.snapshot_dir(PAPER_ID, SHA)
    (root / "pipeline").mkdir(parents=True)
    (root / "pipeline" / "run.py").write_text("def go():\n    return 1\n", encoding="utf-8")
    (root / "README.md").write_text("# hi\n", encoding="utf-8")
    (tmp_path / "outside.txt").write_text("host secret\n", encoding="utf-8")
    storage.write_manifest(
        root,
        {"owner": "andyrdt", "repo": "refusal_direction", "ref": "main",
         "commit_sha": SHA, "files": FILES},
    )
    (root / storage.DONE_MARKER).write_text("ok", encoding="utf-8")

    row = SimpleNamespace(
        id=uuid.uuid4(), paper_id=uuid.UUID(PAPER_ID), owner="andyrdt",
        repo="refusal_direction", ref="main", commit_sha=SHA, status="ready",
        error=None, file_count=2, total_bytes=52, storage_prefix=f"{PAPER_ID}/{SHA}",
        updated_at=datetime.now(timezone.utc),
    )
    monkeypatch.setattr(
        repo_api.paper_crud, "get", lambda *a, **k: SimpleNamespace(id=PAPER_ID)
    )
    monkeypatch.setattr(
        repo_api.paper_repo_crud, "get_by_paper_id", lambda *a, **k: row
    )
    return row


USER = SimpleNamespace(id=uuid.uuid4())


def _call(coro):
    return asyncio.run(coro)


# -- file endpoint traversal ----------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        "../outside.txt",
        "../../etc/passwd",
        "/etc/passwd",
        "pipeline/../../outside.txt",
        "pipeline/../../../etc/passwd",
        ".done",                      # real file in the dir, not in manifest
        "manifest.json",              # ditto
        "does/not/exist.py",
        "",
        "pipeline/run.py/../../outside.txt",
    ],
)
def test_file_endpoint_refuses_anything_outside_the_manifest(published, path):
    with pytest.raises(HTTPException) as excinfo:
        _call(repo_api.get_repo_file(PAPER_ID, path, db=None, current_user=USER))
    assert excinfo.value.status_code == 404


def test_file_endpoint_serves_a_manifest_file_with_a_permalink(published):
    result = _call(
        repo_api.get_repo_file(PAPER_ID, "pipeline/run.py", db=None, current_user=USER)
    )
    assert result["path"] == "pipeline/run.py"
    assert "def go():" in result["content"]
    assert result["size"] == len("def go():\n    return 1\n")
    assert result["github_url"] == (
        f"https://github.com/andyrdt/refusal_direction/blob/{SHA}/pipeline/run.py"
    )


def test_file_endpoint_tolerates_a_leading_slash(published):
    result = _call(
        repo_api.get_repo_file(PAPER_ID, "/README.md", db=None, current_user=USER)
    )
    assert result["path"] == "README.md"


def test_file_endpoint_truncates_huge_files(published, monkeypatch):
    monkeypatch.setattr(repo_api, "MAX_FILE_RESPONSE_BYTES", 5)
    result = _call(
        repo_api.get_repo_file(PAPER_ID, "pipeline/run.py", db=None, current_user=USER)
    )
    assert "file truncated for display" in result["content"]
    assert result["size"] == len("def go():\n    return 1\n")


# -- tree / status shapes -------------------------------------------------


def test_tree_endpoint_returns_the_manifest(published):
    result = _call(repo_api.get_repo_tree(PAPER_ID, db=None, current_user=USER))
    assert result["owner"] == "andyrdt"
    assert result["repo"] == "refusal_direction"
    assert result["ref"] == "main"
    assert result["commit_sha"] == SHA
    assert result["files"] == FILES


def test_status_endpoint_shape(published):
    result = _call(repo_api.get_repo(PAPER_ID, db=None, current_user=USER))
    payload = result.model_dump()
    assert set(payload) == {
        "status", "owner", "repo", "ref", "commit_sha", "error",
        "file_count", "total_bytes", "updated_at",
    }
    assert payload["status"] == "ready"
    assert payload["file_count"] == 2


def test_status_404s_when_nothing_is_connected(monkeypatch):
    monkeypatch.setattr(
        repo_api.paper_crud, "get", lambda *a, **k: SimpleNamespace(id=PAPER_ID)
    )
    monkeypatch.setattr(repo_api.paper_repo_crud, "get_by_paper_id", lambda *a, **k: None)
    with pytest.raises(HTTPException) as excinfo:
        _call(repo_api.get_repo(PAPER_ID, db=None, current_user=USER))
    assert excinfo.value.status_code == 404


def test_endpoints_404_for_a_paper_the_user_does_not_own(monkeypatch):
    monkeypatch.setattr(repo_api.paper_crud, "get", lambda *a, **k: None)
    for coro in (
        repo_api.get_repo(PAPER_ID, db=None, current_user=USER),
        repo_api.get_repo_tree(PAPER_ID, db=None, current_user=USER),
        repo_api.get_repo_file(PAPER_ID, "README.md", db=None, current_user=USER),
    ):
        with pytest.raises(HTTPException) as excinfo:
            _call(coro)
        assert excinfo.value.status_code == 404


def test_tree_409s_while_the_repo_is_not_ready(monkeypatch):
    monkeypatch.setattr(
        repo_api.paper_crud, "get", lambda *a, **k: SimpleNamespace(id=PAPER_ID)
    )
    monkeypatch.setattr(
        repo_api.paper_repo_crud,
        "get_by_paper_id",
        lambda *a, **k: SimpleNamespace(status="ingesting", commit_sha=None),
    )
    with pytest.raises(HTTPException) as excinfo:
        _call(repo_api.get_repo_tree(PAPER_ID, db=None, current_user=USER))
    assert excinfo.value.status_code == 409


# -- ingestion state machine ----------------------------------------------


class _FakeRow(SimpleNamespace):
    pass


def _ingestion_env(monkeypatch, tmp_path: Path, status: str = "pending"):
    """Stub the CRUD + session so run_ingestion can be driven directly."""
    monkeypatch.setenv("REPO_STORAGE_DIR", str(tmp_path))
    row = _FakeRow(
        id=uuid.uuid4(), paper_id=uuid.UUID(PAPER_ID), status=status,
        owner="o", repo="r", ref=None, commit_sha=None, error=None,
        updated_at=datetime.now(timezone.utc),
    )
    state = {"row": row, "claims": 0, "marks": []}

    monkeypatch.setattr(
        "app.database.database.SessionLocal", lambda: SimpleNamespace(
            close=lambda: None, rollback=lambda: None, expire_all=lambda: None
        )
    )
    monkeypatch.setattr(
        repo_api.paper_repo_crud, "get_by_paper_id",
        lambda **kwargs: state["row"],
    )

    def _claim(session, *, row_id):
        state["claims"] += 1
        if state["claims"] > 1:
            return None          # only the first caller wins
        return row

    monkeypatch.setattr(repo_api.paper_repo_crud, "claim_for_ingestion", _claim)

    def _mark(session, *, row, user=None, **fields):
        state["marks"].append(fields)
        for key, value in fields.items():
            setattr(row, key, value)
        return row

    monkeypatch.setattr(repo_api.paper_repo_crud, "mark", _mark)
    return state


def test_run_ingestion_writes_ready_on_success(monkeypatch, tmp_path: Path):
    state = _ingestion_env(monkeypatch, tmp_path)
    monkeypatch.setattr(
        repo_api, "ingest_repo",
        lambda **kwargs: repo_api.__dict__["ingest_repo"] and SimpleNamespace(
            owner="andyrdt", repo="refusal_direction", ref="main",
            commit_sha=SHA, file_count=2, total_bytes=52,
            storage_prefix=f"{PAPER_ID}/{SHA}",
        ),
    )
    repo_api.run_ingestion(PAPER_ID, "https://github.com/andyrdt/refusal_direction")
    assert state["marks"][-1]["status"] == "ready"
    assert state["marks"][-1]["commit_sha"] == SHA


def test_run_ingestion_writes_error_on_ingest_failure(monkeypatch, tmp_path: Path):
    state = _ingestion_env(monkeypatch, tmp_path)

    def _boom(**kwargs):
        raise repo_api.IngestError("Repository not found (or private).")

    monkeypatch.setattr(repo_api, "ingest_repo", _boom)
    repo_api.run_ingestion(PAPER_ID, "https://github.com/o/r")
    assert state["marks"][-1]["status"] == "error"
    assert "not found" in state["marks"][-1]["error"]


def test_run_ingestion_writes_error_on_unexpected_failure(monkeypatch, tmp_path: Path):
    state = _ingestion_env(monkeypatch, tmp_path)

    def _boom(**kwargs):
        raise RuntimeError("kaboom")

    monkeypatch.setattr(repo_api, "ingest_repo", _boom)
    repo_api.run_ingestion(PAPER_ID, "https://github.com/o/r")
    assert state["marks"][-1]["status"] == "error"
    assert "RuntimeError" in state["marks"][-1]["error"]


def test_only_one_of_two_racing_jobs_claims_the_row(monkeypatch, tmp_path: Path):
    state = _ingestion_env(monkeypatch, tmp_path)
    monkeypatch.setattr(
        repo_api, "ingest_repo",
        lambda **kwargs: SimpleNamespace(
            owner="o", repo="r", ref="main", commit_sha=SHA, file_count=1,
            total_bytes=1, storage_prefix=f"{PAPER_ID}/{SHA}",
        ),
    )
    repo_api.run_ingestion(PAPER_ID, "https://github.com/o/r")
    repo_api.run_ingestion(PAPER_ID, "https://github.com/o/r")
    assert state["claims"] == 2
    # Only the winner wrote a terminal status.
    assert [m["status"] for m in state["marks"]] == ["ready"]


def test_snapshot_is_discarded_when_the_row_vanishes(monkeypatch, tmp_path: Path):
    """DELETE during a stale-ingesting window must not leave an orphan
    snapshot — nothing evicts it."""
    state = _ingestion_env(monkeypatch, tmp_path)
    snapshot_root = tmp_path / PAPER_ID / SHA
    snapshot_root.mkdir(parents=True)

    def _ingest(**kwargs):
        state["row"] = None       # row deleted while we were ingesting
        return SimpleNamespace(
            owner="o", repo="r", ref="main", commit_sha=SHA, file_count=1,
            total_bytes=1, storage_prefix=f"{PAPER_ID}/{SHA}",
        )

    monkeypatch.setattr(repo_api, "ingest_repo", _ingest)
    repo_api.run_ingestion(PAPER_ID, "https://github.com/o/r")
    assert not (tmp_path / PAPER_ID).exists()
    assert not any(m.get("status") == "ready" for m in state["marks"])


def test_superseded_job_keeps_the_replacement_rows_snapshot(monkeypatch, tmp_path: Path):
    """A stale job finishing after a disconnect + reconnect must discard only
    ITS snapshot: the new row's snapshot stays and no READY is written."""
    state = _ingestion_env(monkeypatch, tmp_path)
    old_sha = "b" * 40
    (tmp_path / PAPER_ID / old_sha).mkdir(parents=True)
    (tmp_path / PAPER_ID / SHA).mkdir(parents=True)  # the replacement's

    def _ingest(**kwargs):
        state["row"] = _FakeRow(id=uuid.uuid4(), status="ready", commit_sha=SHA)
        return SimpleNamespace(
            owner="o", repo="r", ref="main", commit_sha=old_sha, file_count=1,
            total_bytes=1, storage_prefix=f"{PAPER_ID}/{old_sha}",
        )

    monkeypatch.setattr(repo_api, "ingest_repo", _ingest)
    repo_api.run_ingestion(PAPER_ID, "https://github.com/o/r")
    assert not (tmp_path / PAPER_ID / old_sha).exists()
    assert (tmp_path / PAPER_ID / SHA).exists()
    assert not any(m.get("status") == "ready" for m in state["marks"])


def test_owner_prunes_older_snapshots_only_after_ready(monkeypatch, tmp_path: Path):
    state = _ingestion_env(monkeypatch, tmp_path)
    old_sha = "c" * 40
    (tmp_path / PAPER_ID / old_sha).mkdir(parents=True)
    (tmp_path / PAPER_ID / SHA).mkdir(parents=True)
    monkeypatch.setattr(
        repo_api, "ingest_repo",
        lambda **kwargs: SimpleNamespace(
            owner="o", repo="r", ref="main", commit_sha=SHA, file_count=1,
            total_bytes=1, storage_prefix=f"{PAPER_ID}/{SHA}",
        ),
    )
    repo_api.run_ingestion(PAPER_ID, "https://github.com/o/r")
    assert state["marks"][-1]["status"] == "ready"
    assert not (tmp_path / PAPER_ID / old_sha).exists()
    assert (tmp_path / PAPER_ID / SHA).exists()


# -- staleness rule -------------------------------------------------------


def test_fresh_ingesting_row_is_not_stale():
    row = SimpleNamespace(status="ingesting", updated_at=datetime.now(timezone.utc))
    assert is_stale_ingest(row) is False


def test_ingesting_row_older_than_15_minutes_is_stale():
    row = SimpleNamespace(
        status="ingesting",
        updated_at=datetime.now(timezone.utc) - timedelta(minutes=16),
    )
    assert is_stale_ingest(row) is True


def test_naive_timestamps_are_treated_as_utc():
    row = SimpleNamespace(
        status="ingesting",
        updated_at=(datetime.now(timezone.utc) - timedelta(minutes=16)).replace(tzinfo=None),
    )
    assert is_stale_ingest(row) is True


def test_terminal_states_are_never_stale():
    for status in ("ready", "error"):
        assert is_stale_ingest(SimpleNamespace(status=status, updated_at=None)) is False


def test_pending_counts_as_in_flight_and_can_go_stale():
    """A container that died between creating the row and running the task
    leaves a `pending` row; it must be re-kickable, but not immediately (that
    would let a double-POST schedule two ingestions)."""
    fresh = SimpleNamespace(status="pending", updated_at=datetime.now(timezone.utc))
    assert is_stale_ingest(fresh) is False
    stranded = SimpleNamespace(
        status="pending",
        updated_at=datetime.now(timezone.utc) - timedelta(minutes=16),
    )
    assert is_stale_ingest(stranded) is True
