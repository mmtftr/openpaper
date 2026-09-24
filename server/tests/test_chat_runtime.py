"""Unit tests for the agentic-chat runtime's pure logic.

Covers the four modules that have no I/O in their hot paths:

- `app.llm.chat.stream.EvidenceFilter` — incremental evidence holdback
- `app.llm.chat.citations` — evidence split / strip / citation extraction
- `app.llm.chat.history` — model-history replay + UIMessage serialization
- `app.llm.model_registry` — capability table, resolution, model settings
"""

from __future__ import annotations

import copy
import json
import random
import uuid
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

import pytest

from app.llm.chat.citations import (
    EVIDENCE_END,
    EVIDENCE_START,
    extract_citations,
    split_evidence_block,
    strip_evidence_blocks,
)
from app.llm.chat.history import (
    BUCKET_DUMP_KEY,
    BUCKET_VERSION,
    BUCKET_VERSION_KEY,
    IMAGE_PLACEHOLDER,
    MIN_HISTORY_ROWS,
    load_model_history,
    serialize_ui_messages,
    strip_figure_bytes,
    strip_instructions,
    strip_replayed_images,
)
from app.llm.chat.stream import (
    TOOL_OUTPUT_WIRE_CAP,
    EvidenceFilter,
    truncate_tool_output,
)
from app.llm.model_registry import (
    ModelRegistry,
    ModelRole,
    ModelSpec,
    _build_spec,
    _family_defaults,
)
from app.llm.provider import LLMProvider
from pydantic_ai.ui.vercel_ai.request_types import TextUIPart as TextUIPartRequest
from pydantic_ai.ui.vercel_ai.request_types import UIMessage as UIMessageRequest
from pydantic_ai.messages import (
    ModelMessagesTypeAdapter,
    ModelRequest,
    ModelResponse,
    RetryPromptPart,
    TextPart,
    ThinkingPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)


# =====================================================================
# EvidenceFilter
# =====================================================================


def _drive(filter_: EvidenceFilter, chunks: List[str]) -> str:
    out = "".join(filter_.push(c) for c in chunks)
    return out + filter_.flush()


class TestEvidenceFilter:
    def test_plain_text_passes_through(self):
        f = EvidenceFilter()
        assert _drive(f, ["hello ", "world"]) == "hello world"

    def test_single_block_removed(self):
        f = EvidenceFilter()
        text = f'answer text{EVIDENCE_START}\n@cite[1]\n"q"\n{EVIDENCE_END}'
        assert _drive(f, [text]) == "answer text"

    def test_resumes_after_end_marker(self):
        """A mid-message block must not black-hole the rest of the answer."""
        f = EvidenceFilter()
        text = f"before{EVIDENCE_START}hidden{EVIDENCE_END}after"
        assert _drive(f, [text]) == "beforeafter"

    def test_start_delimiter_split_across_deltas(self):
        f = EvidenceFilter()
        chunks = ["hello ", "---", "EVID", "ENCE---", "secret", EVIDENCE_END, "tail"]
        assert _drive(f, chunks) == "hello tail"

    def test_end_delimiter_split_across_deltas(self):
        f = EvidenceFilter()
        chunks = ["a", EVIDENCE_START, "s1", "---END-", "EVIDENCE", "---", "b"]
        assert _drive(f, chunks) == "ab"

    def test_char_by_char_never_leaks_delimiter(self):
        text = f'Answer.\n\n{EVIDENCE_START}\n@cite[1|page=2]\n"quote"\n{EVIDENCE_END}\n'
        f = EvidenceFilter()
        emitted = _drive(f, list(text))
        assert "---" not in emitted
        assert "EVIDENCE" not in emitted
        assert emitted.strip() == "Answer."

    def test_unterminated_block_suppresses_to_end(self):
        f = EvidenceFilter()
        out = _drive(f, ["answer", EVIDENCE_START, "@cite[1]", '"trunc'])
        assert out == "answer"
        assert f.in_evidence is True

    def test_flush_returns_held_non_delimiter_tail(self):
        """A trailing partial-delimiter that never completes must be emitted."""
        f = EvidenceFilter()
        assert f.push("answer ---") == "answer "
        assert f.flush() == "---"
        assert f.flush() == ""

    def test_flush_inside_evidence_emits_nothing(self):
        f = EvidenceFilter()
        f.push(f"a{EVIDENCE_START}partial---END-")
        assert f.flush() == ""

    def test_near_miss_delimiter_is_emitted(self):
        f = EvidenceFilter()
        assert _drive(f, ["x---EVIDENC", "E-oops"]) == "x---EVIDENCE-oops"

    def test_multiple_blocks(self):
        f = EvidenceFilter()
        text = (
            f"one{EVIDENCE_START}a{EVIDENCE_END}"
            f"two{EVIDENCE_START}b{EVIDENCE_END}three"
        )
        assert _drive(f, [text]) == "onetwothree"

    @pytest.mark.parametrize("seed", range(25))
    def test_matches_strip_evidence_blocks_for_random_chunkings(self, seed: int):
        """The streaming filter and the persistence-side stripper must agree."""
        rng = random.Random(seed)
        pieces = [
            "Some prose. ",
            EVIDENCE_START,
            '\n@cite[1|page=3]\n"quoted text"\n',
            EVIDENCE_END,
            " trailing words ",
            "---EVIDENC",
            "E-not-a-marker ",
        ]
        text = "".join(rng.sample(pieces, k=len(pieces)))
        # Random chunk boundaries
        cuts = sorted(rng.sample(range(1, len(text)), k=min(12, len(text) - 1)))
        chunks, prev = [], 0
        for c in cuts:
            chunks.append(text[prev:c])
            prev = c
        chunks.append(text[prev:])

        streamed = _drive(EvidenceFilter(), chunks)
        assert streamed.strip() == strip_evidence_blocks(text)


class TestTruncateToolOutput:
    def test_small_output_preserved_verbatim(self):
        out = {"a": 1, "b": ["x", "y"]}
        assert truncate_tool_output(out) is out

    def test_large_output_becomes_preview_marker(self):
        out = {"content": "x" * (TOOL_OUTPUT_WIRE_CAP + 500)}
        result = truncate_tool_output(out)
        assert result["truncated"] is True
        # The preview is the trimmed VALUE, not a slice of its encoding.
        assert isinstance(result["preview"], dict)
        assert result["preview"]["content"].startswith("xxx")
        assert result["preview"]["content"].endswith(" chars]")
        assert len(json.dumps(result)) <= TOOL_OUTPUT_WIRE_CAP + 100
        assert "files" not in result

    def test_run_python_shape_keeps_files_and_previews_output_text(self):
        out = {
            "files": ["a.py", "b.py"],
            "output": "o" * (TOOL_OUTPUT_WIRE_CAP + 50),
        }
        result = truncate_tool_output(out)
        assert result["truncated"] is True
        assert result["files"] == ["a.py", "b.py"]
        assert result["preview"] == "o" * TOOL_OUTPUT_WIRE_CAP
        assert result["omitted_chars"] == 50

    def test_unserializable_output_falls_back_to_str(self):
        class Weird:
            def __repr__(self):
                return "W" * (TOOL_OUTPUT_WIRE_CAP + 10)

        result = truncate_tool_output(Weird())
        assert result["truncated"] is True


class TestOpenPaperAdapter:
    """Regression cover for the adapter <-> event-stream wiring.

    `VercelAIAdapter` calls `build_event_stream()` twice per request — once
    from `transform_stream` (the instance that sees the run's events) and
    once from `encode_stream` (encoding only). If the second call replaces
    `last_event_stream`, `accumulated_text` is empty at `on_complete`, which
    silently empties the persisted `content`, kills citation extraction and
    disables partial-turn persistence.
    """

    def _adapter(self):
        from app.llm.chat.stream import OpenPaperAdapter
        from pydantic_ai import Agent
        from pydantic_ai.ui.vercel_ai.request_types import SubmitMessage

        run_input = SubmitMessage(
            id="conv-1",
            trigger="submit-message",
            messages=[
                UIMessageRequest(
                    id="m1", role="user", parts=[TextUIPartRequest(text="hi")]
                )
            ],
        )
        return OpenPaperAdapter(
            agent=Agent("test"),
            run_input=run_input,
            accept=None,
            sdk_version=6,
            server_message_id="assistant-id",
        )

    def test_build_event_stream_is_memoized(self):
        adapter = self._adapter()
        first = adapter.build_event_stream()
        second = adapter.build_event_stream()
        assert first is second
        assert adapter.last_event_stream is first

    def test_last_event_stream_accumulates_raw_text(self):
        import asyncio

        from pydantic_ai.messages import TextPart as PaiTextPart
        from pydantic_ai.messages import TextPartDelta

        adapter = self._adapter()
        stream = adapter.build_event_stream()
        adapter.build_event_stream()  # the encode_stream() rebuild

        async def drive():
            chunks = []
            async for c in stream.handle_text_start(PaiTextPart(content="he")):
                chunks.append(c)
            async for c in stream.handle_text_delta(
                TextPartDelta(
                    content_delta=f"llo{EVIDENCE_START}@cite[1]{EVIDENCE_END}!"
                )
            ):
                chunks.append(c)
            async for c in stream.handle_text_end(PaiTextPart(content="")):
                chunks.append(c)
            return chunks

        chunks = asyncio.run(drive())
        raw = f"hello{EVIDENCE_START}@cite[1]{EVIDENCE_END}!"
        assert adapter.last_event_stream is stream
        assert adapter.last_event_stream.accumulated_text == raw
        visible = "".join(getattr(c, "delta", "") for c in chunks)
        assert visible == "hello!"
        assert strip_evidence_blocks(raw) == "hello!"


