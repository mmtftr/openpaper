"""Read-only repo lookups for the quick-question agent.

The inline answer already has the selected file and the selection in its
prompt; these three tools are what let it follow a reference OUT of that file
(a caller, a definition, a config value) without turning a popover into a
chat turn.

Deliberately sandbox-free: `RepoPrelude`'s helpers are the same host-side
functions the chat agent's `run_python` exposes to Monty, and for a one-shot
question the sandbox buys nothing over calling them directly — no session to
open, no restricted interpreter for the model to learn, no pool slot held for
a popover.

They execute IN the API process and hold the GIL (see `prelude`'s module
docstring), so every call is offloaded to a small dedicated thread pool,
serialized by a per-request lock, admitted through a process-wide slot
limit (`MAX_CONCURRENT_LOOKUPS`), and given a fresh per-call DoS budget via
`start_call()`.

The per-question lookup budget is the shared `ToolBudgetCapability`
(`lookup_budget_capability`): a call with a missing argument is answered for
free, a call past `MAX_LOOKUPS` gets `BUDGET_SPENT`, and a call that found
every slot busy is refunded (`NotCharged`).
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Dict, Optional

from pydantic_ai import Agent

from app.llm.chat.budget import (
    NotCharged,
    Refusal,
    ToolBudget,
    ToolBudgetCapability,
)
from app.llm.repo.prelude import (
    MAX_RESULTS,
    MAX_TREE_DEPTH,
    VIRTUAL_ROOT,
    RepoPrelude,
)

logger = logging.getLogger(__name__)

# Lookups one question gets. A soft budget (a spent budget answers with a
# stop message, see `app.llm.chat.budget`) rather than `UsageLimits` alone:
# pydantic-ai raises on an over-budget tool call, which would kill a stream
# that may already have text in it. `run_quick_question` sets the hard limits
# above this for the same reason `run_paper_chat` leaves headroom over its
# tool budget.
MAX_LOOKUPS = 4

# Cap on what the MODEL sees from one lookup. Same sizing as the sandbox's
# per-call cap: a full 400-line `read` window comes back whole. Independent
# of the UI wire cap in `truncate_tool_output`.
MAX_LOOKUP_OUTPUT = 24_000

TRUNCATION_NOTICE = (
    "\n\n... [output truncated at {cap:,} characters — narrow the range with "
    "start=/end=, or the search with path=/glob=]"
)

BUDGET_SPENT = (
    "[budget] This question has used all {max} repo lookups. Do not call "
    "another tool — answer from the file, the selection and what you have "
    "already read, and say plainly what you could not check."
)

PATH_REQUIRED = "read_file: path is required, e.g. '/repo/pkg/module.py'."
PATTERN_REQUIRED = "grep_repo: pattern is required."

LOOKUP_FAILED = (
    "{tool}: the lookup failed and returned nothing. Answer from what you already have."
)

DEFAULT_GREP_RESULTS = 30

# Process-wide admission for lookups, the quick-question counterpart of the
# sandbox's MAX_PROCESSES: the helpers hold the GIL, so two concurrent greps
# is already a worker's worth, and N concurrent questions must not become N
# GIL-bound threads. A question that can't get a slot within
# LOOKUP_SLOT_WAIT degrades to "answer from what you have" rather than
# queueing behind other requests — same trade-off as a busy sandbox.
MAX_CONCURRENT_LOOKUPS = 2
LOOKUP_SLOT_WAIT = 3.0
_SLOT_POLL_INTERVAL = 0.05

LOOKUPS_BUSY = (
    "{tool}: repository lookups are busy right now. Answer from the file, "
    "the selection and what you have already read."
)

# `threading` primitives rather than `asyncio` ones: they are not bound to an
# event loop (gunicorn workers and the tests' fresh loops both work), and the
# permit is released from the WORKER thread — see `_run_holding_slot`.
_slots = threading.BoundedSemaphore(MAX_CONCURRENT_LOOKUPS)
_executor: Optional[ThreadPoolExecutor] = None
_executor_guard = threading.Lock()


def _get_executor() -> ThreadPoolExecutor:
    """Dedicated pool, created lazily so it is never inherited across a
    gunicorn fork with threads already started."""
    global _executor
    with _executor_guard:
        if _executor is None:
            _executor = ThreadPoolExecutor(
                max_workers=MAX_CONCURRENT_LOOKUPS, thread_name_prefix="qq-lookup"
            )
        return _executor


async def _acquire_slot(timeout: float) -> bool:
    """Poll for a free slot without blocking the loop; False on timeout."""
    deadline = time.monotonic() + timeout
    while True:
        if _slots.acquire(blocking=False):
            return True
        if time.monotonic() >= deadline:
            return False
        await asyncio.sleep(_SLOT_POLL_INTERVAL)


def _run_holding_slot(fn: Callable[..., str], *args: Any) -> str:
    """Worker-thread body. The permit is released HERE, when the helper has
    actually returned: cancelling the awaiting coroutine (client gone) cannot
    stop the thread, so releasing on the loop side would let a fresh request
    in while the orphaned helper still holds the GIL."""
    try:
        return fn(*args)
    finally:
        _slots.release()


def _clip(text: str) -> str:
    if len(text) <= MAX_LOOKUP_OUTPUT:
        return text
    return text[:MAX_LOOKUP_OUTPUT] + TRUNCATION_NOTICE.format(cap=MAX_LOOKUP_OUTPUT)


def missing_argument(name: str, args: Dict[str, Any]) -> Optional[str]:
    """The reply to a lookup without its required argument, else None.

    Answered before the budget is consulted, and never charged: it is a
    malformed call, not a lookup.
    """
    if name == "read_file" and not str(args.get("path") or "").strip():
        return PATH_REQUIRED
    if name == "grep_repo" and not str(args.get("pattern") or "").strip():
        return PATTERN_REQUIRED
    return None


def lookup_budget_capability(budget: ToolBudget) -> ToolBudgetCapability[Any]:
    """The quick-question agent's budget: `budget.max_calls` lookups."""

    def spent(name: str, refusal: Refusal, budget: ToolBudget) -> str:
        return BUDGET_SPENT.format(max=budget.max_calls)

    return ToolBudgetCapability(
        budget_for=lambda ctx: budget, refuse=spent, free_reply=missing_argument
    )


