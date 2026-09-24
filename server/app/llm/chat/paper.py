"""Single-paper agent: system prompt assembly + pydantic-ai Agent build.

Ported from `paper_agentic_operations.py` / `paper_pydantic_agent.py` with
capabilities driven by the ModelSpec (vision decides whether `get_figure`
returns the rendered image) and per-tool telemetry recorded in the tool
executor (the event stream no longer needs to track timings).
"""

from __future__ import annotations

import asyncio
import contextvars
import logging
import re
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence

from pydantic_ai import Agent, RunContext
from pydantic_ai.messages import BinaryImage, ToolReturn
from sqlalchemy.orm import Session

from app.database.crud.paper_crud import paper_crud
from app.database.models import Paper
from app.database.telemetry import track_event
from app.helpers.s3 import s3_service
from app.ingest import content
from app.ingest.content import PAGE_SEPARATOR, Figure, Page
from app.llm.chat.budget import Refusal, ToolBudget, ToolBudgetCapability
from app.llm.chat.history import FIGURE_ID_PREFIX
from app.llm.model_registry import ModelSpec
from app.llm.prompts import (
    ADAPTIVE_MODE_PRELOAD,
    COMPREHENSIVE_MODE_PRELOAD,
    CONCISE_MODE_INSTRUCTIONS,
    DETAILED_MODE_INSTRUCTIONS,
    FULL_MODE_PRELOAD,
    NORMAL_MODE_INSTRUCTIONS,
    PAPER_AGENT_BASE,
)
from app.llm.tools.doc_tools import list_docs, read_doc, write_doc
from app.llm.tools.section_tools import (
    build_outline,
    find_figure,
    read_pages,
    read_section,
    render_outline_text,
    search_paper,
)
from app.schemas.message import ResponseStyle
from app.schemas.user import CurrentUser

logger = logging.getLogger(__name__)

# "adaptive" | "comprehensive" | "full".
ContextMode = str

MAX_AGENTIC_ITERATIONS = 60  # Tool-call budget for retrieval-heavy turns.

# Sub-budget inside the shared tool budget: repo exploration must not be able
# to consume the paper tools' whole allowance on a paper+code question.
MAX_REPO_TOOL_CALLS = 50

_tool_executor = ThreadPoolExecutor(max_workers=4)

TOOL_BUDGET_EXHAUSTED = {
    "error": "ran_out_of_tool_calls",
    "message": (
        "The paper agent has run out of tool calls for this turn. "
        "Do not call another tool. Finish the answer using the evidence and "
        "context already gathered, and mention any remaining uncertainty."
    ),
}

REPO_BUDGET_EXHAUSTED = {
    "files": [],
    "output": (
        "[budget] run_python has been called the maximum number of times for "
        "this turn. Do not call it again — answer from what you have already "
        "read, citing the files and line ranges you saw."
    ),
}


def paper_tool_budget() -> ToolBudget:
    """A fresh per-turn budget: MAX_AGENTIC_ITERATIONS calls over all tools,
    at most MAX_REPO_TOOL_CALLS of them `run_python`."""
    return ToolBudget(
        max_calls=MAX_AGENTIC_ITERATIONS,
        per_tool={"run_python": MAX_REPO_TOOL_CALLS},
    )


@dataclass
class PaperAgentDeps:
    paper_id: str
    paper: Paper
    current_user: CurrentUser
    db: Session
    context_mode: ContextMode
    # Paper ids the agent is allowed to read this turn — parent + any
    # supplementary papers.
    allowed_paper_ids: List[str] = field(default_factory=list)
    # Live sandbox for the companion repo (None when no repo is connected).
    # Owned by `run_paper_chat`, which opens it before the run and closes it
    # in its `finally`.
    repo_sandbox: Optional[Any] = None
    # This turn's tool calls, counted and capped by `ToolBudgetCapability`.
    tool_budget: ToolBudget = field(default_factory=paper_tool_budget)


# ---------------------------------------------------------------------
# Chat context: effective mode, supplementaries, system prompt
# ---------------------------------------------------------------------