# =====================================================================
# citations
# =====================================================================


class TestSplitEvidenceBlock:
    def test_absent_block(self):
        assert split_evidence_block("plain answer") == ("plain answer", "")

    def test_well_formed_block(self):
        text = f'answer\n{EVIDENCE_START}\n@cite[1]\n"q"\n{EVIDENCE_END}\n'
        before, inner = split_evidence_block(text)
        assert before == "answer"
        assert "@cite[1]" in inner
        assert EVIDENCE_END not in inner

    def test_truncated_block_runs_to_end(self):
        text = f'answer\n{EVIDENCE_START}\n@cite[1]\n"q'
        before, inner = split_evidence_block(text)
        assert before == "answer"
        assert inner.strip().startswith("@cite[1]")


class TestStripEvidenceBlocks:
    def test_multiple_blocks_removed(self):
        text = f"a{EVIDENCE_START}x{EVIDENCE_END}b{EVIDENCE_START}y{EVIDENCE_END}c"
        assert strip_evidence_blocks(text) == "abc"

    def test_unterminated_block_removed_to_end(self):
        assert strip_evidence_blocks(f"a{EVIDENCE_START}x") == "a"

    def test_no_block_is_stripped_only(self):
        assert strip_evidence_blocks("  hello  ") == "hello"


class TestExtractCitations:
    def test_named_extras_page_and_paper_id(self):
        pid = str(uuid.uuid4())
        text = (
            "answer\n"
            f"{EVIDENCE_START}\n"
            '@cite[1|page=3]\n"first quote"\n'
            f'@cite[2|page=7|paper_id={pid}]\n"second quote"\n'
            '@cite[3]\n"no page"\n'
            f"{EVIDENCE_END}"
        )
        cits = extract_citations(text)
        assert [c["key"] for c in cits] == [1, 2, 3]
        # The parser keeps the surrounding quote characters verbatim;
        # `reconcile_citations` strips them before matching.
        assert cits[0] == {"key": 1, "reference": '"first quote"', "page": 3}
        assert cits[1]["page"] == 7
        assert cits[1]["paper_id"] == pid
        assert "page" not in cits[2]

    def test_multiline_quote_is_joined(self):
        text = (
            f"{EVIDENCE_START}\n"
            '@cite[1|page=1]\n"line one\nline two"\n'
            f"{EVIDENCE_END}"
        )
        cits = extract_citations(text)
        assert cits[0]["reference"] == '"line one line two"'

    def test_duplicate_keys_keep_first(self):
        text = (
            f"{EVIDENCE_START}\n@cite[1]\n\"A\"\n{EVIDENCE_END}"
            f"{EVIDENCE_START}\n@cite[1]\n\"B\"\n@cite[2]\n\"C\"\n{EVIDENCE_END}"
        )
        cits = extract_citations(text)
        assert [(c["key"], c["reference"]) for c in cits] == [
            (1, '"A"'),
            (2, '"C"'),
        ]

    def test_no_block_yields_nothing(self):
        assert extract_citations("just prose") == []

    def test_unterminated_block_still_parsed(self):
        text = f'{EVIDENCE_START}\n@cite[1|page=2]\n"quote"'
        cits = extract_citations(text)
        assert cits and cits[0]["page"] == 2

    def test_malformed_page_value_ignored(self):
        text = f'{EVIDENCE_START}\n@cite[1|page=abc]\n"q"\n{EVIDENCE_END}'
        cits = extract_citations(text)
        assert "page" not in cits[0]

    def test_trailing_citation_without_body_is_dropped(self):
        """Documents current parser behavior (asymmetric with mid-block)."""
        text = f'{EVIDENCE_START}\n@cite[1]\n"q"\n@cite[2]\n{EVIDENCE_END}'
        cits = extract_citations(text)
        assert [c["key"] for c in cits] == [1]


# =====================================================================
# history: load_model_history
# =====================================================================


def _row(
    role: str,
    content: str,
    *,
    bucket: Optional[Dict[str, Any]] = None,
    references: Optional[Dict[str, Any]] = None,
    row_id: Optional[str] = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        id=row_id or str(uuid.uuid4()),
        role=role,
        content=content,
        bucket=bucket,
        references=references,
    )


def _dump(messages) -> Any:
    return ModelMessagesTypeAdapter.dump_python(messages, mode="json")


def _simple_turn_dump(prompt: str, answer: str) -> Any:
    return _dump(
        [
            ModelRequest(parts=[UserPromptPart(content=prompt)]),
            ModelResponse(parts=[TextPart(content=answer)]),
        ]
    )


def _tool_turn_dump(prompt: str, answer: str) -> Any:
    return _dump(
        [
            ModelRequest(parts=[UserPromptPart(content=prompt)]),
            ModelResponse(
                parts=[
                    ThinkingPart(content="thinking about it"),
                    ToolCallPart(
                        tool_name="read_section",
                        args={"name": "Methods"},
                        tool_call_id="call_1",
                    ),
                ]
            ),
            ModelRequest(
                parts=[
                    ToolReturnPart(
                        tool_name="read_section",
                        content={"text": "M" * 20},
                        tool_call_id="call_1",
                    )
                ]
            ),
            ModelResponse(parts=[TextPart(content=answer)]),
        ]
    )


def _bucketed(dump: Any, client_id: str = "c1") -> Dict[str, Any]:
    return {
        "client_message_id": client_id,
        BUCKET_VERSION_KEY: BUCKET_VERSION,
        BUCKET_DUMP_KEY: dump,
    }


