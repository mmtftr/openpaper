"""
Single-paper agentic tools: read_section, read_pages, search_paper, get_figure.

Every tool reads the paper's per-page text and figures from `paper_pages` /
`paper_figures` (via `app.ingest.content`). read_section returns
{error, available_sections} on no-match
rather than silently fuzzy-picking the wrong section — honest failure beats
hallucination, and the agent can pick again from the listed top-level headings.

Outputs cap at ~8k tokens with `truncated: true` plus a hint at what was left
out: `read_section` reports the section's `full_pages`, `read_pages` cuts on a
page boundary and reports the `pages_returned` plus the `next_page` to resume
from, so the agent can fill in the gap with another call.
"""

import re
import time
from logging import getLogger
from typing import Any, Dict, List, Optional, Tuple

import regex
from sqlalchemy.orm import Session

from app.database.crud.paper_crud import paper_crud
from app.database.models import Paper
from app.ingest import content
from app.ingest.content import PAGE_SEPARATOR, Figure, Page
from app.schemas.user import CurrentUser

logger = getLogger(__name__)


# Roughly 4 chars/token on English prose; 8k tokens => ~32k chars. We cap
# tool output at this so the agentic loop stays within budget.
RESPONSE_CHAR_CAP = 32_000


# --------------------------------------------------------------
# Helpers
# --------------------------------------------------------------

# Markdown ATX heading: '#'..'######' followed by space and text.
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*?)\s*$", re.MULTILINE)


def _heading_level(line: str) -> Optional[int]:
    m = re.match(r"^(#{1,6})\s+", line)
    return len(m.group(1)) if m else None


def _strip_images(md: str) -> str:
    return re.sub(r"!\[[^\]]*\]\([^\)]*\)", "", md)


def _truncate(text: str) -> Tuple[str, bool]:
    if len(text) <= RESPONSE_CHAR_CAP:
        return text, False
    return text[:RESPONSE_CHAR_CAP], True


def _load_pages(db: Session, paper: Paper) -> List[Page]:
    return content.pages(db, paper.id)


def _load_figures(db: Session, paper: Paper) -> List[Figure]:
    return content.figures(db, paper.id)


def _get_paper_or_raise(
    paper_id: str,
    current_user: CurrentUser,
    db: Session,
    allowed_paper_ids: Optional[List[str]] = None,
) -> Paper:
    """Fetch a paper, gating by ownership OR membership in `allowed_paper_ids`.

    The allow-list is the chat-resolved set of papers the agent is permitted
    to read this turn (the parent + its supplementary papers). When a
    `paper_id` is in that list we still go through `paper_crud.get` with the
    current user — supplementaries inherit the parent's user — so the row
    must both exist and be accessible to the user. The list only narrows what
    the agent can target, never widens what a user can read.
    """
    paper = paper_crud.get(db, id=paper_id, user=current_user)
    if paper:
        if allowed_paper_ids is not None and str(paper.id) not in [
            str(p) for p in allowed_paper_ids
        ]:
            raise ValueError(
                f"Paper {paper_id} is not in the allowed paper family for this chat"
            )
        return paper
    raise ValueError(f"Paper {paper_id} not found or access denied")


def _resolve_target_paper_id(
    default_paper_id: str,
    target_paper_id: Optional[str],
    allowed_paper_ids: Optional[List[str]],
) -> str:
    """Pick the paper id a tool call should hit.

    Defaults to the parent paper. If the model explicitly passed
    `target_paper_id`, validate it against `allowed_paper_ids` (when
    supplied) so it cannot reach outside the family.
    """
    if not target_paper_id:
        return default_paper_id
    if allowed_paper_ids and str(target_paper_id) not in [
        str(p) for p in allowed_paper_ids
    ]:
        # Fall back to the parent rather than raising — the tool will then
        # surface a normal "not found" if the agent retries with garbage.
        return default_paper_id
    return str(target_paper_id)


# --------------------------------------------------------------
# Outline builder — used for the system prompt, not as a tool.
# --------------------------------------------------------------


def build_outline(
    paper: Paper, pages: List[Page], figures: List[Figure]
) -> Dict[str, Any]:
    """Build the paper outline injected into every system prompt: the
    headings of every page's markdown plus the figures map."""
    headings: List[Dict[str, Any]] = []
    for page in pages:
        for m in _HEADING_RE.finditer(page.markdown):
            level = len(m.group(1))
            text = m.group(2).strip()
            headings.append({"level": level, "text": text, "page": page.page_no})

    figure_entries = [
        {
            "label": fig.label,
            "page": fig.page_no,
            "caption": fig.caption,
            "id": str(fig.id),
            "available": fig.available,
        }
        for fig in figures
    ]

    return {
        "title": getattr(paper, "title", None) or "(untitled)",
        "authors": getattr(paper, "authors", None) or [],
        "page_count": getattr(paper, "page_count", None),
        "headings": headings,
        "figures": figure_entries,
    }


