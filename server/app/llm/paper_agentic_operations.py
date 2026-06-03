"""
Single-paper agentic chat. Mirrors EvidenceOperations.gather_evidence in spirit
(tool-call loop with status events), but scoped to a single paper and driven
by the user-selected context mode.

The system prompt always carries the paper outline so the agent never has to
spend a tool call discovering structure. The pre-loaded body content varies
by mode:

- Adaptive: abstract + intro + conclusion pre-loaded; full tool surface.
- Comprehensive: main body + figures pre-loaded; references/appendix via tools.
- Full: whole paper + figures pre-loaded; no agentic loop, no tools.
- Raw: pymupdf fallback only — full markdown, no figures, no tools.
"""

import asyncio
import contextvars
import logging
import re
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Any, AsyncGenerator, Dict, List, Optional, Sequence, Tuple, Union

from app.database.crud.message_crud import message_crud
from app.database.crud.paper_crud import paper_crud
from app.database.database import get_db
from app.database.models import Paper
from app.llm.base import BaseLLMClient, ModelType
from app.llm.citation_handler import CitationHandler
from app.llm.citation_normalizer import find_in_pdf_text
from app.llm.prompts import (
    ADAPTIVE_MODE_PRELOAD,
    COMPREHENSIVE_MODE_PRELOAD,
    CONCISE_MODE_INSTRUCTIONS,
    DETAILED_MODE_INSTRUCTIONS,
    FULL_MODE_PRELOAD,
    NORMAL_MODE_INSTRUCTIONS,
    PAPER_AGENT_BASE,
    RAW_MODE_PRELOAD,
)
from app.llm.paper_pydantic_agent import run_pydantic_paper_agent
from app.llm.provider import LLMProvider, TextContent
from app.llm.tools.section_tools import (
    build_outline,
    render_outline_text,
)
from app.llm.utils import retry_llm_operation
from app.schemas.message import ResponseStyle
from app.schemas.user import CurrentUser
from fastapi import Depends
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


MAX_AGENTIC_ITERATIONS = 25  # Tool-call budget for retrieval-heavy paper turns.

_tool_executor = ThreadPoolExecutor(max_workers=4)


ContextMode = str  # "adaptive" | "comprehensive" | "full" | "raw"


def _additional_instructions(response_style: Optional[str]) -> str:
    if response_style == ResponseStyle.DETAILED:
        return DETAILED_MODE_INSTRUCTIONS
    if response_style == ResponseStyle.CONCISE:
        return CONCISE_MODE_INSTRUCTIONS
    return NORMAL_MODE_INSTRUCTIONS


def _select_preload(
    mode: ContextMode, paper: Paper, outline: Dict[str, Any]
) -> str:
    """Return the body content pre-loaded for this mode.

    The content is markdown the agent reads alongside the outline. Mode-driven
    so we don't fall back to "send the whole PDF every turn".
    """
    parser = str(getattr(paper, "parser", "") or "")

    if mode == "raw" or parser != "mistral":
        # Whole flat raw_content; no figures, no tools.
        return str(getattr(paper, "raw_content", "") or "")

    pages = (getattr(paper, "ocr", None) or {}).get("pages") or []
    page_md = [p.get("markdown") or "" for p in pages]

    if mode == "full":
        return "\n\n".join(page_md)

    if mode == "comprehensive":
        # Drop pages that look like references/appendix. Cheap heuristic:
        # find the first page whose markdown contains a top-level heading
        # matching references|bibliography|appendix; cut from there.
        cutoff_page_idx = _first_back_matter_page(pages)
        if cutoff_page_idx is None:
            return "\n\n".join(page_md)
        return "\n\n".join(page_md[:cutoff_page_idx])

    # Adaptive: abstract + intro + conclusion via outline-driven section reads.
    return _adaptive_preload(paper, outline)


_BACK_MATTER_RE = re.compile(
    r"^\s*#{1,6}\s+(references|bibliography|appendix|appendices|acknowledg(e)?ments?)\b",
    re.IGNORECASE | re.MULTILINE,
)