class TestLoadModelHistory:
    def test_empty(self):
        assert load_model_history([]) == []

    def test_legacy_text_only_rows(self):
        rows = [
            _row("user", "q1"),
            _row("assistant", "a1"),
            _row("user", "q2"),
            _row("assistant", "a2"),
        ]
        history = load_model_history(rows)
        assert [type(m).__name__ for m in history] == [
            "ModelRequest",
            "ModelResponse",
            "ModelRequest",
            "ModelResponse",
        ]
        assert history[0].parts[0].content == "q1"
        assert history[1].parts[0].content == "a1"

    def test_dangling_user_row_is_flushed(self):
        rows = [_row("user", "q1"), _row("assistant", "a1"), _row("user", "q2")]
        history = load_model_history(rows)
        assert len(history) == 3
        assert isinstance(history[-1], ModelRequest)
        assert history[-1].parts[0].content == "q2"

    def test_dump_replay_replaces_text_fallback(self):
        dump = _tool_turn_dump("q1", "a1")
        rows = [
            _row("user", "q1"),
            _row("assistant", "a1", bucket=_bucketed(dump)),
        ]
        history = load_model_history(rows)
        # The dump carries its own ModelRequest, so the user row must NOT be
        # duplicated as a separate text request.
        assert [type(m).__name__ for m in history] == [
            "ModelRequest",
            "ModelResponse",
            "ModelRequest",
            "ModelResponse",
        ]
        tool_calls = [
            p
            for m in history
            if isinstance(m, ModelResponse)
            for p in m.parts
            if isinstance(p, ToolCallPart)
        ]
        assert len(tool_calls) == 1
        assert tool_calls[0].tool_name == "read_section"

    def test_turns_outside_the_window_degrade_to_text(self):
        old = _simple_turn_dump("q1", "a1" + "X" * 5000)
        new = _simple_turn_dump("q2", "a2")
        rows = [
            _row("user", "q1"),
            _row("assistant", "a1-text", bucket=_bucketed(old)),
            _row("user", "q2"),
            _row("assistant", "a2-text", bucket=_bucketed(new)),
        ]
        # Only the newest turn fits, and its user row is a grid line.
        history = load_model_history(
            rows, replay_chars=len(json.dumps(new)) + 10, replay_chunk=1
        )
        texts = [
            p.content
            for m in history
            for p in m.parts
            if isinstance(p, (TextPart, UserPromptPart))
        ]
        assert "a1-text" in texts  # degraded to the DB text column
        assert "q1" in texts  # its user row survived as text
        assert "a2" in texts  # newest turn replayed from the dump
        assert not any("XXXXX" in t for t in texts)

    def test_zero_window_degrades_everything(self):
        rows = [
            _row("user", "q1"),
            _row("assistant", "a1-text", bucket=_bucketed(_simple_turn_dump("q1", "a1"))),
        ]
        history = load_model_history(rows, replay_chars=0)
        assert len(history) == 2
        assert history[1].parts[0].content == "a1-text"

    def test_text_cap_drops_the_oldest_turns_entirely(self):
        """Degraded text is bounded too: past the cap, older rows are dropped
        rather than resent forever (and the history opens on a question)."""
        rows = []
        for i in range(6):
            rows.append(_row("user", f"q{i}"))
            rows.append(_row("assistant", f"a{i}" + "T" * 1000))
        # `text_chunk=1` puts a grid line on every user row, so the tail
        # boundary is exactly "the oldest turn that still fits".
        history = load_model_history(
            rows, replay_chars=0, text_chars=2500, text_chunk=1
        )
        texts = [
            p.content
            for m in history
            for p in m.parts
            if isinstance(p, (TextPart, UserPromptPart))
        ]
        assert texts[0] == "q4"
        assert isinstance(history[0].parts[0], UserPromptPart)
        assert any(t.startswith("a5") for t in texts)
        assert "q3" not in texts
        assert not any(t.startswith("a3") for t in texts)

    def test_text_cap_never_drops_the_newest_turn(self):
        rows = [_row("user", "q"), _row("assistant", "a" * 5000)]
        history = load_model_history(rows, replay_chars=0, text_chars=10)
        assert len(history) == 2

    def test_min_history_rows_survive_every_cap(self):
        """The newest MIN_HISTORY_ROWS rows are sent however small the caps."""
        rows = []
        for i in range(6):
            rows.append(_row("user", f"q{i}" + "Q" * 500))
            rows.append(_row("assistant", f"a{i}" + "A" * 500))
        history = load_model_history(
            rows, replay_chars=0, text_chars=0, text_chunk=1
        )
        texts = [
            p.content
            for m in history
            for p in m.parts
            if isinstance(p, (TextPart, UserPromptPart))
        ]
        assert len(history) == MIN_HISTORY_ROWS
        assert texts[0].startswith("q5")
        assert texts[1].startswith("a5")

    def test_row_without_dump_uses_text(self):
        rows = [
            _row("user", "q1"),
            _row("assistant", "a1", bucket={"client_message_id": "c1"}),
        ]
        history = load_model_history(rows)
        assert history[1].parts[0].content == "a1"

    def test_interrupted_row_has_no_dump_and_degrades(self):
        rows = [
            _row("user", "q1"),
            _row("assistant", "partial", bucket={"interrupted": True}),
        ]
        history = load_model_history(rows)
        assert [type(m).__name__ for m in history] == ["ModelRequest", "ModelResponse"]
        assert history[1].parts[0].content == "partial"

    def test_empty_content_rows_are_skipped(self):
        rows = [_row("user", "q1"), _row("assistant", "")]
        history = load_model_history(rows)
        assert len(history) == 1

    def test_corrupt_dump_falls_back_to_text_and_keeps_user_prompt(self):
        """A dump that fails to deserialize must not lose the user turn."""
        rows = [
            _row("user", "q1"),
            _row(
                "assistant",
                "a1-text",
                bucket={BUCKET_VERSION_KEY: 1, BUCKET_DUMP_KEY: [{"bogus": True}]},
            ),
        ]
        history = load_model_history(rows)
        kinds = [type(m).__name__ for m in history]
        assert kinds == ["ModelRequest", "ModelResponse"], (
            f"user prompt lost on corrupt-dump fallback; got {kinds}"
        )

    def test_retry_prompt_round_trips(self):
        dump = _dump(
            [
                ModelRequest(parts=[UserPromptPart(content="q")]),
                ModelResponse(
                    parts=[
                        ToolCallPart(tool_name="read_pages", args={"start": 1}, tool_call_id="t")
                    ]
                ),
                ModelRequest(
                    parts=[
                        RetryPromptPart(
                            content="missing end", tool_name="read_pages", tool_call_id="t"
                        )
                    ]
                ),
                ModelResponse(parts=[TextPart(content="done")]),
            ]
        )
        rows = [_row("user", "q"), _row("assistant", "done", bucket=_bucketed(dump))]
        history = load_model_history(rows)
        assert any(
            isinstance(p, RetryPromptPart) for m in history for p in m.parts
        )


# =====================================================================
# history: prompt-cache prefix stability
# =====================================================================


def _padded_turn_rows(count: int, pad: int = 0) -> List[SimpleNamespace]:
    """`count` complete turns, each replaying a dump padded by `pad` chars."""
    rows: List[SimpleNamespace] = []
    for i in range(count):
        rows.append(_row("user", f"q{i}"))
        rows.append(
            _row(
                "assistant",
                f"a{i}-text",
                bucket=_bucketed(_simple_turn_dump(f"q{i}", f"a{i}" + "X" * pad)),
            )
        )
    return rows


def _turn_costs(rows: List[SimpleNamespace]) -> List[int]:
    """Replay cost per TURN (user row + assistant dump), derived from the
    rows themselves — the same arithmetic the loader's grid is built on."""
    costs = []
    for index in range(0, len(rows), 2):
        dump = rows[index + 1].bucket[BUCKET_DUMP_KEY]
        costs.append(len(rows[index].content) + len(json.dumps(dump)))
    return costs


def _wire_messages(history) -> List[str]:
    """The serialized messages — what a provider's prefix cache hashes."""
    return [
        json.dumps(message)
        for message in ModelMessagesTypeAdapter.dump_python(history, mode="json")
    ]


def _window_first_turn(history) -> int:
    """Index of the oldest turn REPLAYED from its dump (dumps carry a
    ThinkingPart-free ModelResponse whose text is the padded answer)."""
    for message in history:
        for part in message.parts:
            if isinstance(part, UserPromptPart) and str(part.content).startswith("q"):
                return int(str(part.content)[1:])
    raise AssertionError("history has no user prompt")


class TestReplayPrefixStability:
    """Provider prompt caches hash the leading BYTES of a request, so the
    only thing that keeps them warm across turns is a history whose prefix
    never gets rewritten. The old newest-first budget walk moved the
    boundary on EVERY turn; the grid moves it only in chunk-sized jumps."""

    def test_appending_a_turn_only_appends_messages(self):
        rows = _padded_turn_rows(12, pad=200)  # far under the window
        for turns in range(1, 12):
            earlier = _wire_messages(load_model_history(rows[: 2 * turns]))
            later = _wire_messages(load_model_history(rows[: 2 * (turns + 1)]))
            assert later[: len(earlier)] == earlier, (
                f"prefix rewritten when turn {turns + 1} was appended"
            )

    def test_window_start_jumps_by_a_chunk_then_holds(self):
        pad = 4_000
        replay_chunk = 30_000
        replay_chars = 90_000
        rows = _padded_turn_rows(40, pad=pad)
        costs = _turn_costs(rows)

        starts = []
        for turns in range(1, 41):
            history = load_model_history(
                rows[: 2 * turns],
                replay_chars=replay_chars,
                replay_chunk=replay_chunk,
                # Keep the tail out of the way: this is about the window.
                text_chars=0,
            )
            starts.append(_window_first_turn(history))

        assert starts == sorted(starts), "the window start must never move back"
        assert starts[0] == 0
        assert starts[-1] > 0, "the window never moved — test is not exercising it"

        # Every jump skips roughly a chunk's worth of cost. "Roughly": a grid
        # line is the first USER row past a chunk multiple, so two adjacent
        # lines can be up to one turn's overshoot closer than the chunk.
        jumps = 0
        for previous, current in zip(starts, starts[1:]):
            if current == previous:
                continue
            jumps += 1
            assert sum(costs[previous:current]) >= replay_chunk - max(costs), (
                f"window start moved {previous}->{current} for less than a chunk"
            )
        assert jumps >= 2, "expected several jumps over 40 turns"
        # ...so the number of invalidations is bounded by the thread's size
        # over the chunk, not by the number of turns.
        assert jumps <= sum(costs) // replay_chunk + 1

        # ...and between jumps the start holds for several turns.
        plateau = 1
        plateaus = []
        for previous, current in zip(starts, starts[1:]):
            if current == previous:
                plateau += 1
            else:
                plateaus.append(plateau)
                plateau = 1
        assert min(plateaus) >= 2, f"window start moved almost every turn: {starts}"

    def test_window_never_exceeds_its_high_water_mark(self):
        pad = 4_000
        rows = _padded_turn_rows(40, pad=pad)
        costs = _turn_costs(rows)
        for turns in range(1, 41):
            start = _window_first_turn(
                load_model_history(
                    rows[: 2 * turns],
                    replay_chars=90_000,
                    replay_chunk=30_000,
                    text_chars=0,
                )
            )
            if start == 0:
                continue  # nothing evicted yet; the whole thread is the window
            assert sum(costs[start:turns]) <= 90_000

    def test_evicted_turns_appear_as_bounded_text_ahead_of_the_window(self):
        rows = _padded_turn_rows(20, pad=4_000)
        history = load_model_history(
            rows,
            replay_chars=30_000,
            replay_chunk=10_000,
            text_chars=40,  # ~2 evicted turns' worth of `content` text
            text_chunk=1,
        )
        texts = [
            str(p.content)
            for m in history
            for p in m.parts
            if isinstance(p, (TextPart, UserPromptPart))
        ]
        # The tail is plain text from the `content` column, never a dump.
        assert any(t == "a0-text" for t in texts) is False, "oldest turns dropped"
        assert any(t.endswith("-text") for t in texts), "expected a text tail"
        tail_chars = sum(len(t) for t in texts if t.endswith("-text"))
        # `-text` rows only appear in the tail (replayed answers are padded).
        assert tail_chars <= 40 + len("aNN-text")

    def test_zero_text_cap_gives_whole_turn_eviction(self):
        rows = _padded_turn_rows(20, pad=4_000)
        history = load_model_history(
            rows, replay_chars=30_000, replay_chunk=10_000, text_chars=0
        )
        texts = [
            str(p.content)
            for m in history
            for p in m.parts
            if isinstance(p, (TextPart, UserPromptPart))
        ]
        assert not any(t.endswith("-text") for t in texts), (
            f"text_chars=0 must leave no text tail; got {texts[:4]}"
        )

    def test_history_opens_on_a_user_row(self):
        rows = _padded_turn_rows(20, pad=4_000)
        for text_chars in (0, 40, 120_000):
            history = load_model_history(
                rows,
                replay_chars=30_000,
                replay_chunk=10_000,
                text_chars=text_chars,
                text_chunk=1,
            )
            assert isinstance(history[0], ModelRequest)
            assert any(isinstance(p, UserPromptPart) for p in history[0].parts)

    def test_row_without_a_dump_inside_the_window_renders_as_text(self):
        rows = _padded_turn_rows(4, pad=100)
        # Wipe the dump on the second turn: it is well inside the window.
        rows[3].bucket = {"client_message_id": "c1"}
        history = load_model_history(rows, replay_chunk=1)
        texts = [
            str(p.content)
            for m in history
            for p in m.parts
            if isinstance(p, (TextPart, UserPromptPart))
        ]
        assert "a1-text" in texts
        assert "q1" in texts  # its question is still there, as text


