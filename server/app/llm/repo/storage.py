"""Where repo snapshots live on disk.

One directory per (paper, commit sha):

    {REPO_STORAGE_DIR}/{paper_id}/{sha}/
        manifest.json
        .done                 <- written last; gates readers
        tree/                 <- the pruned repo, and ONLY the repo

Repo content lives under `tree/` so it can never collide with our metadata:
a repository with its own top-level `manifest.json` would otherwise have it
overwritten, and `/repo` would not be a faithful copy of what we ingested.
Snapshots written before that split are still readable — `tree_dir()` falls
back to the snapshot root when no `tree/` is present.

In the deployed stack `REPO_STORAGE_DIR` is a named docker volume mounted
into the server container — on a single-host deploy the volume IS the
durability. If it is ever lost, re-connecting the repo re-ingests
idempotently, so nothing here needs a second (S3) copy.

Every path component is validated before it is joined: `paper_id` must
parse as a UUID and `sha` must be 40 hex chars, so no caller-controlled
string can walk out of the storage root.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.settings import get_settings

logger = logging.getLogger(__name__)

DONE_MARKER = ".done"
MANIFEST_NAME = "manifest.json"

_SHA_RE = re.compile(r"^[0-9a-f]{40}$")

# Dev default: a git-ignored directory under `server/`. Compose overrides it
# with the mounted volume path.
_DEFAULT_STORAGE_DIR = Path(__file__).resolve().parents[3] / ".repo_snapshots"


class SnapshotPathError(ValueError):
    """A paper_id / sha / relative path that must never be joined to disk."""


def storage_root() -> Path:
    """Root of the snapshot store (`REPO_STORAGE_DIR`)."""
    configured = get_settings().REPO_STORAGE_DIR
    return Path(configured) if configured else _DEFAULT_STORAGE_DIR


def _safe_paper_id(paper_id: Any) -> str:
    try:
        return str(uuid.UUID(str(paper_id)))
    except (ValueError, AttributeError, TypeError):
        raise SnapshotPathError(f"invalid paper_id: {paper_id!r}")


def _safe_sha(sha: Any) -> str:
    text = str(sha or "").lower()
    if not _SHA_RE.match(text):
        raise SnapshotPathError(f"invalid commit sha: {sha!r}")
    return text


def paper_dir(paper_id: Any) -> Path:
    return storage_root() / _safe_paper_id(paper_id)


def snapshot_dir(paper_id: Any, sha: Any) -> Path:
    return paper_dir(paper_id) / _safe_sha(sha)


TREE_SUBDIR = "tree"


def tree_dir(paper_id: Any, sha: Any) -> Path:
    """Directory to mount / serve / verify against.

    Falls back to the snapshot root for snapshots written before repo content
    was moved under `tree/`.
    """
    root = snapshot_dir(paper_id, sha)
    nested = root / TREE_SUBDIR
    return nested if nested.is_dir() else root


def temp_dir(paper_id: Any, sha: Any) -> Path:
    """Staging directory for an in-flight extraction.

    Same filesystem as the final location (so publishing is a real atomic
    rename) and uniquely named per job — two ingestions of the same sha in
    one process would otherwise share a staging path and delete each other's
    half-written tree.
    """
    return paper_dir(paper_id) / f"{_safe_sha(sha)}.tmp.{uuid.uuid4().hex}"


def is_ready(paper_id: Any, sha: Any) -> bool:
    try:
        return (snapshot_dir(paper_id, sha) / DONE_MARKER).exists()
    except SnapshotPathError:
        return False


def publish(tmp: Path, final: Path) -> None:
    """Mark `tmp` complete and move it into place atomically."""
    (tmp / DONE_MARKER).write_text("ok", encoding="utf-8")
    if final.exists():
        shutil.rmtree(final, ignore_errors=True)
    final.parent.mkdir(parents=True, exist_ok=True)
    os.rename(tmp, final)


def prune_other_snapshots(paper_id: Any, keep_sha: Any) -> None:
    """Drop older snapshots (and abandoned `.tmp.` staging dirs) for a paper.

    Growth is bounded by connected repos, but a repo re-ingested at a new
    head would otherwise keep every historical tree forever.
    """
    keep = _safe_sha(keep_sha)
    root = paper_dir(paper_id)
    if not root.exists():
        return
    for entry in root.iterdir():
        if entry.name == keep:
            continue
        # Never touch a staging directory: a concurrent ingestion of another
        # sha is actively writing into it.
        if ".tmp." in entry.name:
            continue
        try:
            shutil.rmtree(entry, ignore_errors=True)
        except OSError as exc:  # pragma: no cover - defensive
            logger.warning("Failed to prune snapshot %s: %s", entry, exc)


def delete_snapshot(paper_id: Any, sha: Any) -> None:
    """Remove ONE snapshot. Never raises — used by a superseded ingestion to
    discard only its own output while a newer row keeps its snapshot."""
    try:
        target = snapshot_dir(paper_id, sha)
    except SnapshotPathError:
        return
    try:
        if target.exists():
            shutil.rmtree(target, ignore_errors=True)
    except OSError as exc:  # pragma: no cover - defensive
        logger.warning("Failed to delete snapshot %s: %s", target, exc)


def delete_paper_snapshots(paper_id: Any) -> None:
    """Remove every snapshot for a paper. Never raises — callers are delete
    paths where a stale directory must not fail the user's request."""
    try:
        root = paper_dir(paper_id)
    except SnapshotPathError:
        return
    try:
        if root.exists():
            shutil.rmtree(root, ignore_errors=True)
    except OSError as exc:  # pragma: no cover - defensive
        logger.warning("Failed to delete repo snapshots for %s: %s", paper_id, exc)


def write_manifest(target: Path, manifest: Dict[str, Any]) -> None:
    (target / MANIFEST_NAME).write_text(
        json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
    )


def load_manifest(paper_id: Any, sha: Any) -> Optional[Dict[str, Any]]:
    """Read a published snapshot's manifest, or None if it isn't readable."""
    try:
        root = snapshot_dir(paper_id, sha)
    except SnapshotPathError:
        return None
    if not (root / DONE_MARKER).exists():
        return None
    try:
        raw = (root / MANIFEST_NAME).read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        manifest = json.loads(raw)
    except (TypeError, ValueError):
        return None
    return manifest if isinstance(manifest, dict) else None


def manifest_paths(manifest: Optional[Dict[str, Any]]) -> List[str]:
    if not manifest:
        return []
    files = manifest.get("files")
    if not isinstance(files, list):
        return []
    out: List[str] = []
    for entry in files:
        if isinstance(entry, dict):
            path = entry.get("path")
            if isinstance(path, str) and path:
                out.append(path)
    return out


def resolve_within(root: Path, relative: str) -> Path:
    """Resolve `relative` under `root`, refusing anything that escapes it.

    `Path.resolve()` + `is_relative_to` — never a string prefix check, which
    `/repo-evil` would defeat against a `/repo` root.
    """
    text = str(relative or "").strip().lstrip("/")
    if not text:
        raise SnapshotPathError("empty path")
    if "\x00" in text:
        raise SnapshotPathError("path contains a NUL byte")
    resolved_root = root.resolve()
    candidate = (resolved_root / text).resolve()
    if candidate != resolved_root and not candidate.is_relative_to(resolved_root):
        raise SnapshotPathError(f"path escapes the snapshot root: {relative!r}")
    return candidate
