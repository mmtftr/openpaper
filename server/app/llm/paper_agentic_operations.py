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
import json
import logging
import re
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Any, AsyncGenerator, Dict, List, Optional, Sequence, Tuple, Union

from app.database.crud.message_crud import message_crud
from app.database.crud.paper_crud import paper_crud
from app.database.database import get_db
from app.database.models import Paper
from app.database.telemetry import track_event
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
from app.llm.provider import LLMProvider, TextContent
from app.llm.tools.doc_tools import (
    read_main_doc,
    read_main_doc_function,
    write_main_doc,
    write_main_doc_function,
)
from app.llm.tools.section_tools import (
    build_outline,
    get_figure,
    get_figure_function,
    read_pages,
    read_pages_function,
    read_section,
    read_section_function,
    render_outline_text,
    search_paper,
    search_paper_function,
)
from app.llm.utils import retry_llm_operation
from app.schemas.message import ResponseStyle
from app.schemas.responses import ToolCallResult
from app.schemas.user import CurrentUser
from fastapi import Depends
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


HEARTBEAT_INTERVAL_SECONDS = 15
MAX_AGENTIC_ITERATIONS = 6  # Tool-call loop cap. Way more than typical needs.

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
) -> str:
    outline = build_outline(paper)
    outline_text = render_outline_text(outline)
    additional = _additional_instructions(response_style)
    base = PAPER_AGENT_BASE.format(
        outline=outline_text,
        additional_instructions=additional,
    )

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