class TestSandboxOutputsArePersistedWhole:
    """`run_python` returns used to be capped at 1500 chars before saving, so
    the replayed copy of a turn never matched what the model actually saw —
    a guaranteed prefix-cache miss. The sandbox already bounds its own output
    (24k chars per call, 240k per run), so the dump is persisted verbatim."""

    BIG_OUTPUT = "z" * 9_000

    def _sandbox_dump(self):
        return _dump(
            [
                ModelRequest(parts=[UserPromptPart(content="q")]),
                ModelResponse(
                    parts=[
                        ToolCallPart(
                            tool_name="run_python",
                            args={"code": "print(1)"},
                            tool_call_id="call_1",
                        )
                    ]
                ),
                ModelRequest(
                    parts=[
                        ToolReturnPart(
                            tool_name="run_python",
                            content={"files": ["a.py"], "output": self.BIG_OUTPUT},
                            tool_call_id="call_1",
                        )
                    ]
                ),
                ModelResponse(parts=[TextPart(content="done")]),
            ]
        )

    def test_persist_helpers_leave_the_output_intact(self):
        persisted = strip_figure_bytes(strip_instructions(self._sandbox_dump()))
        outputs = [
            part["content"]["output"]
            for entry in persisted
            for part in entry["parts"]
            if part.get("part_kind") == "tool-return"
        ]
        assert outputs == [self.BIG_OUTPUT]

    def test_replay_returns_the_full_output(self):
        rows = [
            _row("user", "q"),
            _row("assistant", "done", bucket=_bucketed(self._sandbox_dump())),
        ]
        history = load_model_history(rows)
        outputs = [
            p.content["output"]
            for m in history
            for p in m.parts
            if isinstance(p, ToolReturnPart)
        ]
        assert outputs == [self.BIG_OUTPUT]

    def test_the_trimmer_is_gone(self):
        from app.llm.chat import history as history_module

        assert not hasattr(history_module, "strip_sandbox_outputs")


# =====================================================================
# history: vision-aware replay
# =====================================================================


def _spec(*, supports_vision: bool = True, api: str = "responses") -> SimpleNamespace:
    """Minimal stand-in for the ModelSpec the runtime passes to replay."""
    return SimpleNamespace(
        id="test-model", supports_vision=supports_vision, api=api
    )


class TestVisionAwareReplay:
    """A figure fetched by a vision-capable model must never be replayed as
    image content to a model that rejects images.

    Verified live: DeepSeek V4 Flash / Kimi K3 400 with "does not support
    image inputs" on ANY image in the request, so one figure fetched by a
    gpt-5.5 turn used to kill every later DeepSeek turn in that conversation
    — including plain-text ones — until the turn aged out of the budget.
    """

    IMAGE_BYTES = b"\x89PNG-figure-bytes"
    S3_KEY = "figures/paper-1/figure-1.png"

    def _figure_dump(self):
        from pydantic_ai.messages import BinaryImage

        from app.llm.chat.history import FIGURE_ID_PREFIX, strip_figure_bytes

        dump = _dump(
            [
                ModelResponse(
                    parts=[
                        ToolCallPart(
                            tool_name="get_figure",
                            args={"label": "Figure 1"},
                            tool_call_id="c1",
                        )
                    ]
                ),
                ModelRequest(
                    parts=[
                        ToolReturnPart(
                            tool_name="get_figure",
                            content={"label": "Figure 1", "page": 3},
                            tool_call_id="c1",
                        ),
                        UserPromptPart(
                            content=[
                                BinaryImage(
                                    data=self.IMAGE_BYTES,
                                    media_type="image/png",
                                    identifier=f"{FIGURE_ID_PREFIX}{self.S3_KEY}",
                                )
                            ]
                        ),
                    ]
                ),
                ModelResponse(parts=[TextPart(content="the figure shows X")]),
            ]
        )
        # Persistence blanks the bytes; only the S3 key survives on the row.
        return strip_figure_bytes(dump)

    def _rows(self):
        return [
            _row("user", "what does figure 1 show?"),
            _row(
                "assistant",
                "the figure shows X",
                bucket=_bucketed(self._figure_dump()),
            ),
        ]

    def _patch_s3(self, monkeypatch, fetched: List[str]):
        from app.llm.chat import history as history_module

        def fake_get(key: str) -> bytes:
            fetched.append(key)
            return self.IMAGE_BYTES

        monkeypatch.setattr(
            history_module.s3_service, "get_object_bytes", fake_get
        )

    def _binary_parts(self, history) -> List[Any]:
        from pydantic_ai.messages import BinaryContent

        found = []
        for message in history:
            for part in message.parts:
                content = getattr(part, "content", None)
                items = content if isinstance(content, list) else [content]
                found.extend(i for i in items if isinstance(i, BinaryContent))
        return found

    def test_vision_model_still_rehydrates_the_figure(self, monkeypatch):
        fetched: List[str] = []
        self._patch_s3(monkeypatch, fetched)
        history = load_model_history(self._rows(), spec=_spec(supports_vision=True))
        images = self._binary_parts(history)
        assert len(images) == 1
        assert images[0].data == self.IMAGE_BYTES
        assert fetched == [self.S3_KEY]

    def test_default_is_still_rehydration(self, monkeypatch):
        """Callers that don't know the model keep the old behavior."""
        fetched: List[str] = []
        self._patch_s3(monkeypatch, fetched)
        assert self._binary_parts(load_model_history(self._rows()))
        assert fetched == [self.S3_KEY]

    def test_non_vision_model_gets_a_placeholder_instead(self, monkeypatch):
        fetched: List[str] = []
        self._patch_s3(monkeypatch, fetched)
        history = load_model_history(self._rows(), spec=_spec(supports_vision=False))

        assert self._binary_parts(history) == []
        # Not even an S3 round-trip: the bytes would only be discarded.
        assert fetched == []

        prompts = [
            part
            for message in history
            for part in message.parts
            if isinstance(part, UserPromptPart)
            and isinstance(part.content, list)
        ]
        assert prompts, "the figure's user-prompt part disappeared"
        assert prompts[0].content == [IMAGE_PLACEHOLDER]

    def test_non_vision_replay_keeps_the_tool_return_metadata(self, monkeypatch):
        """The model must still know a figure was fetched, and which one."""
        self._patch_s3(monkeypatch, [])
        history = load_model_history(self._rows(), spec=_spec(supports_vision=False))
        returns = [
            part
            for message in history
            for part in message.parts
            if isinstance(part, ToolReturnPart)
        ]
        assert [r.tool_name for r in returns] == ["get_figure"]
        assert returns[0].content == {"label": "Figure 1", "page": 3}

    def test_non_vision_replay_round_trips_and_keeps_the_turn(self, monkeypatch):
        """The stripped dump must still validate — a broken one would fall
        back to text and silently drop the tool call."""
        self._patch_s3(monkeypatch, [])
        history = load_model_history(self._rows(), spec=_spec(supports_vision=False))
        assert [type(m).__name__ for m in history] == [
            "ModelRequest",
            "ModelResponse",
            "ModelRequest",
            "ModelResponse",
        ]


