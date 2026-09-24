"""Evidence-block parsing and citation reconciliation.

The agent cites evidence in a trailing text block:

    ---EVIDENCE---
    @cite[1|page=3]
    "quote from the paper"
    @cite[2|page=7|paper_id=<uuid>]
    "quote from a supplementary or corpus paper"
    @cite[3|file=src/model.py|lines=42-57]
    the exact code lines cited from the paper's connected repo
    ---END-EVIDENCE---

`split_evidence_block` separates prose from the block; `parse_evidence_block`
parses its inside (named extras only: `page=`, `paper_id=`, `file=`,
`lines=`). User-attached PDF selections are rendered into the same block
format by `convert_references_to_citations`.

Reconciliation rewrites each OCR-grounded paper quote into the exact
substring of the pymupdf page text so the PDF highlighter can find it —
first via the cheap normalizer, then via a `chat.reconcile` model call on
miss. Code citations (`file=`) take a separate host-side
path: verified byte-for-byte against the repo snapshot (line-number-prefix
tolerant, range repaired by search) and stamped with a SHA-pinned
`github_url`; see app/llm/repo/code_citations.py.
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

from app.database.models import Paper
from app.llm import oneshot
from app.llm.citation_normalizer import find_in_pdf_text

logger = logging.getLogger(__name__)

EVIDENCE_START = "---EVIDENCE---"
EVIDENCE_END = "---END-EVIDENCE---"


# -- user references -> evidence block ------------------------------------


def convert_references_to_dict(references: Sequence[str]) -> dict:
    """User-attached PDF selections as numbered citations."""
    citations = []
    for idx, ref in enumerate(references):
        citations.append({"key": idx + 1, "reference": ref})
    return {"citations": citations}


def convert_references_to_citations(references: Optional[Sequence[str]]) -> str:
    """User-attached PDF selections rendered as an evidence block."""
    if not references:
        return ""
    formatted = [
        f"@cite[{citation['key']}]\n{citation['reference']}"
        for citation in convert_references_to_dict(references)["citations"]
    ]
    return EVIDENCE_START + "\n" + "\n".join(formatted) + "\n" + EVIDENCE_END


# -- evidence block parsing ------------------------------------------------


def _parse_line_range(value: str) -> tuple[Optional[int], Optional[int]]:
    """`lines=142-156` / `lines=142` / `L142-L156` → (start, end).

    Returns (None, None) when nothing usable is present — a malformed range
    must not poison an otherwise-valid citation.
    """
    text = str(value or "").strip().replace("L", "").replace("l", "")
    match = re.match(r"^(\d+)\s*(?:[-–:]\s*(\d+))?$", text)
    if not match:
        return None, None
    start = int(match.group(1))
    if start < 1:
        return None, None
    end = int(match.group(2)) if match.group(2) else start
    if end < start:
        end = start
    return start, end


def parse_evidence_block(evidence_text: str) -> list[dict]:
    """
    Parse evidence block into structured citations
    Handles multi-line citations between @cite markers

    Accepts these forms — `page=N` and `paper_id=ID` are optional and
    used by the agentic chat to pin a citation to a single PDF page (so
    the reconciliation step can match the quote against that page's
    pymupdf text) and to tag the originating paper (parent or one of its
    supplementaries):

        @cite[1]
        "First piece of evidence"

        @cite[2|page=4]
        "Second piece of evidence"

        @cite[3|page=2|paper_id=abc-123]
        "Evidence from a supplementary paper"

    Code citations (companion-repo inspection) use `file=` plus an
    optional `lines=A-B` range, parsed into `start_line` / `end_line`.
    They carry no `page`; verification is host-side against the ingested
    snapshot:

        @cite[4|file=pipeline/run.py|lines=42-57]
        the exact code lines
    """
    citations = []
    lines = evidence_text.strip().split("\n")
    current_citation: dict[str, Union[int, str]] | None = None
    current_text_lines: list[str] = []

    for line in lines:
        line = line.strip()
        if line.startswith("@cite["):
            # If we have a previous citation pending, save it (drop
            # empty-bodied citations, matching the end-of-block rule)
            if current_citation is not None and current_text_lines:
                current_citation["reference"] = " ".join(current_text_lines).strip()
                citations.append(current_citation)

            # Start new citation. Match `@cite[N]`, `@cite[N|page=P]`, or
            # any combination of `page=P` and `paper_id=ID` separated by
            # `|`. `key` is case-insensitive on the leading digit only.
            match = re.search(r"@cite\[(\d+)((?:\|[^\]]+)*)\]", line, re.IGNORECASE)
            if match:
                number = int(match.group(1))
                current_citation = {"key": number, "reference": ""}
                extras = match.group(2) or ""
                for part in extras.split("|"):
                    part = part.strip()
                    if not part:
                        continue
                    if "=" not in part:
                        continue
                    k, _, v = part.partition("=")
                    k = k.strip().lower()
                    v = v.strip()
                    if k == "page":
                        try:
                            current_citation["page"] = int(v)
                        except ValueError:
                            pass
                    elif k == "paper_id" and v:
                        current_citation["paper_id"] = v
                    elif k == "file" and v:
                        # Repo-relative path into the ingested snapshot.
                        # Tolerate the `/repo/` prefix the model sees.
                        path = v.strip().strip('"').strip("'")
                        if path.startswith("/repo/"):
                            path = path[len("/repo/") :]
                        current_citation["file"] = path.lstrip("/")
                    elif k == "lines" and v:
                        start, end = _parse_line_range(v)
                        if start is not None:
                            current_citation["start_line"] = start
                            current_citation["end_line"] = end
                current_text_lines = []
        elif current_citation is not None and line:
            # Accumulate lines for the current citation
            current_text_lines.append(line)

    # Don't forget to save the last citation
    if current_citation is not None and current_text_lines:
        current_citation["reference"] = " ".join(current_text_lines).strip()
        citations.append(current_citation)

    return citations


def split_evidence_block(text: str) -> tuple[str, str]:
    """Return (content_before_evidence, raw_evidence_inner).

    If `---END-EVIDENCE---` is missing (truncation), everything after the
    start delimiter is treated as evidence. Returns (text, "") when the
    block is absent.
    """
    start = text.find(EVIDENCE_START)
    if start == -1:
        return text, ""
    inner = text[start + len(EVIDENCE_START) :]
    end = inner.find(EVIDENCE_END)
    if end != -1:
        inner = inner[:end]
    return text[:start].rstrip(), inner


# Fallback trigger for models that mangle the opening marker (observed:
# DeepSeek emitting `**EVIDENCE**` instead of `---EVIDENCE---` while still
# closing with the correct end marker): a full line that is exactly an
# `@cite[n|...]` marker is unambiguous evidence content. Requires the whole
# line so prose ABOUT the syntax ("@cite[1] means...") never triggers.
CITE_LINE_RE = re.compile(r"^@cite\[\d+[^\]\n]*\]\s*$", re.MULTILINE)

# Decoration-only lines (dashes/asterisks, optionally the word EVIDENCE)
# left dangling right before a fallback-detected block — the mangled header.
_DECORATION_LINE_RE = re.compile(r"^[\s*_\-]*(?:EVIDENCE)?[\s*_\-:]*$")


def _next_evidence_span(text: str, pos: int) -> Optional[tuple[int, int]]:
    """Find the next evidence trigger at/after `pos`.

    Returns (cut_index, inner_index): kept prose ends at `cut_index`;
    evidence content (fed to the citation parser) starts at `inner_index`.
    """
    candidates: List[tuple[int, int]] = []
    marker = text.find(EVIDENCE_START, pos)
    if marker != -1:
        candidates.append((marker, marker + len(EVIDENCE_START)))
    cite = CITE_LINE_RE.search(text, pos)
    if cite is not None:
        candidates.append((cite.start(), cite.start()))
    if not candidates:
        return None
    return min(candidates)


def _trim_dangling_header(segment: str) -> str:
    """Drop trailing decoration-only lines (a mangled `**EVIDENCE**` header)
    from the prose kept before a fallback-detected evidence block.

    Only commits when a NON-EMPTY decoration line was actually removed —
    plain trailing newlines are kept so the batch stripper stays equivalent
    to the streaming filter for ordinary fallback triggers.
    """
    lines = segment.split("\n")
    removed_decoration = False
    trimmed = 0
    while lines and trimmed < 5 and _DECORATION_LINE_RE.match(lines[-1]):
        if lines[-1].strip():
            removed_decoration = True
        lines.pop()
        trimmed += 1
    if not removed_decoration:
        return segment
    return "\n".join(lines)


def strip_evidence_blocks(text: str) -> str:
    """Remove every evidence block from `text`, keeping surrounding prose.

    Everything between `---EVIDENCE---` (or a fallback bare `@cite[...]`
    marker line) and `---END-EVIDENCE---` is removed; an unterminated block
    extends to the end of the text.
    """
    out: List[str] = []
    pos = 0
    while True:
        span = _next_evidence_span(text, pos)
        if span is None:
            out.append(text[pos:])
            break
        cut, inner = span
        kept = text[pos:cut]
        if inner == cut:  # fallback trigger: also drop the mangled header
            kept = _trim_dangling_header(kept)
        out.append(kept)
        end = text.find(EVIDENCE_END, inner)
        if end == -1:
            break
        pos = end + len(EVIDENCE_END)
    return "".join(out).strip()


def extract_citations(text: str) -> List[Dict[str, Any]]:
    """Parse citations out of every evidence block in `text`.

    Multiple blocks are merged; a repeated key keeps its first occurrence.
    Blocks are recognized by the proper opening marker OR by the fallback
    bare `@cite[...]` line, so a model that mangles the opener doesn't lose
    its citations.
    """
    citations: List[Dict[str, Any]] = []
    seen_keys: set = set()
    pos = 0
    while True:
        span = _next_evidence_span(text, pos)
        if span is None:
            break
        _, inner_start = span
        end = text.find(EVIDENCE_END, inner_start)
        inner = text[inner_start:end] if end != -1 else text[inner_start:]
        pos = end + len(EVIDENCE_END) if end != -1 else len(text)
        for cit in parse_evidence_block(inner):
            key = cit.get("key")
            if key in seen_keys:
                continue
            seen_keys.add(key)
            citations.append(cit)
        if end == -1:
            break
    return citations


_PYMUPDF_RECONCILE_PROMPT = """\
The user is reading a PDF that's also been parsed via Mistral OCR. An assistant just produced an evidence quote grounded in the OCR markdown — but the PDF highlighter searches against pymupdf-extracted plain text, which can differ (LaTeX commands vs unicode glyphs, table cell ordering, numbered headings, etc.).