@dataclass
class PaperChatContext:
    paper: Paper
    context_mode: ContextMode
    system_prompt: str
    supplementary_papers: List[Paper]
    allowed_paper_ids: List[str]
    family_index: Dict[str, Paper]
    # Published snapshot of the paper's companion repo, when one is ready.
    repo_snapshot: Optional[Any] = None
    # The paper text pre-loaded into the system prompt for `context_mode`.
    preload: str = ""


def _load_repo_snapshot(db: Session, paper_id: str) -> Optional[Any]:
    """Return the ready snapshot for this paper, or None.

    Non-fatal by design: a missing/corrupt snapshot degrades the turn to a
    paper-only chat rather than failing it.
    """
    try:
        from app.database.crud.paper_repo_crud import paper_repo_crud
        from app.llm.repo.sandbox import load_snapshot

        row = paper_repo_crud.get_ready_for_paper(db, paper_id=uuid.UUID(paper_id))
        if row is None:
            return None
        return load_snapshot(paper_id=paper_id, commit_sha=str(row.commit_sha))
    except Exception as exc:
        logger.warning("Failed to load repo snapshot for %s: %s", paper_id, exc)
        return None


def build_paper_chat_context(
    db: Session,
    *,
    paper_id: str,
    current_user: CurrentUser,
    context_mode: ContextMode = "adaptive",
    response_style: Optional[str] = None,
) -> PaperChatContext:
    paper: Optional[Paper] = paper_crud.get(db, id=paper_id, user=current_user)
    if not paper:
        raise ValueError(f"Paper with ID {paper_id} not found.")

    pages = content.pages(db, paper.id)
    figures = content.figures(db, paper.id)

    supplementary_papers: List[Paper] = []
    try:
        supplementary_papers = list(
            paper_crud.list_supplementary_for(
                db, parent_paper_id=uuid.UUID(paper_id), user=current_user
            )
            or []
        )
    except Exception as exc:
        logger.warning("Failed to load supplementary papers for %s: %s", paper_id, exc)

    repo_snapshot = _load_repo_snapshot(db, paper_id)
    preload = _select_preload(context_mode, paper, pages)
    system_prompt = _build_system_prompt(
        paper,
        pages,
        figures,
        preload,
        context_mode,
        response_style,
        supplementary_papers,
        repo_snapshot,
    )
    family_index: Dict[str, Paper] = {paper_id: paper}
    for sup in supplementary_papers:
        family_index[str(sup.id)] = sup

    return PaperChatContext(
        paper=paper,
        context_mode=context_mode,
        system_prompt=system_prompt,
        supplementary_papers=supplementary_papers,
        allowed_paper_ids=list(family_index.keys()),
        family_index=family_index,
        repo_snapshot=repo_snapshot,
        preload=preload,
    )


def _additional_instructions(response_style: Optional[str]) -> str:
    if response_style == ResponseStyle.DETAILED:
        return DETAILED_MODE_INSTRUCTIONS
    if response_style == ResponseStyle.CONCISE:
        return CONCISE_MODE_INSTRUCTIONS
    return NORMAL_MODE_INSTRUCTIONS


_BACK_MATTER_RE = re.compile(
    r"^\s*#{1,6}\s+(references|bibliography|appendix|appendices|acknowledg(e)?ments?)\b",
    re.IGNORECASE | re.MULTILINE,
)


def _first_back_matter_page(pages: Sequence[Page]) -> Optional[int]:
    for i, page in enumerate(pages):
        if _BACK_MATTER_RE.search(page.markdown):
            return i
    return None


def _select_preload(mode: ContextMode, paper: Paper, pages: Sequence[Page]) -> str:
    """Return the body content pre-loaded for this mode."""
    page_md = [p.markdown for p in pages]

    if mode == "full":
        return PAGE_SEPARATOR.join(page_md)

    if mode == "comprehensive":
        cutoff_page_idx = _first_back_matter_page(pages)
        if cutoff_page_idx is None:
            return PAGE_SEPARATOR.join(page_md)
        return PAGE_SEPARATOR.join(page_md[:cutoff_page_idx])

    return _adaptive_preload(paper, pages)