class TestResponsesReasoningIdsOnChatModels:
    """A gpt-5.x turn persists `ThinkingPart(id='rs_…', provider_name=
    'openai')`. Replayed to a Chat Completions model, pydantic-ai's
    `_map_response_thinking_part` takes its "field" branch and emits the id
    as a TOP-LEVEL assistant-message key; Fireworks-hosted deployments 400
    with "Extra inputs are not permitted, field: messages[2].rs_00…". One
    gpt-5.x tool turn used to poison every later DeepSeek/Kimi turn."""

    def _thinking_dump(self, thinking_id: str):
        return _dump(
            [
                ModelResponse(
                    parts=[
                        ThinkingPart(
                            content="let me think",
                            id=thinking_id,
                            provider_name="openai",
                        ),
                        TextPart(content="the answer"),
                    ]
                )
            ]
        )

    def _rows(self, thinking_id: str = "rs_0011223344"):
        return [
            _row("user", "q"),
            _row(
                "assistant",
                "the answer",
                bucket=_bucketed(self._thinking_dump(thinking_id)),
            ),
        ]

    def _thinking_parts(self, history):
        return [
            part
            for message in history
            for part in message.parts
            if isinstance(part, ThinkingPart)
        ]

    def test_chat_api_clears_responses_reasoning_ids(self):
        history = load_model_history(self._rows(), spec=_spec(api="chat"))
        parts = self._thinking_parts(history)
        assert len(parts) == 1
        assert parts[0].id is None
        # Content is preserved — it just goes back as tagged text.
        assert parts[0].content == "let me think"

    def test_responses_api_leaves_them_alone(self):
        history = load_model_history(self._rows(), spec=_spec(api="responses"))
        assert self._thinking_parts(history)[0].id == "rs_0011223344"

    def test_no_spec_leaves_them_alone(self):
        history = load_model_history(self._rows())
        assert self._thinking_parts(history)[0].id == "rs_0011223344"

    def test_chat_native_thinking_ids_are_kept(self):
        """`reasoning_content` is the Chat Completions field name — it is
        exactly what the field branch is FOR, and Kimi turns replay fine."""
        history = load_model_history(
            self._rows(thinking_id="reasoning_content"), spec=_spec(api="chat")
        )
        assert self._thinking_parts(history)[0].id == "reasoning_content"

    def test_stored_row_is_not_mutated(self):
        rows = self._rows()
        load_model_history(rows, spec=_spec(api="chat"))
        stored = json.dumps(rows[1].bucket)
        assert "rs_0011223344" in stored

    def test_chat_completions_mapping_emits_no_bogus_field(self):
        """The actual failure, reproduced against pydantic-ai's mapper."""
        import asyncio

        from pydantic_ai.models import ModelRequestParameters
        from pydantic_ai.models.openai import OpenAIChatModel
        from pydantic_ai.providers.openai import OpenAIProvider

        model = OpenAIChatModel(
            "FW-DeepSeek-V4-Flash-0731",
            provider=OpenAIProvider(api_key="k", base_url="https://x.invalid/v1"),
        )

        def mapped(spec) -> str:
            history = load_model_history(self._rows(), spec=spec)
            messages = asyncio.run(
                model._map_messages(history, ModelRequestParameters())
            )
            return json.dumps(messages)

        assert "rs_0011223344" in mapped(_spec(api="responses")), (
            "expected the unsanitized dump to reproduce the bogus field"
        )
        assert "rs_0011223344" not in mapped(_spec(api="chat"))


class TestStripReplayedImages:
    def test_figure_binary_is_replaced_in_place(self):
        dump = [{"content": [{"kind": "binary", "media_type": "image/png", "data": "x"}]}]
        assert strip_replayed_images(dump) == [{"content": [IMAGE_PLACEHOLDER]}]

    def test_image_url_is_replaced(self):
        dump = [{"content": [{"kind": "image-url", "url": "https://x/y.png"}]}]
        assert strip_replayed_images(dump) == [{"content": [IMAGE_PLACEHOLDER]}]

    def test_figure_identifier_without_media_type(self):
        from app.llm.chat.history import FIGURE_ID_PREFIX

        dump = [{"content": [{"kind": "binary", "identifier": f"{FIGURE_ID_PREFIX}k"}]}]
        assert strip_replayed_images(dump) == [{"content": [IMAGE_PLACEHOLDER]}]

    def test_non_image_binary_is_left_alone(self):
        node = {"kind": "binary", "media_type": "application/pdf", "data": "x"}
        dump = [{"content": [node]}]
        assert strip_replayed_images(dump) == [{"content": [node]}]

    def test_text_and_structure_are_untouched(self):
        dump = [{"parts": [{"part_kind": "text", "content": "hello"}]}]
        assert strip_replayed_images(copy.deepcopy(dump)) == dump

    def test_image_as_a_dict_value(self):
        dump = {"content": {"kind": "binary", "media_type": "image/jpeg", "data": "x"}}
        assert strip_replayed_images(dump) == {"content": IMAGE_PLACEHOLDER}

    def test_image_uploaded_file_is_replaced(self):
        node = {
            "kind": "uploaded-file",
            "file_id": "file-123",
            "provider_name": "openai",
            "media_type": "image/png",
        }
        dump = [{"parts": [{"part_kind": "user-prompt", "content": [node]}]}]
        assert strip_replayed_images(dump)[0]["parts"][0]["content"] == [
            IMAGE_PLACEHOLDER
        ]

    def test_non_image_uploaded_file_is_kept(self):
        node = {
            "kind": "uploaded-file",
            "file_id": "file-123",
            "provider_name": "openai",
            "media_type": "application/pdf",
        }
        dump = [{"parts": [{"part_kind": "user-prompt", "content": [node]}]}]
        assert strip_replayed_images(dump)[0]["parts"][0]["content"] == [node]


class TestStripReplayedImagesRoundTrips:
    """Whatever comes out must still validate: `load_model_history` catches
    a validation error and silently degrades the WHOLE turn to plain text,
    losing its tool calls."""

    def _round_trip(self, messages) -> List[Any]:
        dump = strip_replayed_images(_dump(messages))
        return ModelMessagesTypeAdapter.validate_json(json.dumps(dump))

    def test_user_prompt_image(self):
        from pydantic_ai.messages import BinaryImage

        out = self._round_trip(
            [
                ModelRequest(
                    parts=[
                        UserPromptPart(
                            content=[
                                "look:",
                                BinaryImage(data=b"\x89PNG", media_type="image/png"),
                            ]
                        )
                    ]
                )
            ]
        )
        assert out[0].parts[0].content == ["look:", IMAGE_PLACEHOLDER]

    def test_response_file_part_becomes_text(self):
        """`FilePart.content` is a required file object — substituting a
        string there fails validation, so the part itself must change."""
        from pydantic_ai.messages import BinaryImage, FilePart

        out = self._round_trip(
            [
                ModelResponse(
                    parts=[
                        FilePart(
                            content=BinaryImage(
                                data=b"\x89PNG", media_type="image/png"
                            )
                        ),
                        TextPart(content="here is the chart"),
                    ]
                )
            ]
        )
        kinds = [type(p).__name__ for p in out[0].parts]
        assert kinds == ["TextPart", "TextPart"]
        assert out[0].parts[0].content == IMAGE_PLACEHOLDER
        assert out[0].parts[1].content == "here is the chart"

    def test_response_file_part_with_a_document_is_untouched(self):
        from pydantic_ai.messages import BinaryContent, FilePart

        out = self._round_trip(
            [
                ModelResponse(
                    parts=[
                        FilePart(
                            content=BinaryContent(
                                data=b"%PDF", media_type="application/pdf"
                            )
                        )
                    ]
                )
            ]
        )
        assert type(out[0].parts[0]).__name__ == "FilePart"


class TestFigureBudgetAccounting:
    """Cost is computed on byte-STRIPPED dumps (`data: ""`), so rehydration
    used to re-inflate the history past the replay window after the fact —
    and the provider rejected it on context length."""

    def _figure_rows(self):
        from pydantic_ai.messages import BinaryImage

        from app.llm.chat.history import FIGURE_ID_PREFIX, strip_figure_bytes

        dump = strip_figure_bytes(
            _dump(
                [
                    ModelRequest(
                        parts=[
                            UserPromptPart(
                                content=[
                                    BinaryImage(
                                        data=b"x",
                                        media_type="image/png",
                                        identifier=f"{FIGURE_ID_PREFIX}fig/1.png",
                                    )
                                ]
                            )
                        ]
                    ),
                    ModelResponse(parts=[TextPart(content="a1")]),
                ]
            )
        )
        return [_row("user", "q1"), _row("assistant", "a1-text", bucket=_bucketed(dump))]

    def _patch_sizes(self, monkeypatch, size_kb):
        from app.llm.chat import history as history_module

        monkeypatch.setattr(
            history_module.s3_service,
            "get_file_size_in_kb",
            lambda key: size_kb,
        )
        monkeypatch.setattr(
            history_module.s3_service, "get_object_bytes", lambda key: b"PNGDATA"
        )

    def test_oversized_figure_degrades_the_turn_to_text(self, monkeypatch):
        self._patch_sizes(monkeypatch, 500)  # ~683k chars of base64
        rows = self._figure_rows()
        history = load_model_history(rows, spec=_spec())
        texts = [
            p.content
            for m in history
            for p in m.parts
            if isinstance(p, (TextPart, UserPromptPart))
        ]
        assert "a1-text" in texts, "expected the turn to degrade to its DB text"

    def test_small_figure_still_replays(self, monkeypatch):
        self._patch_sizes(monkeypatch, 8)
        history = load_model_history(self._figure_rows(), spec=_spec())
        assert any(
            isinstance(p, UserPromptPart) and isinstance(p.content, list)
            for m in history
            for p in m.parts
        )

    def test_unknown_size_uses_the_fallback_estimate(self, monkeypatch):
        from app.llm.chat import history as history_module

        self._patch_sizes(monkeypatch, None)
        rows = self._figure_rows()
        # The fallback alone must not exceed the window...
        assert load_model_history(rows, spec=_spec(), replay_chars=200_000)
        # ...but it IS charged.
        history = load_model_history(
            rows,
            spec=_spec(),
            replay_chars=history_module.FIGURE_REPLAY_CHAR_ESTIMATE // 2,
        )
        assert all(
            not (isinstance(p, UserPromptPart) and isinstance(p.content, list))
            for m in history
            for p in m.parts
        )

    def test_no_size_lookup_for_a_vision_less_model(self, monkeypatch):
        from app.llm.chat import history as history_module

        looked_up = []
        monkeypatch.setattr(
            history_module.s3_service,
            "get_file_size_in_kb",
            lambda key: looked_up.append(key) or 500,
        )
        load_model_history(
            self._figure_rows(), spec=_spec(supports_vision=False)
        )
        assert looked_up == []