def render_outline_text(outline: Dict[str, Any]) -> str:
    """Format the outline as a markdown string suitable for a system prompt.

    Cheap to compute, ~1-2k tokens, saves the agent at least one tool call on
    nearly every query. Keeps page numbers attached so the agent can reach
    for read_pages or read_section confidently.
    """
    lines: List[str] = []
    lines.append(f"# {outline['title']}")
    if outline.get("authors"):
        lines.append("**Authors**: " + ", ".join(outline["authors"]))
    if outline.get("page_count"):
        lines.append(f"**Pages**: {outline['page_count']}")

    headings = outline.get("headings") or []
    if headings:
        lines.append("\n**Section outline**:")
        for h in headings:
            indent = "  " * max(h["level"] - 1, 0)
            page_suffix = f" (p. {h['page']})" if h.get("page") else ""
            lines.append(f"{indent}- {h['text']}{page_suffix}")

    figures = outline.get("figures") or []
    if figures:
        lines.append("\n**Figures**:")
        for fig in figures:
            label = fig.get("label") or f"[unlabeled {fig.get('id') or 'figure'}]"
            page_suffix = f" (p. {fig['page']})" if fig.get("page") else ""
            cap = fig.get("caption")
            cap_suffix = f": {cap}" if cap else ""
            avail = "" if fig.get("available") else " [not yet rendered]"
            lines.append(f"- {label}{page_suffix}{cap_suffix}{avail}")

    return "\n".join(lines)


# --------------------------------------------------------------
# Tool implementations
# --------------------------------------------------------------


def _read_section(pages: List[Page], name: str, text_only: bool) -> Dict[str, Any]:
    if not pages:
        return {"error": "Paper has no parsed pages"}

    name_lc = name.strip().lower()

    # First pass: find a matching heading. Each heading carries (page_num,
    # absolute char offset within the joined content, line, level).
    matches: List[Tuple[int, int, str, int]] = []
    available_top: List[str] = []
    page_starts: List[int] = []

    flat, offsets = content.full_text(pages)
    for page in pages:
        page_start = offsets[page.page_no][0]
        page_starts.append(page_start)
        for m in _HEADING_RE.finditer(page.markdown):
            level = len(m.group(1))
            text = m.group(2).strip()
            if level <= 2:
                available_top.append(text)
            if name_lc in text.lower():
                matches.append((page.page_no, page_start + m.start(), text, level))

    if not matches:
        return {
            "error": f"No section matching '{name}'",
            "available_sections": available_top,
        }

    # Use the first match.
    page_num, abs_offset, heading_text, level = matches[0]

    # Find the next heading at the same-or-higher level (smaller-or-equal #
    # count means same or higher importance).
    next_offset = len(flat)
    for m in _HEADING_RE.finditer(flat, abs_offset + 1):
        m_level = len(m.group(1))
        if m_level <= level:
            next_offset = m.start()
            break

    body = flat[abs_offset:next_offset]
    if text_only:
        body = _strip_images(body)

    out, truncated = _truncate(body)
    response: Dict[str, Any] = {
        "section": heading_text,
        "level": level,
        "page": page_num,
        "content": out,
    }
    if truncated:
        # Tell the agent which pages it's missing so it can read_pages for
        # the rest deterministically rather than guessing.
        response["truncated"] = True
        end_index = _page_index_for_offset(page_starts, next_offset)
        response["full_pages"] = [page_num, pages[end_index].page_no]
    return response


def _page_index_for_offset(page_starts: List[int], offset: int) -> int:
    """Index (into `page_starts`) of the page containing the absolute offset."""
    for i in range(len(page_starts) - 1):
        if offset < page_starts[i + 1]:
            return i
    return len(page_starts) - 1


def read_section(
    paper_id: str,
    name: str,
    current_user: CurrentUser,
    db: Session,
    text_only: bool = False,
    project_id: Optional[str] = None,
    target_paper_id: Optional[str] = None,
    allowed_paper_ids: Optional[List[str]] = None,
) -> Dict[str, Any]:
    effective_id = _resolve_target_paper_id(
        paper_id, target_paper_id, allowed_paper_ids
    )
    paper = _get_paper_or_raise(effective_id, current_user, db, allowed_paper_ids)
    return _read_section(_load_pages(db, paper), name, text_only)


