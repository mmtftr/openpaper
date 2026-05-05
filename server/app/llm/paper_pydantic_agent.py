"""Pydantic AI runtime for single-paper context-mode chat."""

from __future__ import annotations

import asyncio
import contextvars
import logging
import time
from collections.abc import AsyncGenerator, Awaitable, Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Union

from app.database.models import Message, Paper
from app.database.telemetry import track_event
from app.llm._pai_compat import make_openai_responses_model
from app.llm.base import BaseLLMClient, ModelType
from app.llm.citation_handler import CitationHandler
from app.llm.provider import (
    AnthropicProvider,
    GeminiProvider,
    LLMProvider,
    OpenAIProvider,
)
from app.llm.tools.doc_tools import read_main_doc, write_main_doc
from app.llm.tools.section_tools import get_figure, read_pages, read_section, search_paper
from app.schemas.user import CurrentUser
from pydantic_ai import Agent, AgentRunResultEvent, RunContext, UsageLimits
from pydantic_ai.messages import (
    FunctionToolCallEvent,
    FunctionToolResultEvent,
    ModelMessage,
    ModelRequest,
    ModelResponse,
    PartDeltaEvent,
    TextPart,
    TextPartDelta,
    ThinkingPartDelta,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models.anthropic import AnthropicModel
from pydantic_ai.models.google import GoogleModel
from pydantic_ai.models.openai import OpenAIResponsesModelSettings
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


@dataclass(frozen=True)
class PaperAgentDeps:
    paper_id: str
    paper: Paper
    current_user: CurrentUser
    db: Session
    context_mode: ContextMode
    llm_client: BaseLLMClient
    citation_reconciler: CitationReconciler


class EvidenceStreamParser:
    """Convert streamed model text into the existing chat chunk contract."""

    START_DELIMITER = "---EVIDENCE---"
    END_DELIMITER = "---END-EVIDENCE---"

    def __init__(
        self,
        *,
        paper: Paper,
        llm_client: BaseLLMClient,
        citation_reconciler: CitationReconciler,
    ) -> None:
        self.paper = paper
        self.llm_client = llm_client
        self.citation_reconciler = citation_reconciler
        self.evidence_buffer: List[str] = []
        self.text_buffer = ""
        self.in_evidence_section = False

    async def feed(self, text: str) -> List[Dict[str, Any]]:
        if not text:
            return []
        out: List[Dict[str, Any]] = []
        self.text_buffer += text
        entered_evidence = False

        if (
            not self.in_evidence_section
            and self.START_DELIMITER in self.text_buffer
        ):
            entered_evidence = True
            self.in_evidence_section = True
            pre_evidence, post_start = self.text_buffer.split(
                self.START_DELIMITER, 1
            )
            if pre_evidence:
                out.append({"type": "content", "content": pre_evidence})
            self.evidence_buffer = [post_start]
            self.text_buffer = ""

        reconstructed = "".join(
            self.evidence_buffer + [self.text_buffer]
        ).strip()
        if self.in_evidence_section and self.END_DELIMITER in reconstructed:
            delimiter_pos = reconstructed.find(self.END_DELIMITER)
            evidence_part = reconstructed[:delimiter_pos]
            remaining = reconstructed[delimiter_pos + len(self.END_DELIMITER) :]
            structured = CitationHandler.parse_evidence_block(evidence_part)
            out.append(
                {
                    "type": "references",
                    "content": {"citations": structured},
                }
            )
            reconciled = await self.citation_reconciler(
                structured, self.paper, self.llm_client
            )
            if reconciled:
                out.append(
                    {
                        "type": "references_reconciled",
                        "content": {"citations": reconciled},
                    }
                )
            self.in_evidence_section = False
            self.evidence_buffer = []
            self.text_buffer = remaining
            if remaining:
                out.append({"type": "content", "content": remaining})
            return out

        if self.in_evidence_section:
            if not entered_evidence:
                self.evidence_buffer.append(text)
            self.text_buffer = ""
        elif len(self.text_buffer) > len(self.START_DELIMITER) * 2:
            to_yield = self.text_buffer[: -len(self.START_DELIMITER)]
            out.append({"type": "content", "content": to_yield})
            self.text_buffer = self.text_buffer[-len(self.START_DELIMITER) :]

        return out

    def flush(self) -> Optional[Dict[str, Any]]:
        if self.text_buffer:
            return {"type": "content", "content": self.text_buffer}
        return None


def build_pydantic_paper_agent(
    *,
    model: Any,
    system_prompt: str,
    paper: Paper,
    context_mode: ContextMode,
) -> Agent[PaperAgentDeps, str]:
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
                "Use text_only=true when figures are not needed."
            ),
        )
        async def read_section_tool(
            ctx: RunContext[PaperAgentDeps],
            name: str,
            text_only: bool = False,
        ) -> Dict[str, Any]:
            return await _run_sync_tool(
                read_section,
                ctx.deps,
                name=name,
                text_only=text_only,
            )

        @agent.tool(
            name="read_pages",
            description="Read a contiguous 1-indexed inclusive page range.",
        )
        async def read_pages_tool(
            ctx: RunContext[PaperAgentDeps],
            start: int,
            end: int,
        ) -> Dict[str, Any]:
            return await _run_sync_tool(read_pages, ctx.deps, start=start, end=end)

        @agent.tool(
            name="search_paper",
            description=(
                "Regex search the paper. Returns page, line, match, and "
                "surrounding context lines."
            ),
        )
        async def search_paper_tool(
            ctx: RunContext[PaperAgentDeps],
            query: str,
            context_lines: int = 3,
        ) -> Dict[str, Any]:
            return await _run_sync_tool(
                search_paper,
                ctx.deps,
                query=query,
                context_lines=context_lines,
            )

        if str(getattr(paper, "parser", "") or "") == "mistral" and context_mode != "raw":

            @agent.tool(
                name="get_figure",
                description=(
                    "Fetch figure or table metadata by label, such as Figure 2 "
                    "or Table 4."
                ),
            )
            async def get_figure_tool(
                ctx: RunContext[PaperAgentDeps],
                label: str,
            ) -> Dict[str, Any]:
                return await _run_sync_tool(get_figure, ctx.deps, label=label)

    @agent.tool(
        name="read_main_doc",
        description=(
            "Read the user's main writing doc for this paper. Returns content "
            "and revision. Call before write_main_doc."
        ),
    )
    async def read_main_doc_tool(
        ctx: RunContext[PaperAgentDeps],
    ) -> Dict[str, Any]:
        return await _run_sync_tool(read_main_doc, ctx.deps)

    @agent.tool(
        name="write_main_doc",
        description=(
            "Replace the user's main writing doc. expected_revision must come "
            "from the most recent read_main_doc result."
        ),
    )
    async def write_main_doc_tool(
        ctx: RunContext[PaperAgentDeps],
        content: str,
        expected_revision: int,
    ) -> Dict[str, Any]:
        return await _run_sync_tool(
            write_main_doc,
            ctx.deps,
            content=content,
            expected_revision=expected_revision,
        )

    return agent


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
) -> AsyncGenerator[Union[str, Dict[str, Any]], None]:
    pai_model, resolved_provider, resolved_model = _build_pai_model(
        llm_client=llm_client,
        provider=llm_provider,
        model_type=model_type,
        model=model,
    )
    agent = build_pydantic_paper_agent(
        model=pai_model,
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
    )
    parser = EvidenceStreamParser(
        paper=paper,
        llm_client=llm_client,
        citation_reconciler=citation_reconciler,
    )
    start_times: Dict[str, float] = {}

    model_settings = _model_settings_for_provider(resolved_provider, reasoning_effort)
    message_history = _convert_message_history(conversation_history)

    async for event in agent.run_stream_events(
        user_message_text,
        message_history=message_history or None,
        deps=deps,
        model_settings=model_settings,
        usage_limits=UsageLimits(
            request_limit=max_agentic_iterations + 1,
            tool_calls_limit=max_agentic_iterations * 4,
        ),
        metadata={
            "paper_id": paper_id,
            "context_mode": context_mode,
            "provider": resolved_provider.value,
            "model": resolved_model,
        },
    ):
        if isinstance(event, PartDeltaEvent) and isinstance(
            event.delta, TextPartDelta
        ):
            for chunk in await parser.feed(event.delta.content_delta):
                yield chunk
            continue

        if isinstance(event, PartDeltaEvent) and isinstance(
            event.delta, ThinkingPartDelta
        ):
            if event.delta.content_delta:
                yield {
                    "type": "reasoning",
                    "content": event.delta.content_delta,
                }
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
            logger.debug(
                "Pydantic paper agent completed for paper=%s model=%s usage=%s",
                paper_id,
                resolved_model,
                getattr(event.result, "usage", None),
            )

    flushed = parser.flush()
    if flushed:
        yield flushed