# =====================================================================
# history: serialize_ui_messages
# =====================================================================


def _parts_by_type(ui: Dict[str, Any]) -> List[str]:
    return [p["type"] for p in ui["parts"]]


class TestSerializeUIMessages:
    def test_user_row(self):
        rid = str(uuid.uuid4())
        out = serialize_ui_messages([_row("user", "hi there", row_id=rid)])
        assert len(out) == 1
        assert out[0]["id"] == rid
        assert out[0]["role"] == "user"
        assert out[0]["parts"] == [{"type": "text", "text": "hi there", "state": "done"}]
        assert "bucket" not in out[0]

    def test_user_row_with_references(self):
        out = serialize_ui_messages(
            [_row("user", "hi", references={"citations": [{"key": 1, "reference": "x"}]})]
        )
        assert _parts_by_type(out[0]) == ["text", "data-citations"]
        assert out[0]["parts"][1]["id"] == "citations"

    def test_legacy_assistant_text_only_row(self):
        out = serialize_ui_messages(
            [
                _row(
                    "assistant",
                    f"answer{EVIDENCE_START}hidden{EVIDENCE_END}",
                    references={"citations": [{"key": 1, "reference": "q", "page": 2}]},
                )
            ]
        )
        assert _parts_by_type(out[0]) == ["text", "data-citations"]
        assert out[0]["parts"][0]["text"] == "answer"
        assert out[0]["parts"][1]["data"]["citations"][0]["page"] == 2

    def test_assistant_dump_produces_step_tool_reasoning_and_text(self):
        dump = _tool_turn_dump("q1", f"final answer{EVIDENCE_START}x{EVIDENCE_END}")
        out = serialize_ui_messages(
            [
                _row("user", "q1"),
                _row(
                    "assistant",
                    "final answer",
                    bucket=_bucketed(dump),
                    references={"citations": [{"key": 1, "reference": "q"}]},
                ),
            ]
        )
        assistant = out[1]
        types = _parts_by_type(assistant)
        assert types[0] == "step-start"
        assert "reasoning" in types
        assert any(t.startswith("tool-") for t in types)
        assert "text" in types
        assert types[-1] == "data-citations"
        # step-start per ModelResponse
        assert types.count("step-start") == 2
        # Evidence stripped from the dump's text part
        text_parts = [p for p in assistant["parts"] if p["type"] == "text"]
        assert text_parts[0]["text"] == "final answer"
        assert all(EVIDENCE_START not in p.get("text", "") for p in assistant["parts"])

    def test_tool_part_carries_input_and_output(self):
        dump = _tool_turn_dump("q", "a")
        out = serialize_ui_messages([_row("assistant", "a", bucket=_bucketed(dump))])
        tool_parts = [p for p in out[0]["parts"] if p["type"].startswith("tool-")]
        assert tool_parts, "expected a tool part"
        tp = tool_parts[0]
        assert tp["type"] == "tool-read_section"
        assert tp["input"] == {"name": "Methods"}
        assert tp["state"] == "output-available"
        assert tp["output"] == {"text": "M" * 20}

    def test_large_tool_output_is_truncated(self):
        big = "Z" * (TOOL_OUTPUT_WIRE_CAP + 4000)
        dump = _dump(
            [
                ModelRequest(parts=[UserPromptPart(content="q")]),
                ModelResponse(
                    parts=[
                        ToolCallPart(
                            tool_name="read_section",
                            args={"name": "Methods"},
                            tool_call_id="c1",
                        )
                    ]
                ),
                ModelRequest(
                    parts=[
                        ToolReturnPart(
                            tool_name="read_section",
                            content={"text": big},
                            tool_call_id="c1",
                        )
                    ]
                ),
                ModelResponse(parts=[TextPart(content="a")]),
            ]
        )
        out = serialize_ui_messages([_row("assistant", "a", bucket=_bucketed(dump))])
        tp = [p for p in out[0]["parts"] if p["type"].startswith("tool-")][0]
        assert tp["output"]["truncated"] is True
        preview = tp["output"]["preview"]
        assert preview["text"].startswith("ZZZ")
        assert preview["text"].endswith(" chars]")
        assert len(json.dumps(preview)) <= TOOL_OUTPUT_WIRE_CAP

    def test_dump_user_message_is_dropped(self):
        dump = _simple_turn_dump("the user prompt", "a")
        out = serialize_ui_messages([_row("assistant", "a", bucket=_bucketed(dump))])
        assert all(
            "the user prompt" not in json.dumps(p) for p in out[0]["parts"]
        )

    def test_corrupt_dump_falls_back_to_text(self):
        out = serialize_ui_messages(
            [_row("assistant", "plain text", bucket={BUCKET_DUMP_KEY: [{"nope": 1}]})]
        )
        assert _parts_by_type(out[0]) == ["text"]
        assert out[0]["parts"][0]["text"] == "plain text"

    def test_assistant_row_with_no_content_is_dropped(self):
        out = serialize_ui_messages([_row("assistant", "")])
        assert out == []

    def test_evidence_only_assistant_row_keeps_citations(self):
        out = serialize_ui_messages(
            [
                _row(
                    "assistant",
                    f"{EVIDENCE_START}only evidence{EVIDENCE_END}",
                    references={"citations": [{"key": 1, "reference": "q"}]},
                )
            ]
        )
        assert _parts_by_type(out[0]) == ["data-citations"]

    def test_ids_match_db_rows_and_bucket_never_leaks(self):
        uid, aid = str(uuid.uuid4()), str(uuid.uuid4())
        rows = [
            _row("user", "q", row_id=uid, bucket={"client_message_id": "secret"}),
            _row(
                "assistant",
                "a",
                row_id=aid,
                bucket=_bucketed(_tool_turn_dump("q", "a"), client_id="secret"),
            ),
        ]
        out = serialize_ui_messages(rows)
        assert [m["id"] for m in out] == [uid, aid]
        blob = json.dumps(out)
        assert "bucket" not in blob
        assert "client_message_id" not in blob
        assert "secret" not in blob

    def test_unknown_role_skipped(self):
        assert serialize_ui_messages([_row("system", "sys")]) == []

    def test_completed_row_has_no_metadata(self):
        out = serialize_ui_messages([_row("assistant", "done")])
        assert "metadata" not in out[0]

    def test_interrupted_row_carries_metadata(self):
        out = serialize_ui_messages(
            [_row("assistant", "partial", bucket={"interrupted": True})]
        )
        assert out[0]["metadata"] == {"interrupted": True}
        assert out[0]["parts"][0]["text"] == "partial"

    def test_failed_empty_row_survives_with_error_text(self):
        """A run that died before emitting text still has to reach the UI —
        its metadata is what renders the failure and offers the retry."""
        out = serialize_ui_messages(
            [
                _row(
                    "assistant",
                    "",
                    bucket={
                        "interrupted": True,
                        "error": {"message": "ModelHTTPError (500): upstream"},
                    },
                )
            ]
        )
        assert len(out) == 1
        assert out[0]["parts"] == []
        assert out[0]["metadata"] == {
            "interrupted": True,
            "errorText": "ModelHTTPError (500): upstream",
        }

    def test_stop_and_failure_are_distinguishable(self):
        """Wire contract with the client: `errorText` is present IFF the run
        actually failed. A user Stop must render as a neutral interruption
        with no retry affordance, so nothing may fabricate an error string."""
        stopped, failed = serialize_ui_messages(
            [
                _row("assistant", "half an answer", bucket={"interrupted": True}),
                _row(
                    "assistant",
                    "",
                    bucket={
                        "interrupted": True,
                        "error": {"message": "ModelHTTPError (500): upstream"},
                    },
                ),
            ]
        )
        assert stopped["metadata"] == {"interrupted": True}
        assert "errorText" not in stopped["metadata"]
        assert failed["metadata"]["errorText"] == "ModelHTTPError (500): upstream"

    def test_error_bucket_without_message_yields_interrupted_only(self):
        out = serialize_ui_messages(
            [_row("assistant", "x", bucket={"interrupted": True, "error": {}})]
        )
        assert out[0]["metadata"] == {"interrupted": True}