def _paged_response(
    *,
    start: int,
    end: int,
    content: str,
    first_page: int,
    last_page: int,
    truncated: bool,
    page_truncated: bool = False,
) -> Dict[str, Any]:
    """Build the `read_pages` payload.

    `pages` echoes what was asked for (kept for backwards compatibility);
    `pages_returned` is what actually came back. When the cap cut the range
    short, `next_page` is where the agent should resume — unless the cut
    landed inside a single oversized page, in which case there is no clean
    boundary to resume from and `page_truncated` says so instead.
    """
    response: Dict[str, Any] = {
        "pages": [start, end],
        "content": content,
        "pages_returned": [first_page, last_page],
    }
    if truncated:
        response["truncated"] = True
        if page_truncated:
            response["page_truncated"] = True
        else:
            response["next_page"] = last_page + 1
    return response


def _read_pages(pages: List[Page], start: int, end: int) -> Dict[str, Any]:
    """Whole-page reads off the per-page markdown."""
    selected = [(p.page_no, p.markdown) for p in pages if start <= p.page_no <= end]
    if not selected:
        return {"error": f"No pages found in range {start}-{end}"}

    first_page = selected[0][0]
    if len(selected[0][1]) > RESPONSE_CHAR_CAP:
        # One page bigger than the entire budget: hard-cut it rather than
        # returning nothing, and flag that the cut is mid-page.
        return _paged_response(
            start=start,
            end=end,
            content=selected[0][1][:RESPONSE_CHAR_CAP],
            first_page=first_page,
            last_page=first_page,
            truncated=True,
            page_truncated=True,
        )

    chunks: List[str] = []
    length = 0
    last_page = first_page
    truncated = False
    for page_num, md in selected:
        addition = (len(PAGE_SEPARATOR) if chunks else 0) + len(md)
        if length + addition > RESPONSE_CHAR_CAP:
            truncated = True
            break
        chunks.append(md)
        length += addition
        last_page = page_num

    return _paged_response(
        start=start,
        end=end,
        content=PAGE_SEPARATOR.join(chunks),
        first_page=first_page,
        last_page=last_page,
        truncated=truncated,
    )