def _first_back_matter_page(pages: List[Dict[str, Any]]) -> Optional[int]:
    for i, page in enumerate(pages):
        md = page.get("markdown") or ""
        if _BACK_MATTER_RE.search(md):
            return i
    return None


def _adaptive_preload(paper: Paper, outline: Dict[str, Any]) -> str:
    """Find abstract / introduction / conclusion in the outline and return
    just those sections joined together.

    Falls back to the full body if none of the canonical sections are found
    — better to over-include than to ship the agent an empty preload.
    """
    headings = outline.get("headings") or []
    pages = (getattr(paper, "ocr", None) or {}).get("pages") or []
    if not pages:
        return str(getattr(paper, "abstract", "") or "")

    targets = ["abstract", "introduction", "conclusion"]
    chunks: List[str] = []

    abstract = str(getattr(paper, "abstract", "") or "").strip()
    if abstract:
        chunks.append(f"## Abstract\n\n{abstract}")

    flat_pages = "\n\n".join(p.get("markdown") or "" for p in pages)
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


def _build_system_prompt(
    paper: Paper,
    mode: ContextMode,
    response_style: Optional[str],
    supplementary_papers: Optional[Sequence[Paper]] = None,
) -> str:
    outline = build_outline(paper)
    outline_text = render_outline_text(outline)
    additional = _additional_instructions(response_style)
    base = PAPER_AGENT_BASE.format(
        outline=outline_text,
        additional_instructions=additional,
    )

    supplementary_block = _render_supplementary_block(supplementary_papers)
    if supplementary_block:
        base = base + "\n\n" + supplementary_block

    parser = str(getattr(paper, "parser", "") or "")
    preload = _select_preload(mode, paper, outline)

    if mode == "raw" or parser != "mistral":
        return base + "\n\n" + RAW_MODE_PRELOAD.format(preloaded_content=preload)
    if mode == "full":
        return base + "\n\n" + FULL_MODE_PRELOAD.format(preloaded_content=preload)
    if mode == "comprehensive":
        return base + "\n\n" + COMPREHENSIVE_MODE_PRELOAD.format(
            preloaded_content=preload
        )
    return base + "\n\n" + ADAPTIVE_MODE_PRELOAD.format(preloaded_content=preload)


def _render_supplementary_block(
    supplementary_papers: Optional[Sequence[Paper]],
) -> str:
    """Emit the supplementary-papers listing for the system prompt.

    The agent is told it can reach into each supplementary by passing the
    listed `paper_id` to the same tools (read_section, read_pages,
    search_paper, get_figure). Returns "" when there are no supplementaries
    so the prompt stays untouched for ordinary single-paper chats.
    """
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