class QuickQuestionRepoTools:
    """Serialized lookups over one `RepoPrelude`.

    One instance per question, like the snapshot it reads. The lookup budget
    is not kept here: `lookup_budget_capability` enforces it around the
    agent's tools.
    """

    def __init__(self, prelude: RepoPrelude) -> None:
        self._prelude = prelude
        self._lock = asyncio.Lock()

    async def _lookup(self, tool: str, fn: Callable[..., str], *args: Any) -> str:
        try:
            # The prelude is not thread-safe and its DoS budgets are per
            # feed; pydantic-ai runs a response's tool calls in parallel, so
            # one lookup at a time per request, each with a fresh allowance.
            # The per-request lock comes BEFORE the process-wide slot so a
            # request's parallel calls don't hold slots while queued on it.
            async with self._lock:
                if not await _acquire_slot(LOOKUP_SLOT_WAIT):
                    # Not the model's doing: a busy worker costs no lookup.
                    raise NotCharged(LOOKUPS_BUSY.format(tool=tool))
                self._prelude.start_call()
                loop = asyncio.get_running_loop()
                try:
                    future = loop.run_in_executor(
                        _get_executor(), _run_holding_slot, fn, *args
                    )
                except RuntimeError:
                    # Never submitted, so the worker-side release never runs.
                    _slots.release()
                    raise
                output = await future
        except NotCharged:
            raise
        except Exception as exc:
            # A failed lookup must degrade into a note for the model, never
            # into a failed answer.
            logger.warning(
                "Quick-question %s lookup failed: %s: %s",
                tool,
                type(exc).__name__,
                exc,
                exc_info=True,
            )
            return LOOKUP_FAILED.format(tool=tool)
        return _clip(str(output))

    async def tree(self, path: str, max_depth: int) -> str:
        target = str(path or "").strip() or VIRTUAL_ROOT
        depth = max(1, min(_as_int(max_depth, 3), MAX_TREE_DEPTH))
        return await self._lookup("tree", self._prelude.tree, target, depth)

    async def read_file(self, path: str, start: int, end: Optional[int]) -> str:
        target = str(path or "").strip()
        if not target:
            return PATH_REQUIRED
        start_line = max(1, _as_int(start, 1))
        end_line = None if end is None else max(start_line, _as_int(end, start_line))
        return await self._lookup(
            "read_file", self._prelude.read, target, start_line, end_line
        )

    async def grep_repo(
        self,
        pattern: str,
        path: Optional[str],
        glob: Optional[str],
        max_results: int,
    ) -> str:
        text = str(pattern or "").strip()
        if not text:
            return PATTERN_REQUIRED
        target = str(path or "").strip() or VIRTUAL_ROOT
        glob_filter = str(glob or "").strip() or "*"
        cap = max(1, min(_as_int(max_results, DEFAULT_GREP_RESULTS), MAX_RESULTS))
        return await self._lookup(
            "grep_repo",
            self._prelude.grep,
            text,
            target,
            glob_filter,
            2,  # context lines
            cap,
        )


def _as_int(value: Any, fallback: int) -> int:
    """Tolerant int coercion — the model's arguments are untrusted input."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


def register_repo_tools(agent: Agent[Any, str], tools: QuickQuestionRepoTools) -> None:
    """Register the three read-only lookups on a quick-question agent.

    `tool_plain` (not `tool`): the quick-question agent has no deps — the
    snapshot is bound to `tools` for the lifetime of the request.
    """

    @agent.tool_plain(
        name="tree",
        description=(
            "List the repository's files and directories under a path, from "
            "the snapshot manifest. Paths are rooted at /repo (e.g. "
            "'/repo/src'). Use it to locate a file before reading it."
        ),
    )
    async def tree_tool(path: str = VIRTUAL_ROOT, max_depth: int = 3) -> str:
        return await tools.tree(path, max_depth)

    @agent.tool_plain(
        name="read_file",
        description=(
            "Read another file from the repository, line-numbered, at most "
            "400 lines per call. Paths are rooted at /repo. The file the user "
            "selected from is ALREADY in your prompt — use this for the "
            "definitions, callers and config it refers to."
        ),
    )
    async def read_file_tool(
        path: str, start: int = 1, end: Optional[int] = None
    ) -> str:
        return await tools.read_file(path, start, end)

    @agent.tool_plain(
        name="grep_repo",
        description=(
            "Regex-search the repository; returns 'file:line: text' with "
            "context. Narrow it with path='/repo/subdir' or a simple "
            "extension filter glob='*.py' — a whole-repo search can exhaust "
            "the search budget and return nothing."
        ),
    )
    async def grep_repo_tool(
        pattern: str,
        path: Optional[str] = None,
        glob: Optional[str] = None,
        max_results: int = DEFAULT_GREP_RESULTS,
    ) -> str:
        return await tools.grep_repo(pattern, path, glob, max_results)
