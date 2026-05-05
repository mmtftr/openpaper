"""
Single-paper agentic tools: read_section, read_pages, search_paper, get_figure.

Each tool branches on the paper's parser ("mistral" | "pymupdf"). Mistral mode
uses the structured per-page jsonb; pymupdf mode degrades to flat-text fallbacks
over raw_content. read_section returns {error, available_sections} on no-match
rather than silently fuzzy-picking the wrong section — honest failure beats
hallucination, and the agent can pick again from the listed top-level headings.

Outputs cap at ~8k tokens with `truncated: true` plus a `full_pages` hint, so
the agent can `read_pages` to fill in the gap if needed.
"""

import re
import uuid
from logging import getLogger
from typing import Any, Dict, List, Optional, Tuple

from app.database.crud.paper_crud import paper_crud
from app.database.models import Paper
from app.schemas.user import CurrentUser
from sqlalchemy.orm import Session

logger = getLogger(__name__)


# Roughly 4 chars/token on English prose; 8k tokens => ~32k chars. We cap
# tool output at this so the agentic loop stays within budget.
RESPONSE_CHAR_CAP = 32_000


# --------------------------------------------------------------
# Function declarations (LLM-facing schema)
# --------------------------------------------------------------

read_section_function = {
    "name": "read_section",
    "description": (
        "Read a section of the paper by its heading text. Matches the heading "
        "case-insensitively as a substring (e.g. 'methods' matches '## 2 "
        "Methods'). Returns the markdown from the matched heading to the next "
        "heading at the same-or-higher level. On miss, returns "
        "{error, available_sections} listing the top-level headings — "
        "do NOT silently retry with a different name; pick from that list. "
        "Embedded figure references are kept by default; pass text_only=true "
        "for sections you know are pure text."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "The section heading text to match (case-insensitive substring).",
            },
            "text_only": {
                "type": "boolean",
                "description": "If true, strip markdown image references from the output.",
            },
        },
        "required": ["name"],
    },
}


read_pages_function = {
    "name": "read_pages",
    "description": (
        "Read the markdown for a contiguous range of pages (1-indexed, inclusive). "
        "Use as an escape hatch — e.g. 'what's right before Table 3' after a "
        "search_paper hit on page N. Output is capped; the response includes "
        "{truncated, full_pages} when it is."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "start": {
                "type": "integer",
                "description": "First page (1-indexed, inclusive).",
            },
            "end": {
                "type": "integer",
                "description": "Last page (1-indexed, inclusive).",
            },
        },
        "required": ["start", "end"],
    },
}


search_paper_function = {
    "name": "search_paper",
    "description": (
        "Regex search across the paper. Each hit comes back with ±N lines of "
        "surrounding context, the page number, and the matching line. "
        "Prefer this over reading whole sections when the user is asking for "
        "a specific term or phrase."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Regex pattern (case-insensitive).",
            },
            "context_lines": {
                "type": "integer",
                "description": "Lines of context to include on each side. Default 3.",
            },
        },
        "required": ["query"],
    },
}


get_figure_function = {
    "name": "get_figure",
    "description": (
        "Fetch a high-DPI figure by its label ('Figure 2', 'Fig. 3a', "
        "'Table 4') or internal id. Returns the caption, page number, and a "
        "URL the chat UI can render. Returns {error} if the paper was parsed "
        "in fallback mode (no figures available)."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "label": {
                "type": "string",
                "description": "Figure label or internal id.",
            },
        },
        "required": ["label"],
    },
}


# --------------------------------------------------------------
# Helpers shared between Mistral and pymupdf modes
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


def _pages_from_paper(paper: Paper) -> List[Dict[str, Any]]:
    ocr = getattr(paper, "ocr", None)
    if not ocr:
        return []
    return ocr.get("pages") or []


def _is_mistral(paper: Paper) -> bool:
    return str(getattr(paper, "parser", "") or "") == "mistral"


def _get_paper_or_raise(
    paper_id: str, current_user: CurrentUser, db: Session
) -> Paper:
    paper = paper_crud.get(db, id=paper_id, user=current_user)
    if not paper:
        raise ValueError(f"Paper {paper_id} not found or access denied")
    return paper


# --------------------------------------------------------------
# Outline builder — used for the system prompt, not as a tool.
# --------------------------------------------------------------


