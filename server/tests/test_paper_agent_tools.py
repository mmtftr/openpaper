"""Which tools `build_paper_agent` registers, and what the executor reports.

Full mode used to register none of the paper-reading tools even though the
system prompt lists all four unconditionally: a "walk me through Figure 3"
question had no way to fetch the bitmap, and two calls to a tool that isn't
there exhaust the agent's single retry and fail the turn.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any, Dict, List

import pytest

from app.llm.chat import paper as paper_module
from app.llm.chat.paper import PaperAgentDeps, build_paper_agent

PAPER_ID = "33333333-3333-3333-3333-333333333333"
USER = SimpleNamespace(id="44444444-4444-4444-4444-444444444444")

PAPER_TOOLS = {"read_section", "read_pages", "search_paper"}
DOC_TOOLS = {"list_docs", "read_doc", "write_doc"}


def _paper(parser: str = "mistral") -> SimpleNamespace:
    return SimpleNamespace(
        id=PAPER_ID,
        title="A Paper",
        authors=["A. Author"],
        page_count=3,
        parser=parser,
        ocr={"pages": [{"index": 0, "markdown": "# Intro\n\nbody"}], "figures": []},
        raw_content="# Intro\n\nbody",
        page_offset_map={1: [0, 13]},
        abstract="abstract",
    )


def _deps(paper: Any, context_mode: str) -> PaperAgentDeps:
    return PaperAgentDeps(
        paper_id=PAPER_ID,
        paper=paper,
        current_user=USER,
        db=None,
        context_mode=context_mode,
        allowed_paper_ids=[PAPER_ID],
    )


def _registered_tool_names(
    *, parser: str = "mistral", context_mode: str = "adaptive", vision: bool = True
) -> List[str]:
    """Names the model actually sees, taken off the request the agent sends."""
    from pydantic_ai.messages import ModelResponse, TextPart
    from pydantic_ai.models.function import FunctionModel

    seen: List[str] = []

    def respond(messages, info):
        seen.extend(tool.name for tool in info.function_tools)
        return ModelResponse(parts=[TextPart("done")])

    paper = _paper(parser)
    agent = build_paper_agent(
        model=FunctionModel(respond),
        # Only `supports_vision` is read off the spec, so a stub keeps this
        # test decoupled from the ModelSpec dataclass.
        spec=SimpleNamespace(supports_vision=vision),
        system_prompt="system",
        paper=paper,
        context_mode=context_mode,
    )
    agent.run_sync("hello", deps=_deps(paper, context_mode))
    return seen


@pytest.mark.parametrize("context_mode", ["adaptive", "comprehensive", "full"])
def test_paper_tools_are_registered_in_every_mode(context_mode):
    names = set(_registered_tool_names(context_mode=context_mode))
    assert PAPER_TOOLS <= names
    assert DOC_TOOLS <= names


def test_full_mode_registers_get_figure_for_a_mistral_paper():
    """Full mode pre-loads the markdown, which carries no figure bitmaps —
    a vision model still needs get_figure to look at Figure 3."""
    assert "get_figure" in _registered_tool_names(context_mode="full")


def test_get_figure_is_registered_for_a_vision_less_model_too():
    # Without vision it returns caption/label/page only, but it must exist.
    names = _registered_tool_names(context_mode="full", vision=False)
    assert "get_figure" in names


def test_raw_mode_has_the_reading_tools_but_no_figures():
    names = set(_registered_tool_names(parser="pymupdf", context_mode="raw"))
    assert PAPER_TOOLS <= names
    assert "get_figure" not in names


def test_run_python_is_absent_without_a_repo_snapshot():
    assert "run_python" not in _registered_tool_names()


# =====================================================================
# tool telemetry
# =====================================================================


def _run_tool(monkeypatch, result: Any) -> Dict[str, Any]:
    """Run `_run_sync_tool` over a stub tool, returning the tracked payload."""
    events: List[Dict[str, Any]] = []
    monkeypatch.setattr(
        paper_module,
        "track_event",
        lambda name, properties, **kwargs: events.append({"name": name, **properties}),
    )

    def _tool(**kwargs: Any) -> Any:
        return result

    deps = _deps(_paper(), "adaptive")
    returned = asyncio.run(
        paper_module._run_sync_tool(_tool, deps, tool_name="read_pages")
    )
    assert returned is result
    assert len(events) == 1
    return events[0]


def test_telemetry_flags_a_truncated_result(monkeypatch):
    event = _run_tool(monkeypatch, {"content": "x", "truncated": True})
    assert event["name"] == "paper_agentic_tool_call"
    assert event["tool"] == "read_pages"
    assert event["truncated"] is True
    assert event["error"] is False


def test_telemetry_flags_an_errored_result(monkeypatch):
    event = _run_tool(monkeypatch, {"error": "Invalid page range"})
    assert event["error"] is True
    assert event["truncated"] is False


def test_telemetry_of_a_clean_result_flags_neither(monkeypatch):
    event = _run_tool(monkeypatch, {"content": "x", "pages_returned": [1, 2]})
    assert event["truncated"] is False
    assert event["error"] is False


def test_telemetry_tolerates_a_non_dict_result(monkeypatch):
    # The figure path can hand back non-mapping payloads; those must not
    # blow up the event, they just carry no truncated/error flags.
    event = _run_tool(monkeypatch, ["not", "a", "dict"])
    assert "truncated" not in event
    assert "error" not in event
    assert event["tool"] == "read_pages"