# =====================================================================
# runtime: resubmission / trailing-row reuse
# =====================================================================


class TestPlanTrailingReuse:
    """`_plan_trailing_reuse` decides whether a resubmitted question reuses
    the previous attempt's rows (and deletes its failed partial) or starts a
    fresh turn."""

    QUESTION = "why is the sky blue?"

    def _plan(self, rows, question=None):
        from app.llm.chat.runtime import _plan_trailing_reuse

        return _plan_trailing_reuse(rows, question or self.QUESTION)

    def test_empty_history(self):
        assert self._plan([]) == ([], None, None)

    def test_matching_dangling_user_row_is_reused(self):
        user = _row("user", self.QUESTION)
        history, reused, stale = self._plan([user])
        assert history == []
        assert reused is user
        assert stale is None

    def test_different_question_starts_a_fresh_turn(self):
        user = _row("user", "a different question")
        history, reused, stale = self._plan([user])
        assert history == [user]
        assert (reused, stale) == (None, None)

    def test_failed_pair_is_reused_and_marked_for_deletion(self):
        user = _row("user", self.QUESTION)
        failed = _row("assistant", "", bucket={"interrupted": True, "error": {}})
        history, reused, stale = self._plan([user, failed])
        assert history == []
        assert reused is user
        assert stale is failed

    def test_failed_pair_with_a_different_question_is_left_alone(self):
        user = _row("user", "an older question")
        failed = _row("assistant", "", bucket={"interrupted": True, "error": {}})
        rows = [user, failed]
        assert self._plan(rows) == (rows, None, None)

    def test_completed_turn_is_never_reused(self):
        rows = [_row("user", self.QUESTION), _row("assistant", "an answer")]
        assert self._plan(rows) == (rows, None, None)

    def test_earlier_turns_are_preserved(self):
        old_user = _row("user", "older")
        old_assistant = _row("assistant", "older answer")
        user = _row("user", self.QUESTION)
        failed = _row(
            "assistant",
            "partial",
            bucket={"interrupted": True, "error": {"message": "boom"}},
        )
        history, reused, stale = self._plan(
            [old_user, old_assistant, user, failed]
        )
        assert history == [old_user, old_assistant]
        assert (reused, stale) == (user, failed)

    def test_stopped_turn_with_text_is_never_deleted(self):
        """An interrupted row WITHOUT an error is a user stop (or a
        disconnect after the answer completed) — deleting it would destroy a
        real answer, so the resubmission starts a fresh turn instead."""
        user = _row("user", self.QUESTION)
        stopped = _row("assistant", "a real, complete answer", bucket={"interrupted": True})
        rows = [user, stopped]
        assert self._plan(rows) == (rows, None, None)

    def test_empty_interrupted_row_is_replaceable(self):
        user = _row("user", self.QUESTION)
        empty = _row("assistant", "", bucket={"interrupted": True})
        history, reused, stale = self._plan([user, empty])
        assert (history, reused, stale) == ([], user, empty)

    def test_leading_interrupted_row_without_a_question(self):
        failed = _row("assistant", "", bucket={"interrupted": True})
        assert self._plan([failed]) == ([failed], None, None)


# =====================================================================
# model_registry
# =====================================================================


def _mk_registry(
    specs: List[ModelSpec],
    configs: Optional[Dict[LLMProvider, Any]] = None,
    default_provider: LLMProvider = LLMProvider.OPENAI,
) -> ModelRegistry:
    return ModelRegistry(specs, configs or {}, default_provider)


class TestFamilyDefaults:
    def test_openai_gpt_gets_effort_and_summaries(self):
        d = _family_defaults(LLMProvider.OPENAI, "gpt-5.4-mini")
        assert d == {
            "api": "responses",
            "supports_reasoning_effort": True,
            "supports_reasoning_summaries": True,
            "supports_prompt_cache_key": True,
        }

    def test_openai_non_gpt_no_effort(self):
        d = _family_defaults(LLMProvider.OPENAI, "DeepSeek-V4-Flash-0731")
        assert d["api"] == "responses"
        assert d["supports_reasoning_effort"] is False
        assert d["supports_reasoning_summaries"] is False

    def test_codex_proxy_is_chat_without_summaries(self):
        d = _family_defaults(LLMProvider.CODEX_PROXY, "gpt-5.5")
        assert d == {
            "api": "chat",
            "supports_reasoning_effort": True,
            "supports_reasoning_summaries": False,
            "supports_prompt_cache_key": True,
        }

    @pytest.mark.parametrize(
        "provider", [LLMProvider.GROQ, LLMProvider.CEREBRAS]
    )
    def test_chat_only_providers(self, provider):
        assert _family_defaults(provider, "whatever") == {"api": "chat"}

    @pytest.mark.parametrize(
        "provider", [LLMProvider.ANTHROPIC, LLMProvider.GEMINI]
    )
    def test_native_providers(self, provider):
        assert _family_defaults(provider, "claude-sonnet-5") == {"api": "native"}


class TestKnownCapsAndOverrides:
    def test_kimi_forced_to_chat_completions(self):
        spec = _build_spec("FW-Kimi-K3", "Kimi K3", LLMProvider.OPENAI, {})
        assert spec.api == "chat"
        assert spec.supports_vision is True

    def test_deepseek_caps(self):
        spec = _build_spec(
            "DeepSeek-V4-Flash-0731", "DeepSeek", LLMProvider.OPENAI, {}
        )
        assert spec.supports_vision is False
        assert spec.supports_reasoning_effort is False
        assert spec.api == "responses"

    def test_fw_deepseek_caps(self):
        spec = _build_spec(
            "FW-DeepSeek-V4-Flash-0731", "DeepSeek", LLMProvider.OPENAI, {}
        )
        assert spec.supports_vision is False
        assert spec.supports_reasoning_effort is False
        assert spec.api == "chat"

    def test_gemma_caps(self):
        spec = _build_spec(
            "gemma-4-31b-it", "Gemma 4 31B", LLMProvider.GEMINI, {}
        )
        assert spec.supports_vision is True
        assert spec.api == "native"

    def test_env_override_beats_known_caps(self):
        overrides = {"DeepSeek-V4-Flash-0731": {"supports_vision": True}}
        spec = _build_spec(
            "DeepSeek-V4-Flash-0731", "DeepSeek", LLMProvider.OPENAI, overrides
        )
        assert spec.supports_vision is True

    def test_model_overrides_env_parsed(self, monkeypatch):
        from app.llm import model_registry as mr

        monkeypatch.setenv(
            "MODEL_OVERRIDES", json.dumps({"my-model": {"api": "chat"}})
        )
        assert mr._env_overrides() == {"my-model": {"api": "chat"}}

    def test_model_overrides_invalid_json_ignored(self, monkeypatch, caplog):
        from app.llm import model_registry as mr

        monkeypatch.setenv("MODEL_OVERRIDES", "{not json")
        assert mr._env_overrides() == {}

    def test_model_overrides_non_object_ignored(self, monkeypatch):
        from app.llm import model_registry as mr

        monkeypatch.setenv("MODEL_OVERRIDES", "[1,2,3]")
        assert mr._env_overrides() == {}

    def test_model_overrides_unknown_fields_dropped(self, monkeypatch):
        from app.llm import model_registry as mr

        monkeypatch.setenv(
            "MODEL_OVERRIDES",
            json.dumps({"m": {"api": "chat", "bogus_field": 1}}),
        )
        assert mr._env_overrides() == {"m": {"api": "chat"}}

    def test_model_overrides_entry_not_object_skipped(self, monkeypatch):
        from app.llm import model_registry as mr

        monkeypatch.setenv("MODEL_OVERRIDES", json.dumps({"m": "chat"}))
        assert mr._env_overrides() == {}

    def test_model_overrides_unset(self, monkeypatch):
        from app.llm import model_registry as mr

        monkeypatch.delenv("MODEL_OVERRIDES", raising=False)
        assert mr._env_overrides() == {}