def pretty_tool_status(fn_name: str, args: Dict[str, Any]) -> str:
    if fn_name == "read_section":
        return f"Reading section '{args.get('name', '?')}'"
    if fn_name == "read_pages":
        return f"Reading pages {args.get('start')}-{args.get('end')}"
    if fn_name == "search_paper":
        return f"Searching for '{args.get('query', '?')}'"
    if fn_name == "get_figure":
        return f"Fetching {args.get('label', 'figure')}"
    if fn_name == "read_main_doc":
        return "Reading your notes"
    if fn_name == "write_main_doc":
        return "Updating your notes"
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


def _build_pai_model(
    *,
    llm_client: BaseLLMClient,
    provider: Optional[LLMProvider],
    model_type: ModelType,
    model: Optional[str],
) -> tuple[Any, LLMProvider, str]:
    if model:
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
        return (
            make_openai_responses_model(
                resolved_model,
                api_key=provider_instance.api_key,
                base_url=None if provider_instance.is_azure else client_base_url,
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
    reasoning_effort: Optional[str],
) -> Optional[Dict[str, Any]]:
    if not reasoning_effort:
        return None
    if provider == LLMProvider.OPENAI:
        return OpenAIResponsesModelSettings(
            openai_reasoning_effort=str(reasoning_effort),
            openai_reasoning_summary="auto",
        )
    return None


def _convert_message_history(messages: Sequence[Message]) -> List[ModelMessage]:
    history: List[ModelMessage] = []
    for message in messages:
        content = str(getattr(message, "content", "") or "")
        if not content:
            continue
        if message.role == "user":
            history.append(ModelRequest(parts=[UserPromptPart(content=content)]))
        elif message.role == "assistant":
            history.append(ModelResponse(parts=[TextPart(content=content)]))
    return history
