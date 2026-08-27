"""Pydantic AI runtime for single-paper context-mode chat."""

from __future__ import annotations

import asyncio
import contextvars
import copy
import json
import logging
import time
from collections.abc import AsyncGenerator, Awaitable, Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Union

from app.database.models import Message, Paper
from app.database.telemetry import track_event
from app.llm._pai_compat import make_openai_chat_model, make_openai_responses_model
from app.llm.base import BaseLLMClient, ModelType
from app.llm.citation_handler import CitationHandler
from app.llm.provider import (
    AnthropicProvider,
    GeminiProvider,
    LLMProvider,
    OpenAIProvider,
)
from app.database.crud.paper_crud import paper_crud
from app.helpers.s3 import s3_service
from app.llm.tools.doc_tools import list_docs, read_doc, write_doc
from app.llm.tools.section_tools import read_pages, read_section, search_paper
from app.schemas.user import CurrentUser
from pydantic_ai import Agent, AgentRunResultEvent, RunContext, UsageLimits
from pydantic_ai.messages import (
    BinaryImage,
    FunctionToolCallEvent,
    FunctionToolResultEvent,
    ModelMessage,
    ModelMessagesTypeAdapter,
    ModelRequest,
    ModelResponse,
    PartDeltaEvent,
    PartStartEvent,
    TextPart,
    TextPartDelta,
    ThinkingPart,
    ThinkingPartDelta,
    ToolReturn,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models.anthropic import AnthropicModel
from pydantic_ai.models.google import GoogleModel
from pydantic_ai.models.openai import (
    OpenAIChatModelSettings,
    OpenAIResponsesModel,
    OpenAIResponsesModelSettings,
)
from pydantic_ai.providers.anthropic import AnthropicProvider as PaiAnthropicProvider
from pydantic_ai.providers.google import GoogleProvider
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

_tool_executor = ThreadPoolExecutor(max_workers=4)


ContextMode = str
CitationReconciler = Callable[
    [List[Dict[str, Any]], Paper, BaseLLMClient],
    Awaitable[Optional[List[Dict[str, Any]]]],
]


@dataclass
class PaperAgentDeps:
    paper_id: str
    paper: Paper
    current_user: CurrentUser
    db: Session
    context_mode: ContextMode
    llm_client: BaseLLMClient
    citation_reconciler: CitationReconciler
    max_tool_calls: int = 25
    tool_calls_used: int = 0
    # Paper ids the agent is allowed to read this turn — parent + any
    # supplementary papers. Defaults to [paper_id] so legacy call sites
    # that don't pass anything keep working unchanged.
    allowed_paper_ids: List[str] = field(default_factory=list)


EVIDENCE_START = "---EVIDENCE---"
EVIDENCE_END = "---END-EVIDENCE---"
TOOL_BUDGET_EXHAUSTED = {
    "error": "ran_out_of_tool_calls",
    "message": (
        "The paper agent has run out of tool calls for this turn. "
        "Do not call another tool. Finish the answer using the evidence and "
        "context already gathered, and mention any remaining uncertainty."
    ),
}

# Marker prefix on `BinaryImage.identifier` for figures we can rehydrate
# from S3. Bytes are dropped on persistence and re-fetched by S3 key on
# replay — keeps the assistant row's bucket small while keeping the
# replayed binary content byte-identical to the original tool return.
_FIGURE_ID_PREFIX = "openpaper-figure:"

# Azure OPENAI_MODELS deployment ids known not to accept image inputs.
# Verified with a live Responses API call (1x1 PNG): both reject with
# "does not support image inputs" rather than silently ignoring the image,
# so get_figure must not attach BinaryImage content for these.
_VISION_UNSUPPORTED_MODELS = frozenset(
    {
        "FW-Kimi-K3",
        "DeepSeek-V4-Flash-0731",
    }
)


def _model_supports_vision(model: str) -> bool:
    return model not in _VISION_UNSUPPORTED_MODELS


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


def _reasoning_part_delta(content: str, previous_reasoning: str) -> str:
    """Preserve a markdown paragraph break between separate thinking parts."""
    if previous_reasoning and content and not previous_reasoning.endswith("\n"):
        return "\n\n" + content
    return content


def build_pydantic_paper_agent(
    *,
    model: Any,
    model_id: str,
    system_prompt: str,
    paper: Paper,
    context_mode: ContextMode,
) -> Agent[PaperAgentDeps, str]:
    supports_vision = _model_supports_vision(model_id)
    agent: Agent[PaperAgentDeps, str] = Agent(
        model,
        output_type=str,
        instructions=system_prompt,
        deps_type=PaperAgentDeps,
        retries=1,
        end_strategy="early",
    )

    if context_mode != "full":

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
            if exhausted := _consume_tool_budget(ctx.deps):
                return exhausted
            return await _run_sync_tool(
                read_section,
                ctx.deps,
                name=name,
                text_only=text_only,
                target_paper_id=paper_id,
                allowed_paper_ids=ctx.deps.allowed_paper_ids,
            )

        @agent.tool(
            name="read_pages",
            description=(
                "Read a contiguous 1-indexed inclusive page range. Pass "
                "paper_id to target a supplementary paper; defaults to the "
                "main paper."
            ),
        )
        async def read_pages_tool(
            ctx: RunContext[PaperAgentDeps],
            start: int,
            end: int,
            paper_id: Optional[str] = None,
        ) -> Dict[str, Any]:
            if exhausted := _consume_tool_budget(ctx.deps):
                return exhausted
            return await _run_sync_tool(
                read_pages,
                ctx.deps,
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
            if exhausted := _consume_tool_budget(ctx.deps):
                return exhausted
            return await _run_sync_tool(
                search_paper,
                ctx.deps,
                query=query,
                context_lines=context_lines,
                target_paper_id=paper_id,
                allowed_paper_ids=ctx.deps.allowed_paper_ids,
            )

        if str(getattr(paper, "parser", "") or "") == "mistral" and context_mode != "raw":

            @agent.tool(
                name="get_figure",
                description=(
                    "Fetch a figure or table by label, such as Figure 2 or "
                    "Table 4. Returns metadata (label, page, caption) "
                    + (
                        "plus the rendered image so you can read the figure "
                        "directly. "
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
                if exhausted := _consume_tool_budget(ctx.deps):
                    return exhausted
                payload = await _run_sync_tool(
                    _resolve_figure_with_image,
                    ctx.deps,
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
                            # Identifier doubles as the S3 key so we can drop
                            # the bytes from the persisted message dump and
                            # rehydrate them on the next turn — keeps the
                            # bucket row small without losing cache prefix
                            # stability (the bytes from S3 are stable).
                            identifier=f"{_FIGURE_ID_PREFIX}{payload['s3_key']}",
                        )
                    ],
                )

    @agent.tool(
        name="list_docs",
        description=(
            "List every writing doc on this paper. Returns name, kind, "
            "revision, updated_at for each. The MAIN doc is always present "
            "as name='main'."
        ),
    )
    async def list_docs_tool(
        ctx: RunContext[PaperAgentDeps],
    ) -> Dict[str, Any]:
        if exhausted := _consume_tool_budget(ctx.deps):
            return exhausted
        return await _run_sync_tool(list_docs, ctx.deps)

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
        ctx: RunContext[PaperAgentDeps],
        name: str,
    ) -> Dict[str, Any]:
        if exhausted := _consume_tool_budget(ctx.deps):
            return exhausted
        return await _run_sync_tool(read_doc, ctx.deps, name=name)

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
        if exhausted := _consume_tool_budget(ctx.deps):
            return exhausted
        return await _run_sync_tool(
            write_doc,
            ctx.deps,
            name=name,
            content=content,
            expected_revision=expected_revision,
        )

    return agent


def _consume_tool_budget(deps: PaperAgentDeps) -> Optional[Dict[str, Any]]:
    if deps.tool_calls_used >= deps.max_tool_calls:
        return dict(TOOL_BUDGET_EXHAUSTED)
    deps.tool_calls_used += 1
    return None


async def run_pydantic_paper_agent(
    *,
    llm_client: BaseLLMClient,
    paper_id: str,
    paper: Paper,
    current_user: CurrentUser,
    db: Session,
    context_mode: ContextMode,
    system_prompt: str,
    user_message_text: str,
    conversation_history: Sequence[Message],
    citation_reconciler: CitationReconciler,
    llm_provider: Optional[LLMProvider] = None,
    model_type: ModelType = ModelType.DEFAULT,
    model: Optional[str] = None,
    reasoning_effort: Optional[str] = None,
    max_agentic_iterations: int = 6,
    allowed_paper_ids: Optional[List[str]] = None,
) -> AsyncGenerator[Union[str, Dict[str, Any]], None]:
    pai_model, resolved_provider, resolved_model = _build_pai_model(
        llm_client=llm_client,
        provider=llm_provider,
        model_type=model_type,
        model=model,
    )
    agent = build_pydantic_paper_agent(
        model=pai_model,
        model_id=resolved_model,
        system_prompt=system_prompt,
        paper=paper,
        context_mode=context_mode,
    )
    deps = PaperAgentDeps(
        paper_id=paper_id,
        paper=paper,
        current_user=current_user,
        db=db,
        context_mode=context_mode,
        llm_client=llm_client,
        citation_reconciler=citation_reconciler,
        max_tool_calls=max_agentic_iterations,
        allowed_paper_ids=list(allowed_paper_ids) if allowed_paper_ids else [paper_id],
    )
    accumulated_text = ""
    accumulated_reasoning = ""
    start_times: Dict[str, float] = {}
    agent_result: Any = None

    model_settings = _model_settings_for_provider(
        resolved_provider,
        resolved_model,
        reasoning_effort,
        use_responses_api=isinstance(pai_model, OpenAIResponsesModel),
    )
    message_history = _convert_message_history(conversation_history)

    async for event in agent.run_stream_events(
        user_message_text,
        message_history=message_history or None,
        deps=deps,
        model_settings=model_settings,
        usage_limits=UsageLimits(
            # Leave one request after our manual tool budget is exhausted so
            # the model can see the tool error and produce a final answer.
            request_limit=max_agentic_iterations + 2,
            tool_calls_limit=None,
        ),
        metadata={
            "paper_id": paper_id,
            "context_mode": context_mode,
            "provider": resolved_provider.value,
            "model": resolved_model,
        },
    ):
        # PartStartEvent carries the initial chunk of a new TextPart /
        # ThinkingPart inline on `part.content` — pydantic-ai only emits a
        # PartDeltaEvent for *subsequent* updates to the same part, so
        # treating PartStart as a delta is required to avoid dropping the
        # first token of every part.
        if isinstance(event, PartStartEvent):
            if isinstance(event.part, TextPart) and event.part.content:
                accumulated_text += event.part.content
                yield {"type": "content", "content": event.part.content}
            elif isinstance(event.part, ThinkingPart) and event.part.content:
                reasoning_delta = _reasoning_part_delta(
                    event.part.content, accumulated_reasoning
                )
                accumulated_reasoning += reasoning_delta
                yield {"type": "reasoning", "content": reasoning_delta}
            continue

        if isinstance(event, PartDeltaEvent):
            if isinstance(event.delta, TextPartDelta) and event.delta.content_delta:
                accumulated_text += event.delta.content_delta
                yield {"type": "content", "content": event.delta.content_delta}
            elif (
                isinstance(event.delta, ThinkingPartDelta)
                and event.delta.content_delta
            ):
                accumulated_reasoning += event.delta.content_delta
                yield {"type": "reasoning", "content": event.delta.content_delta}
            continue

        if isinstance(event, FunctionToolCallEvent):
            part = event.part
            args = part.args if isinstance(part.args, dict) else {}
            start_times[part.tool_call_id] = time.time()
            yield {
                "type": "status",
                "content": pretty_tool_status(part.tool_name, args),
            }
            continue

        if isinstance(event, FunctionToolResultEvent):
            result = event.result
            if isinstance(result, ToolReturnPart):
                started = start_times.pop(result.tool_call_id, None)
                if started is not None:
                    track_event(
                        "paper_agentic_tool_call",
                        {
                            "tool": result.tool_name,
                            "duration_ms": (time.time() - started) * 1000,
                            "context_mode": context_mode,
                            "runtime": "pydantic_ai",
                        },
                        user_id=str(current_user.id),
                        db=db,
                    )
            continue

        if isinstance(event, AgentRunResultEvent):
            agent_result = event.result
            logger.debug(
                "Pydantic paper agent completed for paper=%s model=%s usage=%s",
                paper_id,
                resolved_model,
                getattr(event.result, "usage", None),
            )

    # Evidence is streamed inline as `---EVIDENCE---...---END-EVIDENCE---`
    # at the end of the response — the client strips it from the live view,
    # and we surface it as structured citations once the run is complete.
    _, evidence_inner = split_evidence_block(accumulated_text)
    if evidence_inner.strip():
        citations = CitationHandler.parse_evidence_block(evidence_inner)
        if citations:
            yield {"type": "references", "content": {"citations": citations}}
            reconciled = await citation_reconciler(citations, paper, llm_client)
            if reconciled:
                yield {
                    "type": "references_reconciled",
                    "content": {"citations": reconciled},
                }

    # Persist this turn's pydantic-ai messages (tool calls + tool returns +
    # final response) on the assistant row so the next turn can replay them
    # verbatim. Replaying the same prefix is what keeps prompt caching warm
    # and gives the model the prior tool transcript instead of a text-only
    # summary.
    if agent_result is not None:
        try:
            new_messages = agent_result.new_messages()
            dump = ModelMessagesTypeAdapter.dump_python(new_messages, mode="json")
            yield {
                "type": "messages_dump",
                "content": _strip_figure_bytes(dump),
            }
        except Exception as exc:
            logger.warning("Failed to serialize pydantic-ai messages: %s", exc)


def pretty_tool_status(fn_name: str, args: Dict[str, Any]) -> str:
    if fn_name == "read_section":
        return f"Reading section '{args.get('name', '?')}'"
    if fn_name == "read_pages":
        return f"Reading pages {args.get('start')}-{args.get('end')}"
    if fn_name == "search_paper":
        return f"Searching for '{args.get('query', '?')}'"
    if fn_name == "get_figure":
        return f"Fetching {args.get('label', 'figure')}"
    if fn_name == "list_docs":
        return "Listing your docs"
    if fn_name == "read_doc":
        name = args.get("name") or ""
        return f"Reading doc '{name}'" if name else "Reading a doc"
    if fn_name == "write_doc":
        name = args.get("name") or ""
        return f"Updating doc '{name}'" if name else "Updating a doc"
    return f"Calling {fn_name}"


async def _run_sync_tool(
    fn: Callable[..., Dict[str, Any]],
    deps: PaperAgentDeps,
    **args: Any,
) -> Dict[str, Any]:
    def _call() -> Dict[str, Any]:
        return fn(
            paper_id=deps.paper_id,
            current_user=deps.current_user,
            db=deps.db,
            **args,
        )

    ctx = contextvars.copy_context()
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(_tool_executor, lambda: ctx.run(_call))


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

    Returns either `{error: ...}` or `{metadata, image_bytes, media_type}`.
    Bypasses `section_tools.get_figure` because that one only emits a URL —
    we need the raw bytes to attach as `BinaryImage` so the model can
    actually look at the figure rather than just read the caption.
    """
    from app.api.paper_figure_api import resolve_figure

    effective_paper_id = target_paper_id or paper_id
    # Gate: if the model passed an explicit paper_id, it must be in the
    # allowed list (parent + supplementaries). Without a list (legacy path)
    # fall through to ownership-only check below.
    if (
        target_paper_id is not None
        and allowed_paper_ids
        and effective_paper_id not in allowed_paper_ids
    ):
        return {"error": f"paper_id {effective_paper_id} is not part of this paper family"}
    paper = paper_crud.get(db, id=effective_paper_id, user=current_user)
    if not paper:
        return {"error": "Paper not found"}
    if str(getattr(paper, "parser", "") or "") != "mistral":
        return {
            "error": "Figures are unavailable for this paper (parsed in fallback mode)"
        }
    figure = resolve_figure(getattr(paper, "ocr", None), label)
    if not figure:
        return {"error": f"No figure matching '{label}'"}
    s3_key = figure.get("s3_key")
    if not s3_key:
        return {
            "error": f"Figure '{figure.get('label') or label}' is not yet rendered"
        }
    try:
        image_bytes = s3_service.get_object_bytes(str(s3_key))
    except Exception as exc:
        logger.warning("Failed to fetch figure %s from S3: %s", s3_key, exc)
        return {"error": "Failed to fetch figure image"}
    return {
        "metadata": {
            "label": figure.get("label"),
            "page": figure.get("page"),
            "caption": figure.get("caption"),
            "id": figure.get("id"),
            "paper_id": effective_paper_id,
        },
        "image_bytes": image_bytes,
        "media_type": "image/png",
        "s3_key": str(s3_key),
    }


def _walk_binary_image_dicts(node: Any):
    """Yield every dict in `node` that looks like a serialized BinaryImage
    pointing at an `openpaper-figure:` S3 key."""
    if isinstance(node, dict):
        identifier = node.get("identifier")
        if (
            node.get("kind") == "binary"
            and isinstance(identifier, str)
            and identifier.startswith(_FIGURE_ID_PREFIX)
        ):
            yield node
        for value in node.values():
            yield from _walk_binary_image_dicts(value)
    elif isinstance(node, list):
        for item in node:
            yield from _walk_binary_image_dicts(item)


def _strip_figure_bytes(dump: Any) -> Any:
    """Replace figure image bytes with empty placeholders before save."""
    for entry in _walk_binary_image_dicts(dump):
        entry["data"] = ""
    return dump


def _rehydrate_figure_bytes(dump: Any) -> Any:
    """Re-fetch figure bytes from S3 in place after load."""
    import base64

    for entry in _walk_binary_image_dicts(dump):
        if entry.get("data"):
            continue
        s3_key = entry["identifier"][len(_FIGURE_ID_PREFIX) :]
        try:
            image_bytes = s3_service.get_object_bytes(s3_key)
        except Exception as exc:
            logger.warning(
                "Failed to rehydrate figure %s for replay: %s", s3_key, exc
            )
            continue
        # `mode='json'` dumps bytes as base64 strings; match that format so
        # `validate_json` round-trips back to the original bytes object.
        entry["data"] = base64.b64encode(image_bytes).decode("ascii")
    return dump


def _build_pai_model(
    *,
    llm_client: BaseLLMClient,
    provider: Optional[LLMProvider],
    model_type: ModelType,
    model: Optional[str],
) -> tuple[Any, LLMProvider, str]:
    if model and provider:
        # Caller knows both (e.g. the model picker returns {id, provider}
        # pairs) - resolve against that specific provider instead of the
        # ambiguous global id lookup below, which would silently prefer
        # whichever provider registered first when two expose the same id
        # (e.g. Azure's OPENAI and a same-model-family CODEX_PROXY).
        resolved_provider = provider
        resolved_model = llm_client.resolve_model_for_provider(provider, model)
    elif model:
        resolved_provider, resolved_model = llm_client.resolve_model(model)
    else:
        resolved_provider = provider or llm_client.default_provider
        resolved_model = llm_client._get_model_for_type(model_type, resolved_provider)

    provider_instance = llm_client._get_provider(resolved_provider)
    for candidate_provider, candidate_instance in llm_client._providers.items():
        if candidate_instance is provider_instance:
            resolved_provider = candidate_provider
            break
    if isinstance(provider_instance, OpenAIProvider):
        client_base_url = (
            str(provider_instance.client.base_url)
            if provider_instance.client.base_url
            else None
        )
        base_url = None if provider_instance.is_azure else client_base_url
        # Custom OpenAI-compatible endpoints (the local codex proxy, Groq,
        # Cerebras) only speak Chat Completions; the Responses API 404s there.
        # Standard OpenAI and Azure's v1 endpoint support Responses, so keep
        # those on the responses model (reasoning summaries, etc.).
        make_model = (
            make_openai_chat_model
            if provider_instance.has_custom_base_url
            else make_openai_responses_model
        )
        return (
            make_model(
                resolved_model,
                api_key=provider_instance.api_key,
                base_url=base_url,
            ),
            resolved_provider,
            resolved_model,
        )

    if isinstance(provider_instance, AnthropicProvider):
        return (
            AnthropicModel(
                resolved_model,
                provider=PaiAnthropicProvider(api_key=provider_instance.api_key),
            ),
            resolved_provider,
            resolved_model,
        )

    if isinstance(provider_instance, GeminiProvider):
        return (
            GoogleModel(
                resolved_model,
                provider=GoogleProvider(api_key=provider_instance.api_key),
            ),
            resolved_provider,
            resolved_model,
        )

    raise ValueError(
        f"Pydantic AI paper chat does not support provider {resolved_provider.value}"
    )


def _model_settings_for_provider(
    provider: LLMProvider,
    model: str,
    reasoning_effort: Optional[str],
    *,
    use_responses_api: bool = True,
) -> Optional[Dict[str, Any]]:
    if not reasoning_effort:
        return None
    if provider in (LLMProvider.OPENAI, LLMProvider.CODEX_PROXY):
        # The Azure OPENAI_MODELS list also includes non-OpenAI passthrough
        # deployments (Kimi, DeepSeek, ...) behind the same provider. Those
        # don't uniformly support the reasoning-effort knob - DeepSeek 400s
        # on it outright - so only attach it for OpenAI's own gpt-* models
        # (true of both the OPENAI/Azure and CODEX_PROXY providers).
        if not model.lower().startswith("gpt-"):
            return None
        if use_responses_api:
            return OpenAIResponsesModelSettings(
                openai_reasoning_effort=str(reasoning_effort),
                openai_reasoning_summary="auto",
            )
        # Chat Completions model (custom OpenAI-compatible endpoint): only the
        # reasoning-effort knob applies; reasoning summaries are Responses-only.
        return OpenAIChatModelSettings(
            openai_reasoning_effort=str(reasoning_effort),
        )
    return None


def _convert_message_history(messages: Sequence[Message]) -> List[ModelMessage]:
    """Reconstruct the pydantic-ai ModelMessage list for the next agent turn.

    We persist `result.new_messages()` on each assistant message's `bucket`
    field under `pai_messages`. Replaying that verbatim keeps the prompt
    prefix byte-identical across turns (prompt cache stays warm) and lets
    the model see the prior tool calls + tool returns rather than a
    text-only summary. Older messages without a dump fall back to plain
    user/assistant text so legacy conversations still work.
    """
    history: List[ModelMessage] = []
    pending_user_text: Optional[str] = None

    for message in messages:
        bucket = getattr(message, "bucket", None) or {}
        dump = bucket.get("pai_messages") if isinstance(bucket, dict) else None

        if message.role == "assistant" and dump:
            # Saved dump already contains the matching ModelRequest for
            # this turn's user prompt, so drop any pending text-only
            # fallback we were holding for it.
            pending_user_text = None
            try:
                hydrated = _rehydrate_figure_bytes(copy.deepcopy(dump))
                # Round-trip through JSON so base64-encoded `data` fields on
                # `BinaryImage` parts decode back to bytes — `validate_python`
                # would reject the base64 string in strict mode.
                history.extend(
                    ModelMessagesTypeAdapter.validate_json(json.dumps(hydrated))
                )
                continue
            except Exception as exc:
                logger.warning(
                    "Failed to deserialize pai_messages on message %s: %s",
                    getattr(message, "id", "?"),
                    exc,
                )
                # Fall through to text fallback for this turn.

        content = str(getattr(message, "content", "") or "")
        if not content:
            continue
        if message.role == "user":
            if pending_user_text is not None:
                history.append(
                    ModelRequest(parts=[UserPromptPart(content=pending_user_text)])
                )
            pending_user_text = content
        elif message.role == "assistant":
            if pending_user_text is not None:
                history.append(
                    ModelRequest(parts=[UserPromptPart(content=pending_user_text)])
                )
                pending_user_text = None
            history.append(ModelResponse(parts=[TextPart(content=content)]))

    if pending_user_text is not None:
        history.append(
            ModelRequest(parts=[UserPromptPart(content=pending_user_text)])
        )
    return history