def _adaptive_preload(paper: Paper, pages: Sequence[Page]) -> str:
    """Abstract + introduction + conclusion, or the full body as fallback."""
    if not pages:
        return str(getattr(paper, "abstract", "") or "")

    targets = ["abstract", "introduction", "conclusion"]
    chunks: List[str] = []

    abstract = str(getattr(paper, "abstract", "") or "").strip()
    if abstract:
        chunks.append(f"## Abstract\n\n{abstract}")

    flat_pages = PAGE_SEPARATOR.join(p.markdown for p in pages)
    heading_re = re.compile(r"^(#{1,6})\s+(.*?)\s*$", re.MULTILINE)
    matches = list(heading_re.finditer(flat_pages))

    for target in targets:
        if target == "abstract" and abstract:
            continue
        for i, m in enumerate(matches):
            text = m.group(2).strip().lower()
            if target in text:
                level = len(m.group(1))
                start = m.start()
                end = len(flat_pages)
                for next_m in matches[i + 1 :]:
                    if len(next_m.group(1)) <= level:
                        end = next_m.start()
                        break
                chunks.append(flat_pages[start:end].strip())
                break

    if not chunks:
        return flat_pages
    return "\n\n".join(chunks)


def _render_supplementary_block(supplementary_papers: Sequence[Paper]) -> str:
    if not supplementary_papers:
        return ""
    lines: List[str] = [
        "## Supplementary materials attached to this paper",
        (
            "These are accessible via the same tools (read_section, read_pages, "
            "search_paper, get_figure) by passing their paper_id. Treat them as "
            "additional sections of the main paper. Calling search_paper without "
            "a paper_id searches the whole family at once and tags each hit with "
            "the paper_id it came from."
        ),
        "",
    ]
    for sup in supplementary_papers:
        sup_id = str(getattr(sup, "id", "") or "")
        sup_title = str(getattr(sup, "title", "") or "(untitled)")
        sup_pages = getattr(sup, "page_count", None)
        pages_suffix = f" ({sup_pages} pages)" if sup_pages else ""
        lines.append(f'- paper_id={sup_id} — "{sup_title}"{pages_suffix}')
    lines.append("")
    lines.append(
        "When citing evidence from a supplementary, extend the evidence-block "
        "marker with the supplementary's paper_id, e.g. "
        "`@cite[3|page=2|paper_id=<id>]`. Citations without a paper_id are "
        "treated as belonging to the main paper."
    )
    return "\n".join(lines)


def _build_system_prompt(
    paper: Paper,
    pages: List[Page],
    figures: List[Figure],
    preload: str,
    mode: ContextMode,
    response_style: Optional[str],
    supplementary_papers: Sequence[Paper],
    repo_snapshot: Optional[Any] = None,
) -> str:
    outline = build_outline(paper, pages, figures)
    outline_text = render_outline_text(outline)
    base = PAPER_AGENT_BASE.format(
        outline=outline_text,
        additional_instructions=_additional_instructions(response_style),
    )

    supplementary_block = _render_supplementary_block(supplementary_papers)
    if supplementary_block:
        base = base + "\n\n" + supplementary_block

    # Only describe the sandbox when the tool is actually registered — an
    # unbacked instruction would have the model call a tool that isn't there.
    if repo_snapshot is not None:
        from app.llm.repo.prompt import build_repo_prompt_section

        base = base + "\n\n" + build_repo_prompt_section(repo_snapshot)

    if mode == "full":
        return base + "\n\n" + FULL_MODE_PRELOAD.format(preloaded_content=preload)
    if mode == "comprehensive":
        return (
            base + "\n\n" + COMPREHENSIVE_MODE_PRELOAD.format(preloaded_content=preload)
        )
    return base + "\n\n" + ADAPTIVE_MODE_PRELOAD.format(preloaded_content=preload)


