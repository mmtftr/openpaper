"""Unit tests for the ephemeral quick-question endpoint.

Covers the things that can silently go wrong: which failures map to which
HTTP status, how a large file is windowed around the selection, that a stray
evidence block never reaches the client, and that the read-only repo lookup
tools are registered, budgeted, path-confined and visible on the wire.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import List

import pytest

from app.llm.chat.quick_question import (
    CODE_FULL_LINE_LIMIT,
    HEAD_LINES,
    MAX_QUESTION_CHARS,
    QUICK_QUESTION_SYSTEM_PROMPT,
    TRUNCATION_MARKER,
    WINDOW_CONTEXT_LINES,
    QuickQuestionError,
    build_code_context,
    build_quick_question_prompt,
    load_quick_question_code,
)
from app.llm.chat.quick_question_tools import (
    MAX_LOOKUPS,
    QuickQuestionRepoTools,
    register_repo_tools,
)
from app.llm.repo import storage
from app.llm.repo.prelude import RepoPrelude

PAPER_ID = "0a0a0a0a-0a0a-0a0a-0a0a-0a0a0a0a0a0a"
SHA = "c" * 40

SMALL_FILE = "\n".join(f"line {i}" for i in range(1, 21))


# -- range validation -----------------------------------------------------


def test_small_file_is_sent_whole_and_line_numbered():
    code = build_code_context(
        SMALL_FILE, file_path="a.py", start_line=3, end_line=5
    )
    assert code.truncated is False
    assert code.total_lines == 20
    assert " 1| line 1" in code.body
    assert "20| line 20" in code.body
    assert TRUNCATION_MARKER not in code.body
    # The selection block is separate and line-numbered.
    assert code.selection == " 3| line 3\n 4| line 4\n 5| line 5"


@pytest.mark.parametrize(
    "start,end",
    [(0, 5), (-1, 5), (5, 3), (21, 25)],
)
def test_invalid_ranges_are_422(start, end):
    with pytest.raises(QuickQuestionError) as excinfo:
        build_code_context(SMALL_FILE, file_path="a.py", start_line=start, end_line=end)
    assert excinfo.value.status_code == 422


def test_end_line_past_eof_is_clamped_not_rejected():
    """Dragging a selection past the last line is a normal editor gesture."""
    code = build_code_context(SMALL_FILE, file_path="a.py", start_line=18, end_line=999)
    assert (code.start_line, code.end_line) == (18, 20)
    assert code.selection.endswith("line 20")


def test_non_integer_range_is_422():
    with pytest.raises(QuickQuestionError) as excinfo:
        build_code_context(
            SMALL_FILE, file_path="a.py", start_line="x", end_line=5  # type: ignore[arg-type]
        )
    assert excinfo.value.status_code == 422


# -- truncation windowing -------------------------------------------------


def _big_file(lines: int = 3000) -> str:
    return "\n".join(f"code line {i}" for i in range(1, lines + 1))


def test_large_file_is_windowed_around_the_selection():
    content = _big_file()
    code = build_code_context(
        content, file_path="big.py", start_line=1500, end_line=1510
    )
    assert code.truncated is True
    assert code.total_lines == 3000

    # Head of the file is kept (imports / class declaration live there).
    assert "   1| code line 1" in code.body
    assert f"{HEAD_LINES}| code line {HEAD_LINES}" in code.body
    # The window around the selection is present...
    assert "1500| code line 1500" in code.body
    assert f"{1500 - WINDOW_CONTEXT_LINES}| code line {1500 - WINDOW_CONTEXT_LINES}" in code.body
    assert f"{1510 + WINDOW_CONTEXT_LINES}| code line {1510 + WINDOW_CONTEXT_LINES}" in code.body
    # ...and the gaps are explicitly marked, not silently dropped.
    assert code.body.count(TRUNCATION_MARKER) == 2
    # Lines outside head and window are gone.
    assert "code line 900\n" not in code.body
    assert "2999| code line 2999" not in code.body


def test_window_merges_with_the_head_when_they_overlap():
    """A selection near the top must not produce a duplicated head segment
    or a spurious truncation marker in the middle."""
    content = _big_file()
    code = build_code_context(content, file_path="big.py", start_line=80, end_line=90)
    assert code.truncated is True
    assert code.body.count("   1| code line 1\n") == 1
    # Only the trailing gap is marked.
    assert code.body.count(TRUNCATION_MARKER) == 1
    assert code.body.rstrip().endswith(TRUNCATION_MARKER)


def test_selection_at_eof_has_no_trailing_marker():
    content = _big_file()
    code = build_code_context(
        content, file_path="big.py", start_line=2995, end_line=3000
    )
    assert code.body.rstrip().endswith("3000| code line 3000")
    # Only the gap between head and window is marked.
    assert code.body.count(TRUNCATION_MARKER) == 1


def test_a_long_but_narrow_file_is_truncated_by_line_count():
    content = "\n".join("x" for _ in range(CODE_FULL_LINE_LIMIT + 10))
    code = build_code_context(content, file_path="many.py", start_line=1, end_line=2)
    assert code.truncated is True


def test_a_short_but_heavy_file_is_truncated_by_byte_size():
    content = "\n".join("y" * 5000 for _ in range(20))
    code = build_code_context(content, file_path="wide.py", start_line=1, end_line=2)
    assert code.truncated is True


# -- prompt assembly ------------------------------------------------------


def test_prompt_delimits_paper_file_selection_and_question():
    code = build_code_context(SMALL_FILE, file_path="a.py", start_line=2, end_line=3)
    prompt = build_quick_question_prompt(
        paper_preload="## Abstract\n\nWe study things.",
        code=code,
        question="What does this do?",
    )
    assert "<paper_context>" in prompt and "We study things." in prompt
    assert '<file path="a.py" total_lines="20">' in prompt
    assert '<selection path="a.py" start_line="2" end_line="3">' in prompt
    assert "<question>\nWhat does this do?\n</question>" in prompt
    assert "truncated" not in prompt


def test_prompt_flags_truncation_to_the_model():
    code = build_code_context(_big_file(), file_path="b.py", start_line=1500, end_line=1501)
    prompt = build_quick_question_prompt(
        paper_preload="", code=code, question="Explain"
    )
    assert 'truncated="true"' in prompt
    assert "shown partially" in prompt
    # An empty preload contributes no empty section.
    assert "<paper_context>" not in prompt


# -- snapshot lookup ------------------------------------------------------


@pytest.fixture()
def ready_snapshot(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("REPO_STORAGE_DIR", str(tmp_path))
    tree = storage.snapshot_dir(PAPER_ID, SHA) / storage.TREE_SUBDIR
    (tree / "pkg").mkdir(parents=True)
    (tree / "pkg" / "mod.py").write_text(SMALL_FILE, encoding="utf-8")
    storage.write_manifest(
        storage.snapshot_dir(PAPER_ID, SHA),
        {"owner": "o", "repo": "r", "ref": "main", "commit_sha": SHA,
         "files": [{"path": "pkg/mod.py", "size": len(SMALL_FILE)}]},
    )
    (storage.snapshot_dir(PAPER_ID, SHA) / storage.DONE_MARKER).write_text(
        "ok", encoding="utf-8"
    )

    import app.llm.chat.quick_question as qq

    class _Row:
        commit_sha = SHA

    class _Crud:
        @staticmethod
        def get_ready_for_paper(session, *, paper_id):
            return _Row()

    monkeypatch.setattr(
        "app.database.database.SessionLocal",
        lambda: type("S", (), {"close": lambda self: None})(),
    )
    monkeypatch.setattr("app.database.crud.paper_repo_crud.paper_repo_crud", _Crud)
    return qq


def test_load_returns_the_file_from_the_snapshot(ready_snapshot):
    loaded = load_quick_question_code(paper_id=PAPER_ID, file_path="pkg/mod.py")
    assert loaded.path == "pkg/mod.py"
    assert loaded.content == SMALL_FILE
    # The snapshot travels with the file so the lookup tools can be bound to
    # the same commit.
    assert loaded.commit_sha == SHA
    assert loaded.root == storage.tree_dir(PAPER_ID, SHA)
    assert [entry["path"] for entry in loaded.manifest_files] == ["pkg/mod.py"]


def test_load_tolerates_a_repo_prefixed_path(ready_snapshot):
    loaded = load_quick_question_code(
        paper_id=PAPER_ID, file_path="/repo/pkg/mod.py"
    )
    assert loaded.path == "pkg/mod.py"


@pytest.mark.parametrize(
    "bad_path",
    ["nope.py", "../../etc/passwd", "manifest.json", ".done", ""],
)
def test_files_outside_the_manifest_are_404(ready_snapshot, bad_path):
    with pytest.raises(QuickQuestionError) as excinfo:
        load_quick_question_code(paper_id=PAPER_ID, file_path=bad_path)
    assert excinfo.value.status_code == 404


def test_no_ready_repo_is_409(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("REPO_STORAGE_DIR", str(tmp_path))

    class _Crud:
        @staticmethod
        def get_ready_for_paper(session, *, paper_id):
            return None

    monkeypatch.setattr(
        "app.database.database.SessionLocal",
        lambda: type("S", (), {"close": lambda self: None})(),
    )
    monkeypatch.setattr("app.database.crud.paper_repo_crud.paper_repo_crud", _Crud)
    with pytest.raises(QuickQuestionError) as excinfo:
        load_quick_question_code(paper_id=PAPER_ID, file_path="pkg/mod.py")
    assert excinfo.value.status_code == 409


# -- evidence stripping (inert safety) ------------------------------------


def test_a_stray_evidence_block_never_reaches_the_client():
    """The prompt forbids citation markers, but a model that emits one
    anyway must not leak it as visible text."""
    from app.llm.chat.stream import EvidenceFilter

    filter_ = EvidenceFilter()
    visible = filter_.push(
        "The loop normalizes the direction.\n"
        "---EVIDENCE---\n@cite[1|file=a.py|lines=1-2]\n\"code\"\n---END-EVIDENCE---"
    )
    visible += filter_.flush()
    assert "The loop normalizes the direction." in visible
    assert "@cite" not in visible
    assert "EVIDENCE" not in visible


def test_evidence_stripping_survives_delta_splits():
    from app.llm.chat.stream import EvidenceFilter

    filter_ = EvidenceFilter()
    out = ""
    for piece in ["Answer text.", "\n---EVI", "DENCE---\n@cite[1]\n\"x\"\n---END-EV", "IDENCE---"]:
        out += filter_.push(piece)
    out += filter_.flush()
    assert out.strip() == "Answer text."


# -- character ceilings -----------------------------------------------------


def test_a_single_enormous_line_is_capped_by_characters():
    """Line windows alone let a 1 MB minified file into the prompt whole."""
    from app.llm.chat.quick_question import (
        CODE_BODY_CHAR_LIMIT,
        CODE_SELECTION_CHAR_LIMIT,
        build_code_context,
    )

    content = "x" * (1024 * 1024)
    code = build_code_context(content, file_path="min.js", start_line=1, end_line=1)
    assert code.truncated is True
    assert len(code.body) <= CODE_BODY_CHAR_LIMIT + 100
    assert len(code.selection) <= CODE_SELECTION_CHAR_LIMIT + 100
    assert "[line truncated]" in code.selection


def test_many_long_lines_are_capped_in_total():
    from app.llm.chat.quick_question import CODE_BODY_CHAR_LIMIT, build_code_context

    content = "\n".join("y" * 1_500 for _ in range(3000))
    code = build_code_context(content, file_path="big.py", start_line=1500, end_line=1500)
    assert code.truncated is True
    assert len(code.body) <= CODE_BODY_CHAR_LIMIT + 100


def test_a_long_line_in_a_small_file_marks_the_context_truncated():
    from app.llm.chat.quick_question import CODE_LINE_CHAR_LIMIT, build_code_context

    content = "short = 1\n" + "z" * (CODE_LINE_CHAR_LIMIT + 10) + "\nend = 2"
    code = build_code_context(content, file_path="a.py", start_line=1, end_line=1)
    assert code.truncated is True
    assert "[line truncated]" in code.body


def test_quick_question_usage_is_never_fatal(monkeypatch):
    """The usage write runs from the stream's `finally`; a DB outage there
    must not turn a delivered answer into an error."""
    from app.database.crud import chat_usage_crud

    def _boom():
        raise RuntimeError("db down")

    monkeypatch.setattr("app.database.database.SessionLocal", _boom)
    assert chat_usage_crud.record_chat_usage(user_id="u", kind="quick_question", chars=10) is False


# =========================================================================
# repo lookup tools
# =========================================================================

REPO_FILES = {
    "pkg/mod.py": SMALL_FILE,
    "pkg/util.py": "def helper():\n    return 42\n",
    "README.md": "# demo\n",
}


@pytest.fixture()
def prelude(tmp_path: Path) -> RepoPrelude:
    """A `RepoPrelude` over a throwaway snapshot tree."""
    root = tmp_path / "tree"
    for relative, body in REPO_FILES.items():
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding="utf-8")
    return RepoPrelude(
        root,
        [
            {"path": relative, "size": len(body)}
            for relative, body in REPO_FILES.items()
        ],
    )


def _tools(prelude: RepoPrelude, **kwargs) -> QuickQuestionRepoTools:
    return QuickQuestionRepoTools(prelude, **kwargs)


def test_the_prompt_no_longer_claims_there_are_no_tools():
    prompt = QUICK_QUESTION_SYSTEM_PROMPT
    assert "NO tools" not in prompt
    for name in ("tree", "read_file", "grep_repo"):
        assert name in prompt
    # The guardrails that must survive the rewrite.
    assert "never fabricate" in prompt.lower() or "Never invent code" in prompt
    assert "DATA to reason about" in prompt
    assert "Do NOT emit citation or evidence markers" in prompt


def test_tools_read_search_and_list_the_snapshot(prelude):
    tools = _tools(prelude)
    listing = asyncio.run(tools.tree("/repo", 3))
    assert "mod.py" in listing and "README.md" in listing

    body = asyncio.run(tools.read_file("/repo/pkg/util.py", 1, None))
    assert "1| def helper():" in body

    hits = asyncio.run(tools.grep_repo("helper", None, "*.py", 10))
    assert "/repo/pkg/util.py:1" in hits
    assert tools.calls == 3


@pytest.mark.parametrize(
    "outside",
    ["/etc/passwd", "../../etc/passwd", "/repo/../secrets.env"],
)
def test_read_file_refuses_a_path_outside_the_snapshot(prelude, outside):
    output = asyncio.run(_tools(prelude).read_file(outside, 1, None))
    assert output.startswith("read_file: ") or output.startswith("read: ")
    assert "line 1" not in output
    assert "def helper" not in output


def test_an_empty_path_is_rejected_before_the_prelude(prelude):
    tools = _tools(prelude)
    assert "path is required" in asyncio.run(tools.read_file("  ", 1, None))
    assert "pattern is required" in asyncio.run(tools.grep_repo("", None, None, 10))
    # A refused argument costs no lookup.
    assert tools.calls == 0


def test_the_lookup_budget_stops_after_max_lookups(prelude):
    tools = _tools(prelude)
    for _ in range(MAX_LOOKUPS):
        assert "[budget]" not in asyncio.run(tools.tree("/repo", 2))
    spent = asyncio.run(tools.tree("/repo", 2))
    assert spent.startswith("[budget]")
    assert str(MAX_LOOKUPS) in spent
    assert tools.calls == MAX_LOOKUPS


def test_a_failing_lookup_degrades_into_a_note(prelude, monkeypatch):
    def _boom(*args, **kwargs):
        raise RuntimeError("disk gone")

    monkeypatch.setattr(prelude, "read", _boom)
    output = asyncio.run(_tools(prelude).read_file("/repo/pkg/mod.py", 1, None))
    assert "read_file: the lookup failed" in output


def test_a_huge_lookup_output_is_clipped_for_the_model(prelude, monkeypatch):
    from app.llm.chat import quick_question_tools as qq_tools

    monkeypatch.setattr(
        prelude, "read", lambda *a, **k: "x" * (qq_tools.MAX_LOOKUP_OUTPUT + 5_000)
    )
    output = asyncio.run(_tools(prelude).read_file("/repo/pkg/mod.py", 1, None))
    assert len(output) < qq_tools.MAX_LOOKUP_OUTPUT + 200
    assert "output truncated" in output


def test_the_three_tools_are_registered_with_stable_names(prelude):
    from pydantic_ai import Agent
    from pydantic_ai.messages import ModelResponse, TextPart
    from pydantic_ai.models.function import FunctionModel

    seen: List[str] = []

    def respond(messages, info):
        seen.extend(tool.name for tool in info.function_tools)
        return ModelResponse(parts=[TextPart("done")])

    agent: Agent[None, str] = Agent(FunctionModel(respond), output_type=str)
    register_repo_tools(agent, _tools(prelude))
    agent.run_sync("hello")
    assert seen == ["tree", "read_file", "grep_repo"]


# =========================================================================
# the tools on the wire (end-to-end through the adapter)
# =========================================================================


def _script_model(script):
    """FunctionModel that streams `script[i]` for the i-th request."""
    from pydantic_ai.models.function import FunctionModel

    state = {"index": 0}

    async def stream_fn(messages, info):
        index = min(state["index"], len(script) - 1)
        state["index"] += 1
        for item in script[index]:
            yield item

    return FunctionModel(stream_function=stream_fn)


def _read_call(index: int, path: str = "/repo/pkg/mod.py"):
    from pydantic_ai.models.function import DeltaToolCall

    return [
        {
            0: DeltaToolCall(
                name="read_file",
                json_args=json.dumps({"path": path}),
                tool_call_id=f"call-{index}",
            )
        }
    ]


def _parse_sse(encoded: List[str]) -> List[dict]:
    chunks: List[dict] = []
    for raw in encoded:
        assert raw.startswith("data: ") and raw.endswith("\n\n"), raw
        body = raw[len("data: ") : -2]
        if body == "[DONE]":
            continue
        chunks.append(json.loads(body))
    return chunks


@pytest.fixture()
def quick_question_run(ready_snapshot, monkeypatch):
    """Drive the real `run_quick_question` against a scripted model."""
    from app.database.models import SubscriptionPlan
    from app.llm.model_registry import ModelSpec
    from app.llm.provider import LLMProvider
    import app.helpers.subscription_limits as limits

    qq = ready_snapshot
    recorded = SimpleNamespace(
        events=[], usage=[], user=SimpleNamespace(id=uuid.uuid4()), chunks=[]
    )
    spec = ModelSpec(
        id="scripted", provider=LLMProvider.OPENAI, display_name="Scripted"
    )

    monkeypatch.setattr(
        qq,
        "build_paper_chat_context",
        lambda *a, **k: SimpleNamespace(
            paper=SimpleNamespace(id=PAPER_ID), context_mode="adaptive"
        ),
    )
    monkeypatch.setattr(qq, "_select_preload", lambda mode, paper: "")
    monkeypatch.setattr(
        qq,
        "track_event",
        lambda name, **kwargs: recorded.events.append((name, kwargs)),
    )
    monkeypatch.setattr(
        qq,
        "record_chat_usage",
        lambda **kwargs: recorded.usage.append(kwargs) is None,
    )
    monkeypatch.setattr(limits, "can_user_chat", lambda db, user: (True, None))
    monkeypatch.setattr(
        limits,
        "get_user_subscription_plan",
        lambda db, user: SubscriptionPlan.BASIC,
    )

    def run(model, *, question="What does this do?"):
        class FakeRegistry:
            def resolve(self, provider=None, model_id=None, role=None):
                return spec

            def build_model(self, _spec):
                return model

            def build_settings(self, _spec, reasoning_effort=None, **_kwargs):
                return None

        monkeypatch.setattr(qq, "get_registry", lambda: FakeRegistry())

        async def drive():
            out: List[str] = []
            stream = qq.run_quick_question(
                db=SimpleNamespace(),
                current_user=recorded.user,
                accept=None,
                paper_id=PAPER_ID,
                question=question,
                file_path="pkg/mod.py",
                start_line=1,
                end_line=3,
                provider=None,
                model=None,
                reasoning_effort=None,
            )
            async for encoded in stream:
                out.append(encoded)
            return out

        recorded.chunks = _parse_sse(asyncio.run(drive()))
        return recorded

    return run


def test_a_tool_call_round_trips_through_the_stream(quick_question_run):
    recorded = quick_question_run(
        _script_model([_read_call(1), ["Lines 1-3 are a preamble."]])
    )
    types = [chunk.get("type") for chunk in recorded.chunks]
    assert "tool-input-available" in types
    assert "tool-output-available" in types

    call = next(c for c in recorded.chunks if c.get("type") == "tool-input-available")
    assert call["toolName"] == "read_file"
    output = next(
        c for c in recorded.chunks if c.get("type") == "tool-output-available"
    )
    # The prelude's line-numbered read, verbatim on the wire.
    assert "1| line 1" in json.dumps(output["output"])

    text = "".join(
        chunk.get("delta", "")
        for chunk in recorded.chunks
        if chunk.get("type") == "text-delta"
    )
    assert "Lines 1-3 are a preamble." in text

    event = next(name for name, _ in recorded.events if name == "quick_question_asked")
    properties = next(
        kwargs["properties"]
        for name, kwargs in recorded.events
        if name == "quick_question_asked"
    )
    assert event == "quick_question_asked"
    assert properties["tool_calls"] == 1
    assert properties["delivered"] is True
    # Quota is still charged: question + answer characters.
    assert recorded.usage and recorded.usage[0]["kind"] == "quick_question"


def test_an_over_eager_model_still_gets_an_answer_out(quick_question_run):
    """The soft budget must degrade into a message, not a raised usage limit."""
    script = [_read_call(index) for index in range(1, MAX_LOOKUPS + 2)]
    script.append(["Answering from what I read."])
    recorded = quick_question_run(_script_model(script))

    outputs = [
        json.dumps(chunk["output"])
        for chunk in recorded.chunks
        if chunk.get("type") == "tool-output-available"
    ]
    assert len(outputs) == MAX_LOOKUPS + 1
    assert "[budget]" in outputs[-1]
    assert all("[budget]" not in output for output in outputs[:-1])

    text = "".join(
        chunk.get("delta", "")
        for chunk in recorded.chunks
        if chunk.get("type") == "text-delta"
    )
    assert "Answering from what I read." in text
    assert [c.get("type") for c in recorded.chunks].count("error") == 0

    properties = next(
        kwargs["properties"]
        for name, kwargs in recorded.events
        if name == "quick_question_asked"
    )
    assert properties["tool_calls"] == MAX_LOOKUPS


def test_a_parallel_batch_of_calls_degrades_instead_of_raising(quick_question_run):
    """pydantic-ai checks tool_calls_limit against the PROJECTED batch, so a
    single response carrying more calls than the hard limit would raise
    before any ran. The ceiling must sit far enough above the soft budget
    that a burst degrades through the wrapper's "[budget]" replies."""
    from pydantic_ai.models.function import DeltaToolCall

    batch = MAX_LOOKUPS * 2 + 1
    burst = {
        index: DeltaToolCall(
            name="read_file",
            json_args=json.dumps({"path": "/repo/pkg/mod.py"}),
            tool_call_id=f"call-{index}",
        )
        for index in range(batch)
    }
    recorded = quick_question_run(_script_model([[burst], ["Answered."]]))

    types = [chunk.get("type") for chunk in recorded.chunks]
    assert "error" not in types, types
    outputs = [
        json.dumps(chunk["output"])
        for chunk in recorded.chunks
        if chunk.get("type") == "tool-output-available"
    ]
    assert len(outputs) == batch
    assert sum("[budget]" in output for output in outputs) == batch - MAX_LOOKUPS
    text = "".join(
        chunk.get("delta", "")
        for chunk in recorded.chunks
        if chunk.get("type") == "text-delta"
    )
    assert "Answered." in text


def test_a_busy_worker_returns_a_note_and_refunds_the_lookup(prelude, monkeypatch):
    import threading

    import app.llm.chat.quick_question_tools as qqt

    taken = threading.BoundedSemaphore(1)
    assert taken.acquire(blocking=False)
    monkeypatch.setattr(qqt, "_slots", taken)
    monkeypatch.setattr(qqt, "LOOKUP_SLOT_WAIT", 0.05)

    tools = _tools(prelude)
    output = asyncio.run(tools.read_file("/repo/pkg/mod.py", 1, None))
    assert output.startswith("read_file: ")
    assert "busy" in output
    # Not the model's fault: the budget is untouched.
    assert tools.calls == 0


def test_the_lookup_slot_is_released_when_the_helper_returns(prelude):
    import app.llm.chat.quick_question_tools as qqt

    tools = _tools(prelude)
    assert "1| " in asyncio.run(tools.read_file("/repo/pkg/mod.py", 1, None))
    # Every permit is back: the full pool can be taken without blocking.
    permits = [qqt._slots.acquire(blocking=False) for _ in range(qqt.MAX_CONCURRENT_LOOKUPS)]
    for _ in permits:
        qqt._slots.release()
    assert all(permits)