class PaperAgenticOperations(BaseLLMClient):
    """Single-paper agentic chat with context modes."""

    @retry_llm_operation(max_retries=2, delay=1.0)
    async def chat_with_paper_agentic(
        self,
        paper_id: str,
        conversation_id: str,
        question: str,
        current_user: CurrentUser,
        context_mode: ContextMode = "adaptive",
        llm_provider: Optional[LLMProvider] = None,
        user_references: Optional[Sequence[str]] = None,
        response_style: Optional[str] = "normal",
        model_type: ModelType = ModelType.DEFAULT,
        model: Optional[str] = None,
        reasoning_effort: Optional[str] = None,
        db: Session = Depends(get_db),
    ) -> AsyncGenerator[Union[str, dict], None]:
        paper: Paper = paper_crud.get(db, id=paper_id, user=current_user)
        if not paper:
            raise ValueError(f"Paper with ID {paper_id} not found.")

        # Context mode validation: paper.parser determines which modes are
        # allowed. Pymupdf-parsed papers force Raw; Mistral-parsed papers
        # reject Raw.
        parser = str(getattr(paper, "parser", "") or "")
        if parser != "mistral" and context_mode != "raw":
            context_mode = "raw"
        if parser == "mistral" and context_mode == "raw":
            context_mode = "adaptive"

        # Pull any supplementary papers attached to this parent. The chat
        # always runs against the parent paper; supplementaries widen the
        # agent's tool surface (they're addressable by paper_id on every
        # paper-reading tool) but never get their own conversation.
        supplementary_papers: List[Paper] = []
        try:
            supplementary_papers = list(
                paper_crud.list_supplementary_for(
                    db,
                    parent_paper_id=uuid.UUID(paper_id),
                    user=current_user,
                )
                or []
            )
        except AttributeError:
            # paper_crud.list_supplementary_for is being added by a separate
            # change; treat its absence as "no supplementaries for now".
            supplementary_papers = []
        except Exception as exc:
            logger.warning(
                "Failed to load supplementary papers for %s: %s", paper_id, exc
            )
            supplementary_papers = []

        allowed_paper_ids: List[str] = [
            paper_id,
            *[str(p.id) for p in supplementary_papers],
        ]

        conversation_history = message_crud.get_conversation_messages(
            db,
            conversation_id=uuid.UUID(conversation_id),
            current_user=current_user,
        )

        system_prompt = _build_system_prompt(
            paper,
            context_mode,
            response_style,
            supplementary_papers=supplementary_papers,
        )

        user_citations = (
            CitationHandler.convert_references_to_citations(user_references)
            if user_references
            else None
        )
        user_message_text = (
            f"{question}\n\n{user_citations}" if user_citations else question
        )

        # The reconciler needs the supplementary papers in scope so it can
        # match a citation's quote against the correct paper's pymupdf text
        # and tag the citation with the originating supplementary id. Bind
        # them onto the function via a small closure to avoid changing
        # `run_pydantic_paper_agent`'s reconciler contract.
        family_index: Dict[str, Paper] = {paper_id: paper}
        for sup in supplementary_papers:
            family_index[str(sup.id)] = sup

        async def _reconcile_family_citations(
            citations: List[Dict[str, Any]],
            parent_paper: Paper,
            llm_client: BaseLLMClient,
        ) -> Optional[List[Dict[str, Any]]]:
            return await _reconcile_citations(
                citations,
                parent_paper,
                llm_client,
                family_index=family_index,
                parent_paper_id=paper_id,
            )

        async for chunk in run_pydantic_paper_agent(
            llm_client=self,
            paper_id=paper_id,
            paper=paper,
            current_user=current_user,
            db=db,
            context_mode=context_mode,
            system_prompt=system_prompt,
            user_message_text=user_message_text,
            conversation_history=conversation_history,
            citation_reconciler=_reconcile_family_citations,
            llm_provider=llm_provider,
            model_type=model_type,
            model=model,
            reasoning_effort=reasoning_effort,
            max_agentic_iterations=MAX_AGENTIC_ITERATIONS,
            allowed_paper_ids=allowed_paper_ids,
        ):
            yield chunk


# ----------------------------------------------------------------------
# Citation reconciliation: OCR-grounded quote → pymupdf-grounded substring
# ----------------------------------------------------------------------


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