# ---------------------------------------------------------------------
# Agent build
# ---------------------------------------------------------------------


def _refuse_paper_tool(name: str, refusal: Refusal, budget: ToolBudget) -> Any:
    """The tool result for a call past the turn's budget."""
    if refusal == "tool":
        # Only run_python has a cap of its own.
        return dict(REPO_BUDGET_EXHAUSTED)
    if name == "run_python":
        # Keep run_python's result shape: the client renders it as output.
        return {"files": [], "output": f"[budget] {TOOL_BUDGET_EXHAUSTED['message']}"}
    return dict(TOOL_BUDGET_EXHAUSTED)


def _paper_budget(ctx: RunContext[PaperAgentDeps]) -> ToolBudget:
    return ctx.deps.tool_budget


async def _run_sync_tool(
    fn: Callable[..., Dict[str, Any]],
    deps: PaperAgentDeps,
    *,
    tool_name: str,
    **args: Any,
) -> Dict[str, Any]:
    def _call() -> Dict[str, Any]:
        # Each tool call gets its OWN session: pydantic-ai executes a
        # response's tool calls in parallel by default, and SQLAlchemy
        # sessions are not thread-safe — sharing the request session across
        # executor threads corrupts transactions (write_doc even commits).
        from app.database.database import SessionLocal

        session = SessionLocal()
        try:
            return fn(
                paper_id=deps.paper_id,
                current_user=deps.current_user,
                db=session,
                **args,
            )
        finally:
            session.close()

    started = time.time()
    ctx = contextvars.copy_context()
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(_tool_executor, lambda: ctx.run(_call))
    payload: Dict[str, Any] = {
        "tool": tool_name,
        "duration_ms": (time.time() - started) * 1000,
        "context_mode": deps.context_mode,
        "runtime": "pydantic_ai",
    }
    # Cap-hit and failure rates per tool. Guarded on dict because not every
    # tool returns a plain mapping (the figure path returns image payloads).
    if isinstance(result, dict):
        payload["truncated"] = bool(result.get("truncated"))
        payload["error"] = bool(result.get("error"))
    track_event(
        "paper_agentic_tool_call",
        payload,
        user_id=str(deps.current_user.id),
    )
    return result


async def _run_repo_tool(deps: PaperAgentDeps, code: str) -> Dict[str, Any]:
    """Dispatch a `run_python` call.

    Deliberately NOT `_run_sync_tool`: a feed can take 25 s and would starve
    the shared 4-thread tool executor, and this tool needs no DB session.
    Concurrency + poison recovery live in `RepoSandbox`.
    """
    sandbox = deps.repo_sandbox
    if sandbox is None:
        return {
            "files": [],
            "output": "[error] No code repository is connected to this paper.",
        }

    started = time.time()
    result = await sandbox.run(code)
    track_event(
        "paper_repo_run_python",
        {
            "tool": "run_python",
            "duration_ms": (time.time() - started) * 1000,
            "context_mode": deps.context_mode,
            "runtime": "pydantic_ai",
            "files_touched": len(result.get("files") or []),
            "call_index": deps.tool_budget.calls_by_tool["run_python"],
        },
        user_id=str(deps.current_user.id),
    )
    return result