class TestResolve:
    def setup_method(self):
        self.openai_spec = ModelSpec(
            id="shared-id", provider=LLMProvider.OPENAI, display_name="OpenAI shared"
        )
        self.proxy_spec = ModelSpec(
            id="shared-id",
            provider=LLMProvider.CODEX_PROXY,
            display_name="Proxy shared",
            api="chat",
        )
        self.other = ModelSpec(
            id="only-openai", provider=LLMProvider.OPENAI, display_name="Only"
        )

    def test_provider_plus_id_disambiguates(self):
        reg = _mk_registry([self.openai_spec, self.proxy_spec, self.other])
        assert (
            reg.resolve(LLMProvider.CODEX_PROXY, "shared-id").display_name
            == "Proxy shared"
        )
        assert (
            reg.resolve(LLMProvider.OPENAI, "shared-id").display_name
            == "OpenAI shared"
        )

    def test_id_only_takes_first_in_registry_order(self):
        reg = _mk_registry([self.proxy_spec, self.openai_spec])
        assert reg.resolve(None, "shared-id").provider == LLMProvider.CODEX_PROXY

    def test_unknown_id_raises(self):
        reg = _mk_registry([self.other])
        with pytest.raises(ValueError, match="not found in any provider"):
            reg.resolve(None, "nope")

    def test_id_under_wrong_provider_raises(self):
        reg = _mk_registry([self.other])
        with pytest.raises(ValueError, match="not available under provider"):
            reg.resolve(LLMProvider.CODEX_PROXY, "only-openai")

    def test_default_role_lookup(self):
        from app.llm.model_registry import _ProviderConfig

        cfg = _ProviderConfig(
            api_key="k", base_url=None, default_model="d", fast_model="f"
        )
        specs = [
            ModelSpec(id="d", provider=LLMProvider.OPENAI, display_name="Default"),
            ModelSpec(id="f", provider=LLMProvider.OPENAI, display_name="Fast"),
        ]
        reg = _mk_registry(specs, {LLMProvider.OPENAI: cfg})
        assert reg.resolve().id == "d"
        assert reg.resolve(role=ModelRole.FAST).id == "f"

    def test_role_model_missing_from_list_is_derived(self):
        from app.llm.model_registry import _ProviderConfig

        cfg = _ProviderConfig(
            api_key="k", base_url=None, default_model="gpt-9-unlisted", fast_model="f"
        )
        reg = _mk_registry([], {LLMProvider.OPENAI: cfg})
        spec = reg.resolve()
        assert spec.id == "gpt-9-unlisted"
        assert spec.supports_reasoning_effort is True

    def test_unconfigured_provider_raises(self):
        reg = _mk_registry([])
        with pytest.raises(ValueError, match="is not configured"):
            reg.resolve()

    def test_chat_models_excludes_providers(self):
        reg = _mk_registry(
            [
                self.other,
                ModelSpec(
                    id="g", provider=LLMProvider.GROQ, display_name="G", api="chat"
                ),
            ]
        )
        ids = [s.id for s in reg.chat_models(exclude=[LLMProvider.GROQ])]
        assert ids == ["only-openai"]


class TestBuildSettings:
    def setup_method(self):
        self.reg = _mk_registry([])
        self.responses_gpt = ModelSpec(
            id="gpt-5.4-mini",
            provider=LLMProvider.OPENAI,
            display_name="mini",
            api="responses",
            supports_reasoning_effort=True,
            supports_reasoning_summaries=True,
        )
        self.chat_gpt = ModelSpec(
            id="gpt-5.5",
            provider=LLMProvider.CODEX_PROXY,
            display_name="proxy",
            api="chat",
            supports_reasoning_effort=True,
            supports_reasoning_summaries=False,
        )
        self.no_effort = ModelSpec(
            id="DeepSeek-V4-Flash-0731",
            provider=LLMProvider.OPENAI,
            display_name="ds",
            supports_reasoning_effort=False,
        )

    def test_none_when_no_effort_requested(self):
        assert self.reg.build_settings(self.responses_gpt, None) is None
        assert self.reg.build_settings(self.responses_gpt, "") is None

    def test_none_when_model_does_not_support_effort(self):
        assert self.reg.build_settings(self.no_effort, "high") is None

    def test_responses_model_gets_effort_and_summary(self):
        s = self.reg.build_settings(self.responses_gpt, "low")
        assert s["openai_reasoning_effort"] == "low"
        assert s["openai_reasoning_summary"] == "auto"

    def test_no_summary_when_unsupported(self):
        spec = ModelSpec(
            id="x",
            provider=LLMProvider.OPENAI,
            display_name="x",
            api="responses",
            supports_reasoning_effort=True,
            supports_reasoning_summaries=False,
        )
        s = self.reg.build_settings(spec, "medium")
        assert "openai_reasoning_summary" not in s

    def test_chat_model_gets_effort_without_summary(self):
        s = self.reg.build_settings(self.chat_gpt, "high")
        assert s["openai_reasoning_effort"] == "high"
        assert "openai_reasoning_summary" not in s

    def test_xhigh_is_downgraded_for_chat_completions(self):
        """Design doc revision #15: `xhigh` -> `high` mapping for chat
        completions (the Chat Completions API rejects `xhigh`)."""
        s = self.reg.build_settings(self.chat_gpt, "xhigh")
        assert s["openai_reasoning_effort"] == "high", (
            "xhigh passed through unchanged to a chat-completions model"
        )

    def test_xhigh_preserved_for_responses_api(self):
        s = self.reg.build_settings(self.responses_gpt, "xhigh")
        assert s["openai_reasoning_effort"] == "xhigh"


class TestPublicDict:
    def test_shape(self):
        spec = ModelSpec(
            id="DeepSeek-V4-Flash-0731",
            provider=LLMProvider.OPENAI,
            display_name="DeepSeek V4 Flash",
            supports_vision=False,
            supports_reasoning_effort=False,
        )
        assert spec.to_public_dict() == {
            "id": "DeepSeek-V4-Flash-0731",
            "name": "DeepSeek V4 Flash",
            "provider": "openai",
            "supports_reasoning_effort": False,
            "supports_vision": False,
        }


class TestResponseOnlyDumpReplay:
    """pydantic-ai 1.89.1 `new_messages()` excludes the user request when the
    prompt arrives via message_history (the adapter's pattern), so real dumps
    are response-only. The DB user row must be flushed ahead of them."""

    def _response_only_dump(self, answer: str) -> Any:
        return _dump([ModelResponse(parts=[TextPart(content=answer)])])

    def test_user_question_precedes_response_only_dump(self):
        rows = [
            _row("user", "q1"),
            _row("assistant", "a1", bucket=_bucketed(self._response_only_dump("a1"))),
            _row("user", "q2"),
        ]
        history = load_model_history(rows)
        assert [type(m).__name__ for m in history] == [
            "ModelRequest",
            "ModelResponse",
            "ModelRequest",
        ]
        assert history[0].parts[0].content == "q1"

    def test_model_prompt_bucket_preferred_over_content(self):
        bucket = {"client_message_id": "c1", "model_prompt": "q1\n\nCITATION BLOCK"}
        rows = [
            _row("user", "q1", bucket=bucket),
            _row("assistant", "a1", bucket=_bucketed(self._response_only_dump("a1"))),
        ]
        history = load_model_history(rows)
        assert history[0].parts[0].content == "q1\n\nCITATION BLOCK"

    def test_self_contained_dump_does_not_duplicate_question(self):
        # Defensive: if a dump DOES carry its own leading user request, the
        # DB fallback must be dropped, not duplicated.
        rows = [
            _row("user", "q1"),
            _row("assistant", "a1", bucket=_bucketed(_tool_turn_dump("q1", "a1"))),
        ]
        history = load_model_history(rows)
        user_requests = [
            m
            for m in history
            if type(m).__name__ == "ModelRequest"
            and any(type(p).__name__ == "UserPromptPart" for p in m.parts)
        ]
        assert len(user_requests) == 1


class TestMangledEvidenceMarkerFallback:
    """Observed live: DeepSeek emitted `**EVIDENCE**` instead of the literal
    `---EVIDENCE---` opener (closing marker intact). A bare full-line
    `@cite[n|...]` marker is the fallback trigger in both the batch stripper
    and the streaming filter, so citations survive marker mangling."""

    MANGLED = (
        'The answer is X [^1].\n\n---\n\n**EVIDENCE**\n'
        '@cite[1|page=5]\n"the quote"\n---END-EVIDENCE---'
    )

    def test_batch_strip_trims_block_and_header(self):
        assert strip_evidence_blocks(self.MANGLED) == "The answer is X [^1]."

    def test_citations_survive_mangled_opener(self):
        assert extract_citations(self.MANGLED) == [
            {"key": 1, "reference": '"the quote"', "page": 5}
        ]

    def test_stream_filter_suppresses_from_cite_line(self):
        f = EvidenceFilter()
        out = "".join([f.push(self.MANGLED[i : i + 5]) for i in range(0, len(self.MANGLED), 5)])
        out += f.flush()
        # The mangled header already streamed by trigger time — only the
        # citation block itself must be suppressed.
        assert out.strip() == "The answer is X [^1].\n\n---\n\n**EVIDENCE**"

    def test_prose_about_cite_syntax_does_not_trigger(self):
        text = "the @cite[1] marker means a citation\n@cite[2] is another"
        assert strip_evidence_blocks(text) == text
        f = EvidenceFilter()
        assert (f.push(text) + f.flush()) == text

    def test_trailing_cite_line_at_eos_is_suppressed(self):
        text = 'answer\n@cite[2|page=4]'
        assert strip_evidence_blocks(text) == "answer"
        f = EvidenceFilter()
        assert (f.push(text) + f.flush()).strip() == "answer"


def test_stored_references_reads_the_persisted_citation_list():
    from app.llm.chat.runtime import _stored_references

    row = _row(
        "user",
        "q",
        references={
            "citations": [
                {"key": "1", "reference": "quoted text"},
                {"key": "2", "reference": ""},
                "junk",
            ]
        },
    )
    assert _stored_references(row) == ["quoted text"]
    assert _stored_references(_row("user", "q")) == []