Your job: find the EXACT substring of the PDF page text that corresponds to the OCR quote. Output ONLY that substring on a single line, with no surrounding quotes, prefixes, or commentary. If no equivalent text exists on this page, output exactly: NO_MATCH

OCR quote (may contain markdown / LaTeX):
{quote}

Pymupdf page text (page {page}):
\"\"\"
{page_text}
\"\"\"
"""


_RECONCILE_CONCURRENCY = 4

_RECONCILE_INSTRUCTIONS = (
    "You return the exact PDF page substring corresponding to "
    "an OCR quote, or NO_MATCH. Output is plain text, one line, "
    "no quotes or prefixes."
)


def _pymupdf_text_for_page(paper: Paper, page: int) -> Optional[str]:
    """Pull the cached pymupdf text for the given 1-indexed page out of the
    paper's `ocr` jsonb. Returns None if the page wasn't OCR'd on the new
    pipeline (e.g. pre-backfill papers)."""
    ocr = getattr(paper, "ocr", None) or {}
    pages = ocr.get("pages") or []
    for p in pages:
        idx = p.get("index")
        if idx is None:
            continue
        if int(idx) + 1 == page:
            txt = p.get("pymupdf_text")
            return txt if isinstance(txt, str) else None
    return None


async def reconcile_citations(
    citations: List[Dict[str, Any]],
    parent_paper: Optional[Paper],
    *,
    family_index: Optional[Dict[str, Paper]] = None,
    parent_paper_id: Optional[str] = None,
    repo_snapshot: Optional[Any] = None,
) -> Optional[List[Dict[str, Any]]]:
    """For each citation, replace its `reference` text with the substring
    that actually matches the PDF page (so the highlighter can find it).

    Strategy per citation:
      1. No page (legacy format) → leave verbatim.
      2. Pick the right paper: a `paper_id` tag selects from `family_index`
         (supplementaries in paper chat, any corpus paper in corpus chat);
         otherwise the parent paper.
      3. Try the markdown→pymupdf normalizer (free, hits ~88% of prose).
      4. On miss, one `chat.reconcile` model call maps quote → page substring.

    The output carries `paper_id` explicitly for every citation matched
    against a non-parent paper so the client can route the highlight.
    """
    if not citations:
        return None

    family_index = dict(family_index or {})
    if (
        parent_paper is not None
        and parent_paper_id
        and parent_paper_id not in family_index
    ):
        family_index[parent_paper_id] = parent_paper

    def _resolve_paper(cit: Dict[str, Any]) -> Tuple[Optional[Paper], Optional[str]]:
        tagged = cit.get("paper_id")
        if tagged:
            if str(tagged) in family_index:
                chosen = family_index[str(tagged)]
                return chosen, (str(tagged) if str(tagged) != parent_paper_id else None)
            # Hallucinated / unknown paper_id: leave the citation verbatim
            # rather than rewriting its quote against the WRONG paper's text.
            return None, None
        return parent_paper, None

    todo_for_llm: List[Tuple[int, Dict[str, Any], str, Optional[str]]] = []
    code_citations: List[Tuple[int, Dict[str, Any]]] = []
    out: List[Dict[str, Any]] = []
    for cit in citations:
        # Code citations FIRST: they carry no `page`, so the short-circuit
        # below would otherwise pass them through unverified and unlinked.
        # Verified host-side in one batch after the loop (we hold the exact
        # bytes — no LLM, and one read per file rather than per citation).
        if cit.get("file"):
            out.append(dict(cit))
            code_citations.append((len(out) - 1, cit))
            continue

        page = cit.get("page")
        ref = str(cit.get("reference") or "")
        if page is None or not ref:
            out.append(dict(cit))
            continue

        candidate_paper, supplementary_id = _resolve_paper(cit)
        if (
            candidate_paper is None
            or str(getattr(candidate_paper, "parser", "") or "") != "mistral"
        ):
            out.append(dict(cit))
            continue

        page_text = _pymupdf_text_for_page(candidate_paper, int(page))
        if not page_text:
            out.append(dict(cit))
            continue

        stripped = ref.strip().strip('"').strip("'")

        normalized_hit = find_in_pdf_text(stripped, page_text)
        if normalized_hit:
            patched = dict(cit)
            patched["reference"] = normalized_hit
            patched["matched_via"] = "normalizer"
            if supplementary_id:
                patched["paper_id"] = supplementary_id
            out.append(patched)
            continue

        out.append(dict(cit))
        todo_for_llm.append((len(out) - 1, cit, page_text, supplementary_id))

    if code_citations:
        from app.llm.repo.code_citations import verify_code_citations

        try:
            # Off the event loop: reads and normalizes up to a few MB.
            verified = await asyncio.to_thread(
                verify_code_citations,
                [cit for _, cit in code_citations],
                repo_snapshot,
            )
            for (index, _), patched in zip(code_citations, verified):
                out[index] = patched
        except Exception as exc:
            logger.warning("Code citation verification failed (non-fatal): %s", exc)

    if todo_for_llm:
        # At most 4 model calls in flight (the old thread pool's size).
        gate = asyncio.Semaphore(_RECONCILE_CONCURRENCY)

        async def _gated(cit: Dict[str, Any], page_text: str) -> Optional[str]:
            async with gate:
                return await _reconcile_one_via_llm(cit, page_text)

        results = await asyncio.gather(
            *[_gated(cit, page_text) for _, cit, page_text, _ in todo_for_llm],
            return_exceptions=True,
        )
        for (idx, cit, _, supplementary_id), result in zip(todo_for_llm, results):
            if isinstance(result, Exception):
                logger.warning(
                    "LLM reconciliation failed for citation %s: %s",
                    cit.get("key"),
                    result,
                )
                continue
            if not result:
                continue
            patched = dict(cit)
            patched["reference"] = result
            patched["matched_via"] = "llm"
            if supplementary_id:
                patched["paper_id"] = supplementary_id
            out[idx] = patched

    return out


async def _reconcile_one_via_llm(
    citation: Dict[str, Any],
    page_text: str,
) -> Optional[str]:
    """Run a single `chat.reconcile` model call. Returns the matched
    substring or None on no-match / error."""
    quote = str(citation.get("reference") or "").strip().strip('"').strip("'")
    page = citation.get("page")
    if not quote or page is None:
        return None

    # Cap the page text to keep the call fast. Pages are typically 1-3k
    # tokens; 12k chars covers very dense pages.
    truncated_page = page_text[:12000]

    prompt = _PYMUPDF_RECONCILE_PROMPT.format(
        quote=quote, page=page, page_text=truncated_page
    )

    try:
        text = await oneshot.complete(
            "chat.reconcile",
            prompt,
            instructions=_RECONCILE_INSTRUCTIONS,
        )
    except Exception as e:
        logger.warning("LLM reconciliation call raised: %s", e)
        return None

    text = (text or "").strip()
    if not text or text.upper().startswith("NO_MATCH"):
        return None
    text = text.strip().strip('"').strip("'").strip()
    if not text:
        return None
    return text