async def _reconcile_citations(
    citations: List[Dict[str, Any]],
    paper: Paper,
    llm_client: BaseLLMClient,
    *,
    family_index: Optional[Dict[str, Paper]] = None,
    parent_paper_id: Optional[str] = None,
) -> Optional[List[Dict[str, Any]]]:
    """For each citation, replace its `reference` text with the substring
    that actually matches the PDF page (so the highlighter can find it).

    Strategy per citation:
      1. If the citation has no page (legacy format), skip — leave verbatim.
      2. Pick the right paper. If the citation came tagged with `paper_id`
         (supplementary-sourced), use that paper's OCR. Otherwise default to
         the parent paper, then try each supplementary as a fallback so a
         missing tag doesn't sink the match.
      3. Try the markdown→pymupdf normalizer. ~88% of prose hits this path
         and costs nothing.
      4. On miss, fall back to a fast-model call: the model gets the page's
         plain-text and the OCR quote, returns the matching substring or
         NO_MATCH.

    Convention on the `paper_id` field of a citation:
      - Absent (or equal to the parent) → citation is from the parent paper.
      - Equal to a supplementary's id → citation is from that supplementary.
    The reconciled output carries `paper_id` explicitly for every citation
    that we matched against a non-parent paper, so the client can route the
    highlight to the right PDF.
    """
    if not citations:
        return None

    family_index = family_index or {}
    # Always make the supplied parent paper available for lookup.
    if parent_paper_id and parent_paper_id not in family_index:
        family_index = {parent_paper_id: paper, **family_index}

    def _resolve_paper_for_citation(cit: Dict[str, Any]) -> Tuple[Paper, Optional[str]]:
        """Pick which paper a citation should match against. Returns
        (paper_used, paper_id_to_tag_on_output_if_supplementary)."""
        tagged = cit.get("paper_id")
        if tagged and str(tagged) in family_index:
            chosen = family_index[str(tagged)]
            return chosen, (str(tagged) if str(tagged) != parent_paper_id else None)
        return paper, None

    # Pair (citation, normalizer_result). Normalizer is sync + cheap so we run
    # it inline.
    todo_for_llm: List[Tuple[int, Dict[str, Any], str, Paper, Optional[str]]] = []
    out: List[Dict[str, Any]] = []
    for cit in citations:
        page = cit.get("page")
        ref = str(cit.get("reference") or "")
        if page is None or not ref:
            out.append(dict(cit))
            continue

        candidate_paper, supplementary_id = _resolve_paper_for_citation(cit)
        if str(getattr(candidate_paper, "parser", "") or "") != "mistral":
            out.append(dict(cit))
            continue

        page_text = _pymupdf_text_for_page(candidate_paper, int(page))
        if not page_text:
            out.append(dict(cit))
            continue

        # Strip leading/trailing quote chars the model often wraps quotes in.
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

        # Defer to LLM fallback. Keep position so we can re-insert in order.
        out.append(dict(cit))
        todo_for_llm.append(
            (len(out) - 1, cit, page_text, candidate_paper, supplementary_id)
        )

    if todo_for_llm:
        results = await asyncio.gather(
            *[
                _reconcile_one_via_llm(cit, page_text, llm_client)
                for _, cit, page_text, _, _ in todo_for_llm
            ],
            return_exceptions=True,
        )
        for (idx, cit, _, _, supplementary_id), result in zip(
            todo_for_llm, results
        ):
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
    llm_client: BaseLLMClient,
) -> Optional[str]:
    """Run a single fast-model reconciliation call. Returns the matched
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

    def _call_sync():
        return llm_client.generate_content(
            contents=[TextContent(text=prompt)],
            system_prompt=(
                "You return the exact PDF page substring corresponding to "
                "an OCR quote, or NO_MATCH. Output is plain text, one line, "
                "no quotes or prefixes."
            ),
            model_type=ModelType.FAST,
            enable_thinking=False,
        )

    # OTel/Logfire tracks the current span via contextvars. `run_in_executor`
    # hops to a worker thread without those vars, so the inner OpenAI span
    # would otherwise be parented to nothing. Copy the current context and
    # run the sync call inside it so the span stays under the chat handler.
    ctx = contextvars.copy_context()
    loop = asyncio.get_event_loop()
    try:
        response = await loop.run_in_executor(
            _tool_executor, lambda: ctx.run(_call_sync)
        )
    except Exception as e:
        logger.warning("LLM reconciliation call raised: %s", e)
        return None

    text = (response.text or "").strip()
    if not text or text.upper().startswith("NO_MATCH"):
        return None
    # Strip wrapping quotes the model sometimes adds despite the instruction.
    text = text.strip().strip('"').strip("'").strip()
    if not text:
        return None
    return text
