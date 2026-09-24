"""The Monty sandbox behind the `run_python` tool.

Empirically-verified Monty semantics this encodes (see the spike report):

* `max_duration_secs` is a CUMULATIVE session budget, not a per-call
  timeout. Exhausting it (or the memory cap) POISONS the session
  permanently: every later feed fails instantly with the same limit error.
  So the pool's parent-side `request_timeout` is the real per-call deadline,
  and poisoning is detected (same limit error, ~0 ms) and repaired by
  rebuilding the session, with an explicit notice appended to the tool
  output so the model knows its variables are gone.
* A mount is PER-FEED: omitting it on a later call raises `PermissionError`,
  which reads like a security failure rather than a wiring bug. Same for
  `external_lookup`. Both are passed on every feed.

Concurrency: one pool per gunicorn worker with at most 2 sandbox processes
(workers = cpu*2+1, so unbounded defaults would multiply out to hundreds of
processes), a module-level semaphore matching it, and a per-run lock because
pydantic-ai may execute a response's tool calls in parallel.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from pydantic_monty import (
    CollectStreams,
    Monty,
    MontyCrashedError,
    MontyError,
    MontyRuntimeError,
    MontySyntaxError,
    MontyTypingError,
    MountDir,
)

from app.llm.repo.prelude import RepoPrelude

logger = logging.getLogger(__name__)

VIRTUAL_ROOT = "/repo"

# Genuine per-call deadline (parent-side). A snippet exceeding it kills the
# worker and raises MontyCrashedError(timed_out=True).
PER_CALL_TIMEOUT = 25.0
# Whole-turn cumulative execution budget. Exhausting it poisons the session.
SESSION_DURATION_BUDGET = 240.0
SESSION_MEMORY_BUDGET = 512 * 1024 * 1024
# Default MountDir limit is 100 MB and it also covers transient filesystem
# results, so it is sized against the ingested tree cap (100 MB), not left
# at the default.
MOUNT_MEMORY_LIMIT = 512 * 1024 * 1024

MAX_PROCESSES = 2
CHECKOUT_TIMEOUT = 10.0
# Waiting for a free sandbox slot happens BEFORE any tokens stream, so it is
# kept short: a busy sandbox degrades the turn to paper-only rather than
# stalling the whole answer.
OPEN_CHECKOUT_TIMEOUT = 3.0
# Cap on what the MODEL sees from one run_python call. Sized so a full
# `read()` window (MAX_READ_LINES line-numbered lines of ~60-char source)
# comes back whole. Independent of the UI wire cap: past 6,000 serialized
# chars `truncate_tool_output` swaps the structure for a preview, but keeps
# the `files` chips, so nothing here needs to fit on the wire.
MAX_TOOL_OUTPUT = 24_000
# Cumulative model-visible output for one agent run (~60K tokens, ten full
# read() windows). Once spent, later calls are cut to
# MAX_TOOL_OUTPUT_AFTER_BUDGET so a long exploration cannot flood the
# context window; the truncation notice tells the model to narrow.
RUN_OUTPUT_BUDGET = 240_000
MAX_TOOL_OUTPUT_AFTER_BUDGET = 4_000

MAX_CODE_CHARS = 20_000

SESSION_RESET_NOTICE = (
    "\n\n[sandbox notice] This session exceeded its resource budget and has "
    "been RESET. All variables and function definitions from earlier "
    "run_python calls are gone — redefine anything you still need. Avoid "
    "unbounded loops and reading very large files."
)

BUSY_MESSAGE = (
    "[sandbox busy] No sandbox worker was available for this request. The "
    "repo tools are temporarily saturated — answer from what you already "
    "have, or try one more run_python call later in this turn."
)

# `run_python` feeds can take 25 s. They must NOT share the paper tools'
# executor, where they would starve every other tool call in the process.
_sandbox_executor = ThreadPoolExecutor(
    max_workers=MAX_PROCESSES + 1, thread_name_prefix="repo-sandbox"
)

_pool: Optional[Monty] = None
_pool_lock = threading.Lock()
_slots = asyncio.Semaphore(MAX_PROCESSES)


def _get_pool() -> Monty:
    """Lazily build the per-process pool (never at import time — gunicorn
    forks workers, and a pool created pre-fork would be shared)."""
    global _pool
    if _pool is None:
        with _pool_lock:
            if _pool is None:
                pool = Monty(
                    request_timeout=PER_CALL_TIMEOUT,
                    min_processes=0,
                    max_processes=MAX_PROCESSES,
                    checkout_timeout=CHECKOUT_TIMEOUT,
                )
                pool.__enter__()
                _pool = pool
    return _pool


@dataclass
class RepoSnapshot:
    """A published, SHA-pinned snapshot on local disk."""

    paper_id: str
    owner: str
    repo: str
    ref: str
    commit_sha: str
    root: Path
    files: List[Dict[str, Any]]

    @property
    def slug(self) -> str:
        return f"{self.owner}/{self.repo}"

    @property
    def file_count(self) -> int:
        return len(self.files)


class SandboxBusy(RuntimeError):
    """No pool worker became available within the checkout timeout."""


class RepoSandbox:
    """One repo snapshot mounted read-only for the duration of one agent run.

    Owned by `run_paper_chat`: it calls `open()` before the run and `close()`
    in its `finally` BEFORE the stream `aclose()` awaits (those can re-raise
    CancelledError and skip anything after them). A user hitting stop must
    not leak a pool worker or a mount fd.
    """

    def __init__(self, snapshot: RepoSnapshot):
        self.snapshot = snapshot
        self.prelude = RepoPrelude(snapshot.root, snapshot.files)
        self._mount: Optional[MountDir] = None
        self._session: Any = None
        self._open_error: Optional[str] = None
        self._lock = asyncio.Lock()
        # Guards the mount/session handoff between the executor thread that
        # opens them and the loop thread that closes them: a cancelled turn
        # can run `close()` while the checkout is still in flight, and a
        # session attached after that point would never be released.
        self._state_lock = threading.Lock()
        self._closed = False
        self._holds_slot = False
        self.calls = 0
        self.resets = 0
        self.errors = 0
        # Model-visible output chars emitted so far in this run
        # (see RUN_OUTPUT_BUDGET).
        self.output_chars = 0

    # ---- lifecycle --------------------------------------------------------

    async def open(self) -> None:
        """Create the mount and take a pool slot. Never raises: a sandbox that
        fails to open must degrade to a tool-level message, not kill the chat
        turn.

        The slot is acquired HERE, with a deadline, and held until `close()`.
        Checking out first and bounding later would let the Nth concurrent
        turn queue behind N-1 ten-second checkout timeouts, each occupying an
        executor thread — the saturation stall would grow without limit
        instead of being capped at one timeout.
        """
        try:
            await asyncio.wait_for(_slots.acquire(), timeout=OPEN_CHECKOUT_TIMEOUT)
        except (asyncio.TimeoutError, TimeoutError):
            self._open_error = BUSY_MESSAGE
            logger.warning("Monty pool saturated; repo sandbox unavailable")
            return
        self._holds_slot = True
        try:
            await asyncio.get_running_loop().run_in_executor(
                _sandbox_executor, self._open_sync
            )
        except TimeoutError:
            self._open_error = BUSY_MESSAGE
            logger.warning("Monty checkout timed out; repo sandbox unavailable")
        except Exception as exc:
            self._open_error = (
                f"[sandbox unavailable] The code sandbox could not be started "
                f"({type(exc).__name__}). Answer from the paper text instead."
            )
            logger.error("Failed to open repo sandbox: %s", exc, exc_info=True)

    def _open_sync(self) -> None:
        pool = _get_pool()
        mount = MountDir(
            host_path=self.snapshot.root,
            virtual_path=VIRTUAL_ROOT,
            mode="read-only",
            memory_usage_limit=MOUNT_MEMORY_LIMIT,
        )
        mount.__enter__()
        try:
            session = _checkout(pool)
        except BaseException:
            _close_mount(mount)
            raise
        if not self._attach(mount, session):
            # `close()` already ran (the turn was cancelled while the
            # checkout was in flight). Nothing else will ever release these.
            logger.info("Repo sandbox opened after close; releasing it")
            _exit_session(session)
            _close_mount(mount)

    def _attach(self, mount: MountDir, session: Any) -> bool:
        """Adopt freshly opened resources unless the sandbox is closed."""
        with self._state_lock:
            if self._closed:
                return False
            self._mount = mount
            self._session = session
            return True

    def close(self) -> None:
        """Release the session, the mount and the pool slot.

        Idempotent and never raises — it runs from `run_paper_chat`'s
        `finally`, including on cancellation. Detaches under the state lock
        so a checkout finishing on the executor thread sees `_closed` and
        releases its own resources (see `_attach`).
        """
        with self._state_lock:
            if self._closed:
                return
            self._closed = True
            session, self._session = self._session, None
            mount, self._mount = self._mount, None
        _exit_session(session)
        _close_mount(mount)
        if self._holds_slot:
            self._holds_slot = False
            try:
                _slots.release()
            except (ValueError, RuntimeError) as exc:  # pragma: no cover
                logger.warning("Failed to release sandbox slot: %s", exc)

    def _reset_session(self) -> None:
        with self._state_lock:
            session, self._session = self._session, None
        _exit_session(session)
        try:
            fresh = _checkout(_get_pool())
        except Exception as exc:
            logger.warning("Failed to rebuild poisoned sandbox session: %s", exc)
            return
        with self._state_lock:
            if not self._closed:
                self._session = fresh
                self.resets += 1
                return
        # Closed while rebuilding: never leave a checked-out worker behind.
        _exit_session(fresh)

    # ---- the tool body ----------------------------------------------------

    async def run(self, code: str) -> Dict[str, Any]:
        """Execute `code` in the session. Returns the tool payload.

        The payload is `{"files": [...], "output": "..."}`. The wire-side
        `truncate_tool_output` keeps `files` when it previews an oversized
        `output`, so the UI chips never depend on the model-facing cap here.
        """
        snippet = str(code or "")
        if not snippet.strip():
            return _payload([], "[error] run_python was called with empty code.")
        if len(snippet) > MAX_CODE_CHARS:
            return _payload(
                [],
                f"[error] code is too long ({len(snippet)} chars, max "
                f"{MAX_CODE_CHARS}). Split the work across calls.",
            )
        if self._open_error:
            return _payload([], self._open_error)
        if self._closed or self._session is None:
            return _payload([], BUSY_MESSAGE)

        # The pool slot is already held for this sandbox's lifetime (taken in
        # `open()`), so bounding happens once per RUN rather than per call.
        # The per-run lock serializes parallel tool calls within ONE run — a
        # Monty session is not re-entrant.
        async with self._lock:
            # Inside the lock: the touched-file log and the prelude's DoS
            # budgets are per feed, and a parallel feed would otherwise steal
            # this call's chips and its allowance.
            self.prelude.start_call()
            cap = self._output_cap()
            output = await asyncio.get_running_loop().run_in_executor(
                _sandbox_executor, self._feed_sync, snippet, cap
            )
            touched = self.prelude.touched
            self.output_chars += len(output)

        self.calls += 1
        return _payload(touched, output)

    def _output_cap(self) -> int:
        """Per-call output cap, reduced once the run's cumulative budget is spent."""
        if self.output_chars >= RUN_OUTPUT_BUDGET:
            return MAX_TOOL_OUTPUT_AFTER_BUDGET
        return MAX_TOOL_OUTPUT

    def _feed_sync(self, code: str, cap: int = MAX_TOOL_OUTPUT) -> str:
        streams = CollectStreams()
        was_reset = False
        try:
            value = self._feed(code, streams)
        except (MontyRuntimeError, MontySyntaxError) as exc:
            self.errors += 1
            body = exc.display("traceback")
            short = exc.display("type-msg")
            # Match the TYPE only. The spike confirmed the exact TimeoutError
            # wording, but not MemoryError's — and a wording mismatch would
            # leave the session poisoned for the rest of the turn.
            if short.startswith(("TimeoutError:", "MemoryError:")):
                self._reset_session()
                was_reset = True
                body += SESSION_RESET_NOTICE
            return _format(streams, cap, body, None)
        except MontyTypingError as exc:
            self.errors += 1
            return _format(streams, cap, exc.display(), None)
        except MontyCrashedError as exc:
            self.errors += 1
            self._reset_session()
            was_reset = True
            return _format(
                streams,
                cap,
                f"SandboxCrashed: the worker died (timed_out={exc.timed_out}). "
                "The session has been RESET — all previous variables are gone."
                + (
                    " If a search or read was too large, narrow it."
                    if exc.timed_out
                    else ""
                ),
                None,
            )
        except MontyError as exc:
            self.errors += 1
            return _format(streams, cap, f"{type(exc).__name__}: {exc}", None)
        except Exception as exc:  # pragma: no cover - defensive
            self.errors += 1
            logger.error("Unexpected sandbox failure: %s", exc, exc_info=True)
            return _format(streams, cap, f"SandboxError: {type(exc).__name__}", None)
        finally:
            if was_reset:
                logger.info("Repo sandbox session reset (poisoned)")
        return _format(streams, cap, None, None if value is None else repr(value))

    def _feed(self, code: str, streams: CollectStreams) -> Any:
        if self._session is None:
            raise RuntimeError("sandbox session is not available")
        return self._session.feed_run(
            code,
            mount=self._mount,
            print_callback=streams,
            external_lookup=self.prelude.as_external_lookup(),
        )