def _tool_specs(paper: Paper, mode: ContextMode) -> List[Dict[str, Any]]:
    """Tool surface for this mode.

    Paper-reading tools are gated by mode: Full mode pre-loads the whole
    paper (no paper tools needed); Raw mode hides figure tools (pymupdf
    fallback can't render). The user-doc tools (read/write_main_doc) are
    available in every mode — they target the user's writeup, not the
    paper, so context-mode reasoning doesn't apply.
    """
    parser = str(getattr(paper, "parser", "") or "")
    tools: List[Dict[str, Any]] = []
    if mode != "full":
        tools.extend(
            [read_section_function, read_pages_function, search_paper_function]
        )
        if parser == "mistral" and mode != "raw":
            tools.append(get_figure_function)
    tools.extend([read_main_doc_function, write_main_doc_function])
    return tools


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

        conversation_history = message_crud.get_conversation_messages(
            db,
            conversation_id=uuid.UUID(conversation_id),
            current_user=current_user,
        )

        system_prompt = _build_system_prompt(paper, context_mode, response_style)

        user_citations = (
            CitationHandler.convert_references_to_citations(user_references)
            if user_references
            else None
        )
        user_message_text = (
            f"{question}\n\n{user_citations}" if user_citations else question
        )
        user_content = [TextContent(text=user_message_text)]

        tool_specs = _tool_specs(paper, context_mode)
        tool_map = {
            "read_section": read_section,
            "read_pages": read_pages,
            "search_paper": search_paper,
            "get_figure": get_figure,
            "read_main_doc": read_main_doc,
            "write_main_doc": write_main_doc,
        }

        # No tools at all → stream a normal answer. (Doc tools are always in
        # the surface, so this only fires if a future mode strips everything.)
        if not tool_specs:
            async for chunk in self._stream_final_answer(
                paper=paper,
                system_prompt=system_prompt,
                user_content=user_content,
                history=conversation_history,
                provider=llm_provider,
                model_type=model_type,
                model=model,
                reasoning_effort=reasoning_effort,
            ):
                yield chunk
            return

        tool_call_results: List[ToolCallResult] = []
        seen_calls: set[str] = set()
        iteration = 0

        while iteration < MAX_AGENTIC_ITERATIONS:
            iteration += 1

            llm_response = self.generate_content(
                system_prompt=system_prompt,
                contents=user_content,
                history=conversation_history,
                function_declarations=tool_specs,
                tool_call_results=tool_call_results or None,
                provider=llm_provider,
                model_type=model_type,
                enable_thinking=True,
            )

            # Surface the model's thinking summary as a status (until either a
            # tool-call status or the answer stream supersedes it). This is
            # the model's own narration of what it's about to do — much more
            # useful than an opaque step counter.
            thinking = (getattr(llm_response, "thinking", None) or "").strip()
            if thinking:
                yield {"type": "status", "content": _summarize_thinking(thinking)}

            if not llm_response.tool_calls:
                # The model is ready to answer. Re-run as a streaming call so
                # the user sees content as it's produced.
                async for chunk in self._stream_final_answer(
                    paper=paper,
                    system_prompt=system_prompt,
                    user_content=user_content,
                    history=conversation_history,
                    tool_call_results=tool_call_results or None,
                    provider=llm_provider,
                    model_type=model_type,
                    model=model,
                    reasoning_effort=reasoning_effort,
                ):
                    yield chunk
                return

            for call in llm_response.tool_calls:
                fn_name = (call.name or "").lower()
                fn_args = call.args or {}
                key = f"{fn_name}:{json.dumps(fn_args, sort_keys=True, default=str)}"
                if key in seen_calls:
                    tool_call_results.append(
                        ToolCallResult(
                            id=call.id,
                            name=fn_name,
                            args=fn_args,
                            result={"error": "duplicate call skipped"},
                        )
                    )
                    continue
                seen_calls.add(key)

                if fn_name not in tool_map:
                    tool_call_results.append(
                        ToolCallResult(
                            id=call.id,
                            name=fn_name,
                            args=fn_args,
                            result={"error": f"unknown tool {fn_name}"},
                        )
                    )
                    continue

                yield {
                    "type": "status",
                    "content": _pretty_tool_status(fn_name, fn_args),
                }

                t0 = time.time()
                try:
                    result = await self._run_tool_with_heartbeats(
                        fn_name=fn_name,
                        fn=tool_map[fn_name],
                        args=fn_args,
                        paper_id=paper_id,
                        current_user=current_user,
                        db=db,
                    )
                    tool_call_results.append(
                        ToolCallResult(
                            id=call.id,
                            name=fn_name,
                            args=fn_args,
                            result=result,
                        )
                    )
                except Exception as e:
                    logger.warning(f"Tool {fn_name} raised: {e}", exc_info=True)
                    tool_call_results.append(
                        ToolCallResult(
                            id=call.id,
                            name=fn_name,
                            args=fn_args,
                            result={"error": str(e)},
                        )
                    )

                track_event(
                    "paper_agentic_tool_call",
                    {
                        "tool": fn_name,
                        "duration_ms": (time.time() - t0) * 1000,
                        "context_mode": context_mode,
                    },
                    user_id=str(current_user.id),
                    db=db,
                )

        # Hit the iteration cap without the model deciding it was done.
        yield {
            "type": "status",
            "content": "Reached max agent steps; finalizing answer.",
        }
        async for chunk in self._stream_final_answer(
            paper=paper,
            system_prompt=system_prompt,
            user_content=user_content,
            history=conversation_history,
            tool_call_results=tool_call_results or None,
            provider=llm_provider,
            model_type=model_type,
            model=model,
            reasoning_effort=reasoning_effort,
        ):
            yield chunk

    async def _run_tool_with_heartbeats(
        self,
        fn_name: str,
        fn,
        args: Dict[str, Any],
        paper_id: str,
        current_user: CurrentUser,
        db: Session,
    ) -> Any:
        """Run a synchronous tool in a thread, surfacing heartbeats so the
        streaming connection doesn't time out for slow ones."""

        def _call():
            return fn(
                paper_id=paper_id,
                current_user=current_user,
                db=db,
                **args,
            )

        # Propagate the OTel/Logfire span context across the thread hop so
        # any spans the tool emits (DB queries, S3 fetches) parent to the
        # chat handler instead of becoming orphans.
        ctx = contextvars.copy_context()
        loop = asyncio.get_event_loop()
        future = loop.run_in_executor(_tool_executor, lambda: ctx.run(_call))
        while True:
            try:
                return await asyncio.wait_for(
                    asyncio.shield(future), timeout=HEARTBEAT_INTERVAL_SECONDS
                )
            except asyncio.TimeoutError:
                # The loop will be polled again; the user-facing status comes
                # from the caller (one status per tool call is enough).
                continue

    async def _stream_final_answer(
        self,
        paper: Paper,
        system_prompt: str,
        user_content: List[TextContent],
        history,
        tool_call_results: Optional[List[ToolCallResult]] = None,
        provider: Optional[LLMProvider] = None,
        model_type: ModelType = ModelType.DEFAULT,
        model: Optional[str] = None,
        reasoning_effort: Optional[str] = None,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """Stream the final answer with the same evidence-block parsing the
        non-agentic chat uses, so the existing client UI keeps working."""

        evidence_buffer: List[str] = []
        text_buffer: str = ""
        in_evidence_section = False
        START_DELIMITER = "---EVIDENCE---"
        END_DELIMITER = "---END-EVIDENCE---"

        kwargs: Dict[str, Any] = {}
        if reasoning_effort:
            kwargs["reasoning_effort"] = reasoning_effort
        # Plumbed straight through to the provider — each provider's
        # send_message_stream pops it and reconstructs proper
        # assistant{tool_calls} / tool{result} messages, instead of the old
        # user-content text injection.
        if tool_call_results:
            kwargs["tool_call_results"] = tool_call_results

        for chunk in self.send_message_stream(
            message=user_content,
            file=None,
            system_prompt=system_prompt,
            history=history,
            provider=provider,
            model_type=model_type,
            model=model,
            **kwargs,
        ):
            text = chunk.text
            if not text:
                continue
            text_buffer += text

            if not in_evidence_section and START_DELIMITER in text_buffer:
                in_evidence_section = True
                pre_evidence = text_buffer.split(START_DELIMITER)[0]
                if pre_evidence:
                    yield {"type": "content", "content": pre_evidence}
                evidence_buffer = [text_buffer.split(START_DELIMITER)[1]]
                text_buffer = ""
                continue

            reconstructed = "".join(evidence_buffer + [text_buffer]).strip()
            if in_evidence_section and END_DELIMITER in reconstructed:
                delimiter_pos = reconstructed.find(END_DELIMITER)
                evidence_part = reconstructed[:delimiter_pos]
                remaining = reconstructed[delimiter_pos + len(END_DELIMITER) :]
                structured = CitationHandler.parse_evidence_block(evidence_part)
                # Stream the original (OCR-grounded) citations immediately so
                # the chat UI can show evidence as soon as it lands.
                yield {
                    "type": "references",
                    "content": {"citations": structured},
                }
                # Then reconcile in the background. Each citation gets a
                # match against its page's pymupdf text — the highlighter
                # search target is what makes a citation actually click
                # through to the right spot on the PDF.
                reconciled = await _reconcile_citations(
                    structured, paper, llm_client=self
                )
                if reconciled:
                    yield {
                        "type": "references_reconciled",
                        "content": {"citations": reconciled},
                    }
                in_evidence_section = False
                evidence_buffer = []
                text_buffer = remaining
                if remaining:
                    yield {"type": "content", "content": remaining}
                continue

            if in_evidence_section:
                evidence_buffer.append(text)
                text_buffer = ""
            else:
                if len(text_buffer) > len(START_DELIMITER) * 2:
                    to_yield = text_buffer[: -len(START_DELIMITER)]
                    yield {"type": "content", "content": to_yield}
                    text_buffer = text_buffer[-len(START_DELIMITER) :]

        if text_buffer:
            yield {"type": "content", "content": text_buffer}


def _pretty_tool_status(fn_name: str, args: Dict[str, Any]) -> str:
    if fn_name == "read_section":
        return f"Reading section '{args.get('name', '?')}'"
    if fn_name == "read_pages":
        return f"Reading pages {args.get('start')}–{args.get('end')}"
    if fn_name == "search_paper":
        return f"Searching for '{args.get('query', '?')}'"
    if fn_name == "get_figure":
        return f"Fetching {args.get('label', 'figure')}"
    if fn_name == "read_main_doc":
        return "Reading your notes"
    if fn_name == "write_main_doc":
        return "Updating your notes"
    return f"Calling {fn_name}"


def _summarize_thinking(thinking: str, max_chars: int = 160) -> str:
    """Compact a thinking blob into a single status line.

    The chat UI shows one status string at a time, so we collapse newlines
    and use only the first sentence-ish chunk. The user sees a hint of what
    the model is reasoning about, not the whole transcript.
    """
    flat = " ".join(thinking.split())
    if len(flat) <= max_chars:
        return flat
    return flat[: max_chars - 1].rstrip() + "…"


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
) -> Optional[List[Dict[str, Any]]]:
    """For each citation, replace its `reference` text with the substring
    that actually matches the PDF page (so the highlighter can find it).

    Strategy per citation:
      1. If the citation has no page (legacy format), skip — leave verbatim.
      2. Try the markdown→pymupdf normalizer. ~88% of prose hits this path
         and costs nothing.
      3. On miss, fall back to a fast-model call: the model gets the page's
         plain-text and the OCR quote, returns the matching substring or
         NO_MATCH.

    Falls back to the original quote when both fail. Returns None when there
    were no citations to reconcile (so the caller can skip the event).
    """
    if not citations:
        return None
    if str(getattr(paper, "parser", "") or "") != "mistral":
        return None

    # Pair (citation, normalizer_result). Normalizer is sync + cheap so we run
    # it inline.
    todo_for_llm: List[Tuple[int, Dict[str, Any], str]] = []
    out: List[Dict[str, Any]] = []
    for cit in citations:
        page = cit.get("page")
        ref = str(cit.get("reference") or "")
        if page is None or not ref:
            out.append(dict(cit))
            continue

        page_text = _pymupdf_text_for_page(paper, int(page))
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
            out.append(patched)
            continue

        # Defer to LLM fallback. Keep position so we can re-insert in order.
        out.append(dict(cit))
        todo_for_llm.append((len(out) - 1, cit, page_text))

    if todo_for_llm:
        results = await asyncio.gather(
            *[
                _reconcile_one_via_llm(cit, page_text, llm_client)
                for _, cit, page_text in todo_for_llm
            ],
            return_exceptions=True,
        )
        for (idx, cit, _), result in zip(todo_for_llm, results):
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
