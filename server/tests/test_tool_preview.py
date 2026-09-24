"""Structural (JSON-valued) previews of oversized tool outputs."""

import json
from datetime import datetime

from app.llm.chat.stream import TOOL_OUTPUT_WIRE_CAP, truncate_tool_output
from app.llm.chat.tool_preview import (
    MORE_KEY,
    PREVIEW_BUDGETS,
    clip_string,
    clip_value,
    preview_json,
)


class TestClipValue:
    def test_short_leaves_pass_through_unchanged(self):
        value = {"n": 1, "f": 1.5, "b": True, "none": None, "s": "short"}
        clipped = clip_value(value, string_cap=10, item_cap=10, max_depth=3)
        assert clipped == value

    def test_long_string_gets_a_counted_tail(self):
        assert clip_string("abcdef", 3) == "abc…[+3 chars]"
        assert clip_string("abc", 3) == "abc"

    def test_long_list_keeps_head_and_counts_the_rest(self):
        clipped = clip_value(list(range(10)), string_cap=99, item_cap=3, max_depth=3)
        assert clipped == [0, 1, 2, "…[+7 more items]"]

    def test_wide_dict_keeps_first_keys_in_order(self):
        value = {f"k{i}": i for i in range(6)}
        clipped = clip_value(value, string_cap=99, item_cap=2, max_depth=3)
        assert clipped == {"k0": 0, "k1": 1, MORE_KEY: "[+4 more keys]"}

    def test_depth_limit_collapses_nested_collections(self):
        value = {"a": {"b": {"c": [1, 2, 3]}}}
        clipped = clip_value(value, string_cap=99, item_cap=9, max_depth=2)
        assert clipped == {"a": {"b": "{…1 keys}"}}
        as_list = {"a": [[1, 2], [3]]}
        clipped = clip_value(as_list, string_cap=99, item_cap=9, max_depth=2)
        assert clipped == {"a": ["[…2 items]", "[…1 items]"]}

    def test_non_json_leaves_preview_as_str(self):
        when = datetime(2026, 9, 5, 12, 0)
        clipped = clip_value({"when": when}, string_cap=99, item_cap=9, max_depth=3)
        assert clipped == {"when": str(when)}
        json.dumps(clipped)  # must be encodable without `default`

    def test_input_is_not_mutated(self):
        value = {"items": list(range(10)), "text": "x" * 100}
        snapshot = json.dumps(value)
        clip_value(value, string_cap=5, item_cap=2, max_depth=3)
        assert json.dumps(value) == snapshot

    def test_tuples_preview_as_lists(self):
        clipped = clip_value((1, 2), string_cap=9, item_cap=9, max_depth=3)
        assert clipped == [1, 2]


class TestPreviewJson:
    def test_uses_the_most_generous_budget_that_fits(self):
        # A single 1,000-char string fits the first budget (1,200) untouched.
        value = {"content": "y" * 1000}
        assert preview_json(value, cap=2000) == value

    def test_tightens_until_it_fits(self):
        value = {f"key{i}": "z" * 500 for i in range(30)}
        preview = preview_json(value, cap=TOOL_OUTPUT_WIRE_CAP)
        assert preview is not None
        assert len(json.dumps(preview)) <= TOOL_OUTPUT_WIRE_CAP
        assert MORE_KEY in preview
        assert preview["key0"].endswith(" chars]")

    def test_preview_is_valid_json_at_every_budget(self):
        value = {
            "sections": [
                {"title": f"Section {i}", "text": "t" * 3000, "refs": list(range(50))}
                for i in range(20)
            ]
        }
        for string_cap, item_cap, max_depth in PREVIEW_BUDGETS:
            clipped = clip_value(
                value, string_cap=string_cap, item_cap=item_cap, max_depth=max_depth
            )
            assert json.loads(json.dumps(clipped)) == clipped

    def test_gives_up_only_on_pathological_width(self):
        # Even the tightest budget keeps 3 top-level keys, so a cap smaller
        # than that is unmeetable — the caller falls back to a raw slice.
        value = {f"k{i}": i for i in range(5)}
        assert preview_json(value, cap=5) is None


class TestTruncateToolOutputPreviewShape:
    def test_structured_output_previews_as_trimmed_json(self):
        out = {
            "name": "Methods",
            "content": "m" * (TOOL_OUTPUT_WIRE_CAP + 1000),
            "figures": [{"label": f"Figure {i}"} for i in range(3)],
        }
        result = truncate_tool_output(out)
        assert result["truncated"] is True
        preview = result["preview"]
        assert isinstance(preview, dict)
        assert preview["name"] == "Methods"
        assert preview["figures"] == out["figures"]
        assert preview["content"].startswith("mmm")
        assert preview["content"].endswith(" chars]")
        assert len(json.dumps(result)) <= TOOL_OUTPUT_WIRE_CAP + 200
        assert "omitted_chars" not in result

    def test_list_output_previews_as_trimmed_list(self):
        out = [{"page": i, "match": "hit " * 200} for i in range(40)]
        result = truncate_tool_output(out)
        assert result["truncated"] is True
        assert isinstance(result["preview"], list)
        assert result["preview"][0]["page"] == 0
        assert result["preview"][-1].startswith("…[+")

    def test_run_python_shape_previews_text_with_omitted_count(self):
        out = {"files": ["a.py"], "output": "o" * (TOOL_OUTPUT_WIRE_CAP + 50)}
        result = truncate_tool_output(out)
        assert result["files"] == ["a.py"]
        assert result["preview"] == "o" * TOOL_OUTPUT_WIRE_CAP
        assert result["omitted_chars"] == 50

    def test_bare_string_output_previews_as_text(self):
        result = truncate_tool_output("s" * (TOOL_OUTPUT_WIRE_CAP + 7))
        assert result["preview"] == "s" * TOOL_OUTPUT_WIRE_CAP
        assert result["omitted_chars"] == 7

    def test_marker_round_trips_through_json(self):
        out = {"content": "c" * (TOOL_OUTPUT_WIRE_CAP * 2)}
        result = truncate_tool_output(out)
        assert json.loads(json.dumps(result)) == result
