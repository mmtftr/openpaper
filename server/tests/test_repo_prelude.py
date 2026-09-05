"""Unit tests for the sandbox prelude and snapshot storage helpers.

The prelude's callbacks run IN the API process holding the GIL — Monty's
`request_timeout` does not guard them — so confinement and the DoS clamps
are the load-bearing behavior here.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from app.llm.repo import storage
from app.llm.repo.prelude import (
    MAX_CONTEXT,
    MAX_READ_LINES,
    MAX_RESULTS,
    RepoPrelude,
)
from app.llm.repo.storage import SnapshotPathError


@pytest.fixture()
def snapshot(tmp_path: Path):
    root = tmp_path / "snap"
    (root / "pipeline").mkdir(parents=True)
    (root / "pipeline" / "run.py").write_text(
        "\n".join(f"line {i}" for i in range(1, 51)) + "\ndef compute_direction():\n    return 1\n",
        encoding="utf-8",
    )
    (root / "README.md").write_text("# Title\n\nsome docs\n", encoding="utf-8")
    (root / "deep").mkdir()
    (root / "deep" / "a").mkdir()
    (root / "deep" / "a" / "b.py").write_text("x = 1\n", encoding="utf-8")
    # A file physically present but NOT in the manifest: must be invisible.
    (root / "secret.py").write_text("TOKEN = 'sekrit'\n", encoding="utf-8")
    (tmp_path / "outside.txt").write_text("host secret\n", encoding="utf-8")

    files = [
        {"path": "README.md", "size": 20},
        {"path": "deep/a/b.py", "size": 6},
        {"path": "pipeline/run.py", "size": 400},
    ]
    return RepoPrelude(root, files)


# -- path confinement -----------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        "/repo/../outside.txt",
        "/repo/../../etc/passwd",
        "/etc/passwd",
        "/repo/pipeline/../../outside.txt",
        "../outside.txt",
    ],
)
def test_read_refuses_to_escape_the_snapshot(snapshot, path):
    out = snapshot.read(path)
    assert "host secret" not in out
    assert out.startswith("read:")


def test_read_refuses_files_absent_from_the_manifest(snapshot):
    out = snapshot.read("/repo/secret.py")
    assert "sekrit" not in out
    assert "no such file" in out


def test_read_is_line_numbered_and_clamped(snapshot):
    out = snapshot.read("/repo/pipeline/run.py")
    assert "/repo/pipeline/run.py (lines 1-" in out
    assert "\n 1| line 1" in out or "\n  1| line 1" in out
    # A 400-line cap applies even when the caller asks for everything.
    body_lines = [line for line in out.split("\n") if "|" in line]
    assert len(body_lines) <= MAX_READ_LINES


def test_read_range_and_continuation_hint(snapshot):
    out = snapshot.read("/repo/pipeline/run.py", 5, 7)
    assert "(lines 5-7 of" in out
    assert "5| line 5" in out
    assert "8| line 8" not in out
    assert "more lines" in out


def test_read_records_touched_files(snapshot):
    assert snapshot.touched == []
    snapshot.read("/repo/README.md")
    snapshot.read("/repo/README.md")
    snapshot.read("/repo/nope.py")
    assert snapshot.touched == ["README.md"]


def test_relative_paths_are_accepted(snapshot):
    assert "# Title" in snapshot.read("README.md")


# -- tree -----------------------------------------------------------------


def test_tree_lists_manifest_entries_only(snapshot):
    out = snapshot.tree("/repo")
    assert "README.md" in out
    assert "pipeline/" in out
    assert "secret.py" not in out


def test_tree_collapses_below_max_depth(snapshot):
    out = snapshot.tree("/repo", max_depth=1)
    assert "raise max_depth" in out or "…" in out or "files" in out


def test_tree_rejects_unknown_directory(snapshot):
    assert "no such directory" in snapshot.tree("/repo/nope")


# -- grep -----------------------------------------------------------------


def test_grep_finds_matches_with_context(snapshot):
    out = snapshot.grep("compute_direction", glob="*.py")
    assert "/repo/pipeline/run.py:51:" in out
    assert "matches in" in out
    assert "pipeline/run.py" in snapshot.touched


def test_grep_glob_filters_files(snapshot):
    assert "no matches" in snapshot.grep("Title", glob="*.py")
    assert "README.md" in snapshot.grep("Title", glob="*.md")


def test_grep_clamps_context_and_results(snapshot):
    # Absurd arguments must not blow up or produce unbounded output.
    out = snapshot.grep("line", context=10_000, max_results=10_000)
    assert "grep:" not in out.split("\n")[0]
    assert out.count("\n") < 5_000


def test_grep_rejects_overlong_patterns(snapshot):
    assert "too long" in snapshot.grep("a" * 501)


def test_grep_rejects_bad_regex(snapshot):
    assert "bad regex" in snapshot.grep("(unclosed")


def test_grep_survives_catastrophic_backtracking(snapshot):
    """A pattern that CANNOT match must still return promptly.

    `(a+)+b` over a long run of `a` with no `b` is the classic exponential
    case; the per-line regex timeout is the only thing standing between it
    and a wedged gunicorn worker.
    """
    (snapshot.root / "pipeline" / "run.py").write_text(
        "a" * 5000 + "\n", encoding="utf-8"
    )
    started = time.monotonic()
    out = snapshot.grep(r"(a+)+b", glob="*.py")
    assert time.monotonic() - started < 5.0
    assert "too expensive" in out or "too broad" in out or "no matches" in out


def test_grep_honors_the_scan_budget(snapshot, monkeypatch):
    monkeypatch.setattr("app.llm.repo.prelude.GREP_SCAN_BYTE_BUDGET", 1)
    out = snapshot.grep("line")
    assert "too broad" in out or "matches" in out


def test_grep_rejects_paths_outside_the_snapshot(snapshot):
    out = snapshot.grep("secret", path="/repo/../")
    assert "host secret" not in out


# -- per-FEED budgets -----------------------------------------------------


def test_helper_budgets_are_per_feed_not_per_call(snapshot):
    """A snippet can call the helpers in a LOOP. Per-call budgets would just
    multiply — measured at 85 s of GIL-holding work in one feed, which Monty's
    request_timeout cannot interrupt because the sandbox worker is idle."""
    from app.llm.repo.prelude import MAX_HELPER_CALLS_PER_FEED

    snapshot.start_call()
    outputs = [
        snapshot.grep("line", glob="*.py")
        for _ in range(MAX_HELPER_CALLS_PER_FEED + 5)
    ]
    assert any("[budget]" in out for out in outputs)
    # ...and the very next feed starts with a fresh allowance.
    snapshot.start_call()
    assert "[budget]" not in snapshot.grep("line", glob="*.py")


def test_scan_budget_is_shared_between_read_and_grep(snapshot, monkeypatch):
    monkeypatch.setattr("app.llm.repo.prelude.GREP_SCAN_BYTE_BUDGET", 100)
    snapshot.start_call()
    snapshot.read("/repo/pipeline/run.py")     # 400 bytes per the manifest
    assert "[budget]" in snapshot.read("/repo/README.md")


def test_wall_deadline_is_shared_across_calls(snapshot, monkeypatch):
    snapshot.start_call()
    monkeypatch.setattr(snapshot, "_deadline", time.monotonic() - 1)
    assert "[budget]" in snapshot.read("/repo/README.md")
    assert "budget" in snapshot.grep("line").lower()


def test_start_call_clears_the_chips(snapshot):
    snapshot.read("/repo/README.md")
    assert snapshot.touched == ["README.md"]
    snapshot.start_call()
    assert snapshot.touched == []


def test_unsupported_globs_are_reported_not_ignored(snapshot):
    """Silently ignoring the glob turns an attempt to NARROW the search into
    a whole-repo scan."""
    for glob in ("**/*.py", "src/*.py", "test_*.py"):
        out = snapshot.grep("line", glob=glob)
        assert "unsupported glob" in out


def test_virtual_root_prefix_is_component_aware(snapshot):
    """`/repository/...` and `/repo-evil/...` must not be silently rewritten
    into paths under the snapshot."""
    for path in ("/repository/pkg/a.py", "/repo-evil/x.py"):
        out = snapshot.read(path)
        assert "must be under /repo/" in out


# -- storage helpers ------------------------------------------------------


def test_resolve_within_blocks_traversal(tmp_path: Path):
    root = tmp_path / "root"
    root.mkdir()
    (tmp_path / "sibling.txt").write_text("nope", encoding="utf-8")
    with pytest.raises(SnapshotPathError):
        storage.resolve_within(root, "../sibling.txt")
    with pytest.raises(SnapshotPathError):
        storage.resolve_within(root, "a/../../sibling.txt")
    assert storage.resolve_within(root, "a/b.py") == (root / "a" / "b.py").resolve()


def test_resolve_within_rejects_prefix_lookalike(tmp_path: Path):
    """`/x/root-evil` must not pass as being inside `/x/root`."""
    root = tmp_path / "root"
    root.mkdir()
    (tmp_path / "root-evil").mkdir()
    with pytest.raises(SnapshotPathError):
        storage.resolve_within(root, "../root-evil/x.py")


def test_storage_paths_validate_their_components(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("REPO_STORAGE_DIR", str(tmp_path))
    paper_id = "11111111-1111-1111-1111-111111111111"
    sha = "a" * 40
    assert storage.snapshot_dir(paper_id, sha) == tmp_path / paper_id / sha
    with pytest.raises(SnapshotPathError):
        storage.snapshot_dir("../../etc", sha)
    with pytest.raises(SnapshotPathError):
        storage.snapshot_dir(paper_id, "../../etc")
    with pytest.raises(SnapshotPathError):
        storage.snapshot_dir(paper_id, "not-a-sha")


def test_manifest_is_gated_by_the_done_marker(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("REPO_STORAGE_DIR", str(tmp_path))
    paper_id = "22222222-2222-2222-2222-222222222222"
    sha = "b" * 40
    target = tmp_path / paper_id / sha
    target.mkdir(parents=True)
    (target / "manifest.json").write_text(json.dumps({"files": []}), encoding="utf-8")
    assert storage.load_manifest(paper_id, sha) is None  # no .done yet
    (target / storage.DONE_MARKER).write_text("ok", encoding="utf-8")
    assert storage.load_manifest(paper_id, sha) == {"files": []}


def test_publish_is_atomic_and_prunes_old_snapshots(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("REPO_STORAGE_DIR", str(tmp_path))
    paper_id = "33333333-3333-3333-3333-333333333333"
    old_sha, new_sha = "c" * 40, "d" * 40
    old = storage.snapshot_dir(paper_id, old_sha)
    old.mkdir(parents=True)
    (old / storage.DONE_MARKER).write_text("ok", encoding="utf-8")

    staging = storage.temp_dir(paper_id, new_sha)
    staging.mkdir(parents=True)
    (staging / "a.py").write_text("x", encoding="utf-8")
    storage.publish(staging, storage.snapshot_dir(paper_id, new_sha))

    assert storage.is_ready(paper_id, new_sha)
    assert not staging.exists()
    storage.prune_other_snapshots(paper_id, new_sha)
    assert not old.exists()
    assert storage.is_ready(paper_id, new_sha)


def test_tree_dir_prefers_the_nested_tree(monkeypatch, tmp_path: Path):
    """Repo content lives under `tree/` so it can never collide with our
    `manifest.json` / `.done`."""
    monkeypatch.setenv("REPO_STORAGE_DIR", str(tmp_path))
    paper_id = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
    sha = "f" * 40
    root = storage.snapshot_dir(paper_id, sha)
    (root / storage.TREE_SUBDIR).mkdir(parents=True)
    assert storage.tree_dir(paper_id, sha) == root / storage.TREE_SUBDIR


def test_tree_dir_falls_back_for_legacy_snapshots(monkeypatch, tmp_path: Path):
    """Snapshots written before the split must stay readable."""
    monkeypatch.setenv("REPO_STORAGE_DIR", str(tmp_path))
    paper_id = "cccccccc-cccc-cccc-cccc-cccccccccccc"
    sha = "e" * 40
    root = storage.snapshot_dir(paper_id, sha)
    root.mkdir(parents=True)
    (root / "main.py").write_text("x", encoding="utf-8")
    assert storage.tree_dir(paper_id, sha) == root


def test_staging_dirs_are_unique_per_job(monkeypatch, tmp_path: Path):
    """Two ingestions of the same sha in one process must not share a
    staging path — the second's cleanup would delete the first's tree."""
    monkeypatch.setenv("REPO_STORAGE_DIR", str(tmp_path))
    paper_id = "dddddddd-dddd-dddd-dddd-dddddddddddd"
    sha = "a" * 40
    assert storage.temp_dir(paper_id, sha) != storage.temp_dir(paper_id, sha)


def test_prune_never_deletes_a_live_staging_dir(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("REPO_STORAGE_DIR", str(tmp_path))
    paper_id = "eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee"
    keep, other = "a" * 40, "b" * 40
    storage.snapshot_dir(paper_id, keep).mkdir(parents=True)
    stale = storage.snapshot_dir(paper_id, other)
    stale.mkdir(parents=True)
    staging = storage.temp_dir(paper_id, other)
    staging.mkdir(parents=True)

    storage.prune_other_snapshots(paper_id, keep)
    assert not stale.exists()
    assert staging.exists()      # a concurrent ingest is writing here


def test_delete_paper_snapshots_is_total_and_safe(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("REPO_STORAGE_DIR", str(tmp_path))
    paper_id = "44444444-4444-4444-4444-444444444444"
    target = storage.snapshot_dir(paper_id, "e" * 40)
    target.mkdir(parents=True)
    storage.delete_paper_snapshots(paper_id)
    assert not (tmp_path / paper_id).exists()
    # Non-existent and invalid ids are no-ops, never raises.
    storage.delete_paper_snapshots(paper_id)
    storage.delete_paper_snapshots("../../etc")