def _checkout(pool: Monty) -> Any:
    """Check a session out of the pool and enter it."""
    session = pool.checkout(
        script_name="repo_explorer.py",
        limits={
            "max_duration_secs": SESSION_DURATION_BUDGET,
            "max_memory": SESSION_MEMORY_BUDGET,
        },
    )
    session.__enter__()
    return session


def _exit_session(session: Any) -> None:
    if session is None:
        return
    try:
        session.__exit__(None, None, None)
    except Exception:
        pass


def _close_mount(mount: Optional[MountDir]) -> None:
    if mount is None:
        return
    try:
        mount.close()
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("Failed to close repo mount: %s", exc)


# Chips are bounded on their own: 20 paths of up to 400 chars would be ~8 KB
# of file list on every tool row, on the wire and in the persisted dump alike.
MAX_FILES_CHARS = 800


def _payload(files: Sequence[str], output: str) -> Dict[str, Any]:
    kept: List[str] = []
    used = 0
    for path in list(files)[:20]:
        text = str(path)
        if used + len(text) + 4 > MAX_FILES_CHARS:
            break
        kept.append(text)
        used += len(text) + 4
    return {"files": kept, "output": output}


def _format(
    streams: CollectStreams,
    cap: int,
    error: Optional[str],
    value_repr: Optional[str],
) -> str:
    stdout = "".join(text for stream, text in streams.output if stream == "stdout")
    stderr = "".join(text for stream, text in streams.output if stream == "stderr")
    parts: List[str] = []
    if stdout:
        parts.append(stdout.rstrip("\n"))
    if stderr:
        parts.append(f"[stderr]\n{stderr.rstrip()}")
    if error:
        parts.append(f"[error]\n{error.rstrip()}")
    elif value_repr is not None:
        parts.append(f"[value] {value_repr}")
    if not parts:
        parts.append(
            "[no output] (the snippet printed nothing and its last line was "
            "not an expression — use print())"
        )
    out = "\n".join(parts)
    if len(out) > cap:
        dropped = len(out) - cap
        out = out[:cap] + (
            f"\n...[output truncated: {dropped:,} more chars not shown. Narrow "
            "the call — read() a smaller start=/end= window, grep() with "
            "path=/glob= or a lower max_results — and continue from the last "
            "line shown above.]"
        )
    return out


def shutdown_pool() -> None:
    """Tear the pool down (tests; process exit)."""
    global _pool
    with _pool_lock:
        if _pool is not None:
            try:
                _pool.__exit__(None, None, None)
            except Exception:
                pass
            _pool = None


def load_snapshot(*, paper_id: str, commit_sha: str) -> Optional[RepoSnapshot]:
    """Build a RepoSnapshot from a published manifest, or None if unusable."""
    from app.llm.repo import storage

    manifest = storage.load_manifest(paper_id, commit_sha)
    if not manifest:
        return None
    files = manifest.get("files")
    if not isinstance(files, list) or not files:
        return None
    try:
        # `tree_dir` is what gets mounted: only repo content, never our
        # manifest/marker files.
        root = storage.tree_dir(paper_id, commit_sha)
    except storage.SnapshotPathError:
        return None
    return RepoSnapshot(
        paper_id=str(paper_id),
        owner=str(manifest.get("owner") or ""),
        repo=str(manifest.get("repo") or ""),
        ref=str(manifest.get("ref") or ""),
        commit_sha=str(manifest.get("commit_sha") or commit_sha),
        root=root,
        files=[entry for entry in files if isinstance(entry, dict)],
    )