def build_outline(paper: Paper) -> Dict[str, Any]:
    """Build the paper outline injected into every system prompt.

    For Mistral: walks per-page markdown for headings + figures map.
    For pymupdf fallback: regex over flat raw_content for headings; figures
    are listed as 'unavailable'.
    """
    title = getattr(paper, "title", None) or "(untitled)"
    authors = getattr(paper, "authors", None) or []
    page_count = getattr(paper, "page_count", None)
    parser = str(getattr(paper, "parser", "") or "")

    headings: List[Dict[str, Any]] = []
    figure_entries: List[Dict[str, Any]] = []

    if parser == "mistral":
        pages = _pages_from_paper(paper)
        for page_dict in pages:
            page_idx_raw = page_dict.get("index")
            page_num = (int(page_idx_raw) + 1) if page_idx_raw is not None else None
            md = page_dict.get("markdown") or ""
            for m in _HEADING_RE.finditer(md):
                level = len(m.group(1))
                text = m.group(2).strip()
                headings.append({"level": level, "text": text, "page": page_num})

        ocr = getattr(paper, "ocr", None) or {}
        for fig in ocr.get("figures") or []:
            figure_entries.append(
                {
                    "label": fig.get("label"),
                    "page": fig.get("page"),
                    "caption": fig.get("caption"),
                    "id": fig.get("id"),
                    "available": bool(fig.get("s3_key")),
                }
            )

    else:  # pymupdf fallback
        raw = str(getattr(paper, "raw_content", "") or "")
        for m in _HEADING_RE.finditer(raw):
            level = len(m.group(1))
            text = m.group(2).strip()
            headings.append({"level": level, "text": text, "page": None})

    if page_count is None and parser != "mistral":
        page_offset_map = getattr(paper, "page_offset_map", None) or {}
        page_count = len(page_offset_map) if page_offset_map else None

    return {
        "title": title,
        "authors": authors,
        "page_count": page_count,
        "parser": parser or "pymupdf",
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
# Tool implementations — branch on paper.parser
# --------------------------------------------------------------


def _read_section_mistral(
    paper: Paper, name: str, text_only: bool
) -> Dict[str, Any]:
    pages = _pages_from_paper(paper)
    if not pages:
        return {"error": "Paper has no parsed pages"}

    name_lc = name.strip().lower()

    # First pass: find a matching heading. Each heading carries (page_num,
    # absolute char offset within the joined content, line, level).
    matches: List[Tuple[int, int, str, int]] = []
    available_top: List[str] = []
    flat_chunks: List[str] = []
    page_starts: List[int] = []

    cursor = 0
    sep = "\n\n"
    for i, page_dict in enumerate(pages):
        md = page_dict.get("markdown") or ""
        page_starts.append(cursor)
        flat_chunks.append(md)
        page_idx_raw = page_dict.get("index")
        page_num = (int(page_idx_raw) + 1) if page_idx_raw is not None else (i + 1)
        for m in _HEADING_RE.finditer(md):
            level = len(m.group(1))
            text = m.group(2).strip()
            abs_offset = cursor + m.start()
            if level <= 2:
                available_top.append(text)
            if name_lc in text.lower():
                matches.append((page_num, abs_offset, text, level))
        cursor += len(md)
        if i < len(pages) - 1:
            cursor += len(sep)
            flat_chunks.append(sep)

    if not matches:
        return {
            "error": f"No section matching '{name}'",
            "available_sections": available_top,
        }

    # Use the first match.
    page_num, abs_offset, heading_text, level = matches[0]
    flat = "".join(flat_chunks)

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
        end_page = _page_for_offset(page_starts, next_offset, len(pages))
        response["full_pages"] = [page_num, end_page]
    return response


def _read_section_pymupdf(
    paper: Paper, name: str, text_only: bool
) -> Dict[str, Any]:
    raw = str(getattr(paper, "raw_content", "") or "")
    if not raw:
        return {"error": "Paper has no raw_content"}

    name_lc = name.strip().lower()
    matches: List[Tuple[int, str, int]] = []
    available_top: List[str] = []
    for m in _HEADING_RE.finditer(raw):
        level = len(m.group(1))
        text = m.group(2).strip()
        if level <= 2:
            available_top.append(text)
        if name_lc in text.lower():
            matches.append((m.start(), text, level))

    if not matches:
        return {
            "error": f"No section matching '{name}'",
            "available_sections": available_top,
        }

    abs_offset, heading_text, level = matches[0]
    next_offset = len(raw)
    for m in _HEADING_RE.finditer(raw, abs_offset + 1):
        m_level = len(m.group(1))
        if m_level <= level:
            next_offset = m.start()
            break

    body = raw[abs_offset:next_offset]
    if text_only:
        body = _strip_images(body)

    out, truncated = _truncate(body)
    response: Dict[str, Any] = {
        "section": heading_text,
        "level": level,
        "content": out,
    }
    if truncated:
        response["truncated"] = True
    return response


def _page_for_offset(
    page_starts: List[int], offset: int, total_pages: int
) -> int:
    """Find the 1-indexed page number containing the given absolute offset."""
    for i, start in enumerate(page_starts):
        if i + 1 < len(page_starts) and offset < page_starts[i + 1]:
            return i + 1
        if i + 1 == len(page_starts):
            return i + 1
    return total_pages


def read_section(
    paper_id: str,
    name: str,
    current_user: CurrentUser,
    db: Session,
    text_only: bool = False,
    project_id: Optional[str] = None,
) -> Dict[str, Any]:
    paper = _get_paper_or_raise(paper_id, current_user, db)
    if _is_mistral(paper):
        return _read_section_mistral(paper, name, text_only)
    return _read_section_pymupdf(paper, name, text_only)


def read_pages(
    paper_id: str,
    start: int,
    end: int,
    current_user: CurrentUser,
    db: Session,
    project_id: Optional[str] = None,
) -> Dict[str, Any]:
    paper = _get_paper_or_raise(paper_id, current_user, db)

    if start < 1 or end < start:
        return {"error": "Invalid page range"}

    if _is_mistral(paper):
        pages = _pages_from_paper(paper)
        chunks: List[str] = []
        for page_dict in pages:
            idx_raw = page_dict.get("index")
            if idx_raw is None:
                continue
            page_num = int(idx_raw) + 1
            if start <= page_num <= end:
                chunks.append(page_dict.get("markdown") or "")
        body = "\n\n".join(chunks)
    else:
        # In pymupdf mode, fall back to page_offset_map.
        page_offset_map = getattr(paper, "page_offset_map", None) or {}
        raw = str(getattr(paper, "raw_content", "") or "")
        if not page_offset_map:
            return {"error": "No page offset map available for this paper"}
        # Keys may be ints or stringified ints depending on jsonb shape.

        def _bounds(p: int) -> Optional[List[int]]:
            return page_offset_map.get(p) or page_offset_map.get(str(p))

        start_bounds = _bounds(start)
        end_bounds = _bounds(end)
        if not start_bounds:
            return {"error": f"Page {start} not found"}
        if not end_bounds:
            end_bounds = start_bounds
        body = raw[start_bounds[0] : end_bounds[1]]

    out, truncated = _truncate(body)
    response: Dict[str, Any] = {
        "pages": [start, end],
        "content": out,
    }
    if truncated:
        response["truncated"] = True
    return response


def search_paper(
    paper_id: str,
    query: str,
    current_user: CurrentUser,
    db: Session,
    context_lines: int = 3,
    project_id: Optional[str] = None,
) -> Dict[str, Any]:
    paper = _get_paper_or_raise(paper_id, current_user, db)
    try:
        pattern = re.compile(query, re.IGNORECASE)
    except re.error as e:
        return {"error": f"Invalid regex: {e}"}

    hits: List[Dict[str, Any]] = []

    if _is_mistral(paper):
        pages = _pages_from_paper(paper)
        for page_dict in pages:
            idx_raw = page_dict.get("index")
            page_num = (int(idx_raw) + 1) if idx_raw is not None else None
            md = page_dict.get("markdown") or ""
            lines = md.splitlines()
            for line_num, line in enumerate(lines):
                if pattern.search(line):
                    before = lines[max(0, line_num - context_lines) : line_num]
                    after = lines[
                        line_num + 1 : min(len(lines), line_num + 1 + context_lines)
                    ]
                    hits.append(
                        {
                            "page": page_num,
                            "line": line_num + 1,
                            "match": line,
                            "before": before,
                            "after": after,
                        }
                    )
    else:
        raw = str(getattr(paper, "raw_content", "") or "")
        page_offset_map = getattr(paper, "page_offset_map", None) or {}
        # Build offset-to-page lookup once.
        ranges: List[Tuple[int, int, int]] = []
        for k, v in page_offset_map.items():
            try:
                p = int(k)
            except (TypeError, ValueError):
                continue
            if isinstance(v, (list, tuple)) and len(v) == 2:
                ranges.append((p, int(v[0]), int(v[1])))
        ranges.sort(key=lambda x: x[1])

        def _page_for(off: int) -> Optional[int]:
            for p, s, e in ranges:
                if s <= off < e:
                    return p
            return None

        lines = raw.splitlines()
        # Compute starting offset for each line so we can map to a page.
        line_starts: List[int] = [0]
        running = 0
        for line in lines:
            running += len(line) + 1  # +1 for the newline
            line_starts.append(running)

        for line_num, line in enumerate(lines):
            if pattern.search(line):
                before = lines[max(0, line_num - context_lines) : line_num]
                after = lines[
                    line_num + 1 : min(len(lines), line_num + 1 + context_lines)
                ]
                hits.append(
                    {
                        "page": _page_for(line_starts[line_num]),
                        "line": line_num + 1,
                        "match": line,
                        "before": before,
                        "after": after,
                    }
                )

    # Cap the result set to keep tool output bounded.
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


def get_figure(
    paper_id: str,
    label: str,
    current_user: CurrentUser,
    db: Session,
    project_id: Optional[str] = None,
) -> Dict[str, Any]:
    paper = _get_paper_or_raise(paper_id, current_user, db)
    if not _is_mistral(paper):
        return {"error": "Figures are unavailable for this paper (parsed in fallback mode)"}

    from app.api.paper_figure_api import resolve_figure

    figure = resolve_figure(getattr(paper, "ocr", None), label)
    if not figure:
        return {"error": f"No figure matching '{label}'"}

    if not figure.get("s3_key"):
        return {"error": f"Figure '{figure.get('label') or label}' is not yet rendered"}

    # The chat UI fetches the bitmap via the figure endpoint; the agent gets
    # back the metadata + endpoint path so it can answer with caption text
    # and reference the image inline.
    return {
        "label": figure.get("label"),
        "page": figure.get("page"),
        "caption": figure.get("caption"),
        "id": figure.get("id"),
        "url": f"/api/paper/{paper_id}/figure/{figure.get('label') or figure.get('id')}",
    }