def build_paper_agent(
    *,
    model: Any,
    spec: ModelSpec,
    system_prompt: str,
    paper: Paper,
    context_mode: ContextMode,
    repo_snapshot: Optional[Any] = None,
) -> Agent[PaperAgentDeps, str]:
    supports_vision = spec.supports_vision
    agent: Agent[PaperAgentDeps, str] = Agent(
        model,
        output_type=str,
        instructions=system_prompt,
        deps_type=PaperAgentDeps,
        retries=1,
        end_strategy="early",
        # Every tool below counts against the turn's budget.
        capabilities=[
            ToolBudgetCapability(budget_for=_paper_budget, refuse=_refuse_paper_tool)
        ],
    )

    # The paper-reading tools are registered in EVERY context mode, Full
    # included. The system prompt lists all four unconditionally, so gating
    # them by mode left the model calling tools that weren't there (two such
    # calls exhaust `retries=1` and fail the turn), and Full mode still needs
    # `get_figure` — the pre-loaded markdown carries no figure bitmaps, so a
    # vision model otherwise cannot actually look at a figure.

    @agent.tool(
        name="read_section",
        description=(
            "Read a section by heading. On miss, returns available_sections. "
            "Use text_only=true when figures are not needed. Pass paper_id "
            "to target a supplementary paper; defaults to the main paper."
        ),
    )
    async def read_section_tool(
        ctx: RunContext[PaperAgentDeps],
        name: str,
        text_only: bool = False,
        paper_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        return await _run_sync_tool(
            read_section,
            ctx.deps,
            tool_name="read_section",
            name=name,
            text_only=text_only,
            target_paper_id=paper_id,
            allowed_paper_ids=ctx.deps.allowed_paper_ids,
        )

    @agent.tool(
        name="read_pages",
        description=(
            "Read a contiguous 1-indexed inclusive page range. Output is "
            "capped at whole pages: pages_returned tells you which pages "
            "actually came back and next_page where to continue reading. "
            "Pass paper_id to target a supplementary paper; defaults to the "
            "main paper."
        ),
    )
    async def read_pages_tool(
        ctx: RunContext[PaperAgentDeps],
        start: int,
        end: int,
        paper_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        return await _run_sync_tool(
            read_pages,
            ctx.deps,
            tool_name="read_pages",
            start=start,
            end=end,
            target_paper_id=paper_id,
            allowed_paper_ids=ctx.deps.allowed_paper_ids,
        )

    @agent.tool(
        name="search_paper",
        description=(
            "Regex search the paper. Returns page, line, match, and "
            "surrounding context lines. Omit paper_id to search the main "
            "paper plus all supplementary papers together (each hit is "
            "tagged with its paper_id); pass paper_id to scope to one."
        ),
    )
    async def search_paper_tool(
        ctx: RunContext[PaperAgentDeps],
        query: str,
        context_lines: int = 3,
        paper_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        return await _run_sync_tool(
            search_paper,
            ctx.deps,
            tool_name="search_paper",
            query=query,
            context_lines=context_lines,
            target_paper_id=paper_id,
            allowed_paper_ids=ctx.deps.allowed_paper_ids,
        )

    @agent.tool(
        name="get_figure",
        description=(
            "Fetch a figure or table by label, such as Figure 2 or "
            "Table 4 (or, for an unlabeled figure, its id from the outline). "
            "Returns metadata (label, page, caption) "
            + (
                "plus the rendered image so you can read the figure directly. "
                if supports_vision
                else "— this model doesn't support image input, so "
                "only the caption/label/page is returned, not the "
                "rendered image. "
            )
            + "Pass paper_id to target a supplementary paper; "
            "defaults to the main paper."
        ),
    )
    async def get_figure_tool(
        ctx: RunContext[PaperAgentDeps],
        label: str,
        paper_id: Optional[str] = None,
    ) -> Any:
        payload = await _run_sync_tool(
            _resolve_figure_with_image,
            ctx.deps,
            tool_name="get_figure",
            label=label,
            target_paper_id=paper_id,
            allowed_paper_ids=ctx.deps.allowed_paper_ids,
        )
        if "error" in payload:
            return payload
        if not supports_vision:
            return payload["metadata"]
        return ToolReturn(
            return_value=payload["metadata"],
            content=[
                BinaryImage(
                    data=payload["image_bytes"],
                    media_type=payload["media_type"],
                    # Identifier doubles as the S3 key so the bytes
                    # can be dropped from the persisted dump and
                    # rehydrated on replay.
                    identifier=f"{FIGURE_ID_PREFIX}{payload['s3_key']}",
                )
            ],
        )

    if repo_snapshot is not None:

        @agent.tool(
            name="run_python",
            description=(
                "Run Python in a sandbox with this paper's companion GitHub "
                f"repo ({repo_snapshot.owner}/{repo_snapshot.repo}) mounted "
                "read-only at /repo. Returns whatever the snippet printed. "
                "Helper functions tree(), read(), grep() are pre-defined — "
                "use them instead of hand-rolled directory walks. State "
                "persists between calls within this turn. It is a RESTRICTED "
                "interpreter (no os.walk, no Path.glob, no generators, no "
                "class inheritance, no str.format) — see the system prompt "
                "for the exact subset."
            ),
        )
        async def run_python_tool(
            ctx: RunContext[PaperAgentDeps], code: str
        ) -> Dict[str, Any]:
            return await _run_repo_tool(ctx.deps, code)

    @agent.tool(
        name="list_docs",
        description=(
            "List every writing doc on this paper. Returns name, kind, "
            "revision, updated_at for each. The MAIN doc is always present "
            "as name='main'."
        ),
    )
    async def list_docs_tool(ctx: RunContext[PaperAgentDeps]) -> Dict[str, Any]:
        return await _run_sync_tool(list_docs, ctx.deps, tool_name="list_docs")

    @agent.tool(
        name="read_doc",
        description=(
            "Read a doc by name (use 'main' for the user's primary writeup). "
            "Returns {name, content, revision} or {error: 'not_found', name}. "
            "Pair with write_doc — the revision is required for the "
            "optimistic lock when updating."
        ),
    )
    async def read_doc_tool(
        ctx: RunContext[PaperAgentDeps], name: str
    ) -> Dict[str, Any]:
        return await _run_sync_tool(read_doc, ctx.deps, tool_name="read_doc", name=name)

    @agent.tool(
        name="write_doc",
        description=(
            "Write content to a doc by name. Creates the doc if it doesn't "
            "exist (NOTE kind, except name='main' which targets the MAIN "
            "doc). When updating an existing doc, expected_revision must "
            "come from the most recent read_doc."
        ),
    )
    async def write_doc_tool(
        ctx: RunContext[PaperAgentDeps],
        name: str,
        content: str,
        expected_revision: Optional[int] = None,
    ) -> Dict[str, Any]:
        return await _run_sync_tool(
            write_doc,
            ctx.deps,
            tool_name="write_doc",
            name=name,
            content=content,
            expected_revision=expected_revision,
        )

    return agent


def _resolve_figure_with_image(
    *,
    paper_id: str,
    current_user: CurrentUser,
    db: Session,
    label: str,
    target_paper_id: Optional[str] = None,
    allowed_paper_ids: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Resolve a figure label to metadata + PNG bytes for multimodal return.

    Returns either `{error: ...}` or `{metadata, image_bytes, media_type,
    s3_key}`. Bypasses `section_tools.get_figure` because that one only
    emits a URL — the raw bytes are needed for `BinaryImage` so the model
    can look at the figure rather than just read the caption.
    """
    effective_paper_id = target_paper_id or paper_id
    if (
        target_paper_id is not None
        and allowed_paper_ids
        and effective_paper_id not in allowed_paper_ids
    ):
        return {
            "error": f"paper_id {effective_paper_id} is not part of this paper family"
        }
    paper = paper_crud.get(db, id=effective_paper_id, user=current_user)
    if not paper:
        return {"error": "Paper not found"}
    found = find_figure(db, paper, label)
    if "error" in found:
        return found
    figure: Figure = found["figure"]
    s3_key = str(figure.s3_key)
    try:
        image_bytes = s3_service.get_object_bytes(str(s3_key))
    except Exception as exc:
        logger.warning("Failed to fetch figure %s from S3: %s", s3_key, exc)
        return {"error": "Failed to fetch figure image"}
    return {
        "metadata": {
            "label": figure.label,
            "page": figure.page_no,
            "caption": figure.caption,
            "id": str(figure.id),
            "paper_id": effective_paper_id,
        },
        "image_bytes": image_bytes,
        "media_type": "image/png",
        "s3_key": s3_key,
    }