def read_pages(
    paper_id: str,
    start: int,
    end: int,
    current_user: CurrentUser,
    db: Session,
    project_id: Optional[str] = None,
    target_paper_id: Optional[str] = None,
    allowed_paper_ids: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Read a 1-indexed inclusive page range, truncating on page boundaries.

    The cap is applied per page rather than per character so the agent never
    gets a page cut mid-sentence without knowing where the cut fell: the
    response always says which pages came back (`pages_returned`) and, when
    the cap stopped it early, where to resume (`next_page`).
    """
    effective_id = _resolve_target_paper_id(
        paper_id, target_paper_id, allowed_paper_ids
    )
    paper = _get_paper_or_raise(effective_id, current_user, db, allowed_paper_ids)

    if start < 1 or end < start:
        return {"error": "Invalid page range"}

    return _read_pages(_load_pages(db, paper), start, end)


# Total wall-clock budget for one `search_paper` call. A model-written regex
# with catastrophic backtracking would otherwise hang the chat turn.
SEARCH_TIME_BUDGET_S = 2.0


class _TimedPattern:
    """A compiled `regex` pattern whose searches share one deadline."""

    def __init__(self, query: str, budget_s: float) -> None:
        self._pattern = regex.compile(query, regex.IGNORECASE)
        self._deadline = time.monotonic() + budget_s

    def search(self, text: str):
        remaining = self._deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError
        return self._pattern.search(text, timeout=remaining)


def _search_pages(
    pages: List[Page],
    pattern: _TimedPattern,
    context_lines: int,
    paper_id_for_tagging: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Run the per-paper search loop. Hits are tagged with `paper_id` when
    provided so callers searching across the parent + supplementaries can
    tell which paper each hit came from."""
    hits: List[Dict[str, Any]] = []
    for page in pages:
        lines = page.markdown.splitlines()
        for line_num, line in enumerate(lines):
            if pattern.search(line):
                before = lines[max(0, line_num - context_lines) : line_num]
                after = lines[
                    line_num + 1 : min(len(lines), line_num + 1 + context_lines)
                ]
                hit: Dict[str, Any] = {
                    "page": page.page_no,
                    "line": line_num + 1,
                    "match": line,
                    "before": before,
                    "after": after,
                }
                if paper_id_for_tagging:
                    hit["paper_id"] = paper_id_for_tagging
                hits.append(hit)
    return hits


def search_paper(
    paper_id: str,
    query: str,
    current_user: CurrentUser,
    db: Session,
    context_lines: int = 3,
    project_id: Optional[str] = None,
    target_paper_id: Optional[str] = None,
    allowed_paper_ids: Optional[List[str]] = None,
) -> Dict[str, Any]:
    try:
        pattern = _TimedPattern(query, SEARCH_TIME_BUDGET_S)
    except (regex.error, ValueError, KeyError) as e:
        # `regex` raises ValueError/KeyError for a few malformed inline flags.
        return {"error": f"Invalid regex: {e}"}
    try:
        return _search_paper(
            paper_id,
            pattern,
            current_user,
            db,
            context_lines,
            target_paper_id,
            allowed_paper_ids,
        )
    except TimeoutError:
        return {
            "error": f"Regex search timed out after {SEARCH_TIME_BUDGET_S:.0f}s; "
            "use a simpler pattern."
        }


def _search_paper(
    paper_id: str,
    pattern: _TimedPattern,
    current_user: CurrentUser,
    db: Session,
    context_lines: int,
    target_paper_id: Optional[str],
    allowed_paper_ids: Optional[List[str]],
) -> Dict[str, Any]:

    # When the agent passes no explicit target, search across the whole
    # paper family (parent + supplementaries). Each hit is tagged with the
    # paper_id it came from so the agent can follow up with `read_section`
    # or `read_pages` against the right paper.
    if target_paper_id is None and allowed_paper_ids and len(allowed_paper_ids) > 1:
        all_hits: List[Dict[str, Any]] = []
        for pid in allowed_paper_ids:
            try:
                p = _get_paper_or_raise(pid, current_user, db, allowed_paper_ids)
            except ValueError:
                continue
            all_hits.extend(
                _search_pages(
                    _load_pages(db, p),
                    pattern,
                    context_lines,
                    paper_id_for_tagging=str(pid),
                )
            )
        return _cap_hits(all_hits)

    effective_id = _resolve_target_paper_id(
        paper_id, target_paper_id, allowed_paper_ids
    )
    paper = _get_paper_or_raise(effective_id, current_user, db, allowed_paper_ids)
    hits = _search_pages(_load_pages(db, paper), pattern, context_lines)
    return _cap_hits(hits)


def _cap_hits(hits: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Cap the result set to keep tool output bounded."""
    serialized = repr(hits)
    if len(serialized) > RESPONSE_CHAR_CAP:
        capped: List[Dict[str, Any]] = []
        size = 0
        for hit in hits:
            chunk = repr(hit)
            if size + len(chunk) > RESPONSE_CHAR_CAP:
                break
            capped.append(hit)
            size += len(chunk)
        return {"hits": capped, "truncated": True, "total_hits": len(hits)}
    return {"hits": hits, "truncated": False, "total_hits": len(hits)}


def find_figure(db: Session, paper: Paper, label: str) -> Dict[str, Any]:
    """`{"figure": Figure}` for a label/id on this paper, or `{"error": ...}`
    (no such figure, or not rendered yet). Shared by both figure tools."""
    figure = content.resolve_figure(_load_figures(db, paper), label)
    if figure is None:
        return {"error": f"No figure matching '{label}'"}
    if not figure.available:
        return {"error": f"Figure '{figure.label or label}' is not yet rendered"}
    return {"figure": figure}


def get_figure(
    paper_id: str,
    label: str,
    current_user: CurrentUser,
    db: Session,
    project_id: Optional[str] = None,
    target_paper_id: Optional[str] = None,
    allowed_paper_ids: Optional[List[str]] = None,
) -> Dict[str, Any]:
    effective_id = _resolve_target_paper_id(
        paper_id, target_paper_id, allowed_paper_ids
    )
    paper = _get_paper_or_raise(effective_id, current_user, db, allowed_paper_ids)
    found = find_figure(db, paper, label)
    if "error" in found:
        return found
    figure: Figure = found["figure"]

    # The chat UI fetches the bitmap via the figure endpoint; the agent gets
    # back the metadata + endpoint path so it can answer with caption text
    # and reference the image inline.
    return {
        "label": figure.label,
        "page": figure.page_no,
        "caption": figure.caption,
        "id": str(figure.id),
        "paper_id": effective_id,
        "url": f"/api/paper/{effective_id}/figure/{figure.id}",
    }
