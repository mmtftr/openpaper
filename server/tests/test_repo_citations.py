"""Unit tests for code citations: parsing, host-side verification, repair.

Code citations are verified against the exact bytes we ingested — no LLM in
the loop — so all three outcomes (exact / repaired / fabricated) are
deterministic and testable.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from app.llm.chat.evidence import (
    extract_citations,
    parse_evidence_block,
    reconcile_citations,
)
from app.llm.repo.code_citations import verify_code_citation, verify_code_citations
from app.llm.repo.sandbox import RepoSnapshot

SOURCE = """\
import torch


def get_refusal_direction(harmful, harmless):
    diff = harmful.mean(dim=0) - harmless.mean(dim=0)
    return diff / diff.norm()


def select_direction(candidates):
    scored = [(score(c), c) for c in candidates]
    return max(scored)[1]
"""


@pytest.fixture()
def snapshot(tmp_path: Path) -> RepoSnapshot:
    root = tmp_path / "snap"
    (root / "pipeline").mkdir(parents=True)
    (root / "pipeline" / "run.py").write_text(SOURCE, encoding="utf-8")
    return RepoSnapshot(
        paper_id="55555555-5555-5555-5555-555555555555",
        owner="andyrdt",
        repo="refusal_direction",
        ref="main",
        commit_sha="f" * 40,
        root=root,
        files=[{"path": "pipeline/run.py", "size": len(SOURCE)}],
    )


# -- marker parsing -------------------------------------------------------


def test_parse_evidence_block_reads_file_and_lines():
    block = (
        "@cite[1|file=pipeline/run.py|lines=5-6]\n"
        "diff = harmful.mean(dim=0) - harmless.mean(dim=0)\n"
        "return diff / diff.norm()\n"
    )
    (citation,) = parse_evidence_block(block)
    assert citation["key"] == 1
    assert citation["file"] == "pipeline/run.py"
    assert citation["start_line"] == 5
    assert citation["end_line"] == 6
    assert "page" not in citation


@pytest.mark.parametrize(
    "marker,expected",
    [
        ("lines=42", (42, 42)),
        ("lines=42-57", (42, 57)),
        ("lines=57-42", (57, 57)),      # inverted range collapses
        ("lines=L42-L57", (42, 57)),
        ("lines=abc", (None, None)),
        ("lines=0", (None, None)),
    ],
)
def test_line_range_forms(marker, expected):
    block = f"@cite[1|file=a.py|{marker}]\nquoted text\n"
    (citation,) = parse_evidence_block(block)
    assert (citation.get("start_line"), citation.get("end_line")) == expected


def test_file_marker_tolerates_the_repo_prefix():
    block = "@cite[1|file=/repo/pipeline/run.py|lines=1-2]\nimport torch\n"
    (citation,) = parse_evidence_block(block)
    assert citation["file"] == "pipeline/run.py"


def test_extract_citations_handles_mixed_page_and_file_citations():
    text = (
        "Prose.\n---EVIDENCE---\n"
        "@cite[1|page=3]\n\"from the paper\"\n"
        "@cite[2|file=pipeline/run.py|lines=4-6]\ndef get_refusal_direction(harmful, harmless):\n"
        "---END-EVIDENCE---\n"
    )
    citations = extract_citations(text)
    assert [c.get("page") for c in citations] == [3, None]
    assert [c.get("file") for c in citations] == [None, "pipeline/run.py"]


# -- verification ---------------------------------------------------------


def test_exact_citation_is_verified_and_linked(snapshot):
    citation = {
        "key": 1,
        "file": "pipeline/run.py",
        "start_line": 4,
        "end_line": 6,
        "reference": "def get_refusal_direction(harmful, harmless): "
        "diff = harmful.mean(dim=0) - harmless.mean(dim=0)",
    }
    out = verify_code_citation(citation, snapshot)
    assert out["verified"] is True
    assert out["matched_via"] == "exact"
    assert (out["start_line"], out["end_line"]) == (4, 6)
    assert out["github_url"] == (
        f"https://github.com/andyrdt/refusal_direction/blob/{'f' * 40}"
        "/pipeline/run.py#L4-L6"
    )
    assert citation.get("verified") is None  # input untouched


def test_moved_citation_has_its_line_numbers_repaired(snapshot):
    citation = {
        "key": 1,
        "file": "pipeline/run.py",
        "start_line": 90,       # nowhere near the truth
        "end_line": 95,
        "reference": "return diff / diff.norm()",
    }
    out = verify_code_citation(citation, snapshot)
    assert out["verified"] is True
    assert out["matched_via"] == "repaired"
    assert out["start_line"] == 6 and out["end_line"] == 6
    assert "#L6" in out["github_url"]


def test_multiline_quote_is_matched_whitespace_normalized(snapshot):
    # The parser flattens a quote's lines with single spaces; a literal
    # comparison against the file would fail here.
    citation = {
        "key": 1,
        "file": "pipeline/run.py",
        "start_line": 1,
        "end_line": 2,
        "reference": "scored = [(score(c), c) for c in candidates] "
        "return max(scored)[1]",
    }
    out = verify_code_citation(citation, snapshot)
    assert out["verified"] is True
    assert out["start_line"] == 10 and out["end_line"] == 11


def test_quote_with_literal_backslash_n_escapes_is_matched(snapshot):
    """Models routinely emit a multi-line code quote as one line with
    literal `\\n` escapes; those are not whitespace, so the normalizer needs
    an unescaping pass or every such citation would read as fabricated."""
    citation = {
        "key": 1,
        "file": "pipeline/run.py",
        "start_line": 4,
        "end_line": 6,
        "reference": (
            "def get_refusal_direction(harmful, harmless):\\n"
            "    diff = harmful.mean(dim=0) - harmless.mean(dim=0)\\n"
            "    return diff / diff.norm()"
        ),
    }
    out = verify_code_citation(citation, snapshot)
    assert out["verified"] is True
    assert (out["start_line"], out["end_line"]) == (4, 6)


def test_quote_copied_from_read_gutter_is_matched(snapshot):
    """Observed live: models quote `read()` output verbatim, gutter and all
    (`  5|     diff = ...`). That can never match raw file bytes, so the
    gutter is stripped before matching."""
    citation = {
        "key": 1,
        "file": "pipeline/run.py",
        "start_line": 4,
        "end_line": 6,
        "reference": (
            "4| def get_refusal_direction(harmful, harmless):\n"
            "5|     diff = harmful.mean(dim=0) - harmless.mean(dim=0)\n"
            "6|     return diff / diff.norm()"
        ),
    }
    out = verify_code_citation(citation, snapshot)
    assert out["verified"] is True
    assert (out["start_line"], out["end_line"]) == (4, 6)
    assert "#L4-L6" in out["github_url"]


def test_gutter_line_numbers_repair_a_missing_lines_marker(snapshot):
    """No `lines=` at all: the gutter's first number is the anchor."""
    citation = {
        "key": 1,
        "file": "pipeline/run.py",
        "reference": (
            "  9| def select_direction(candidates):\n"
            " 10|     scored = [(score(c), c) for c in candidates]"
        ),
    }
    out = verify_code_citation(citation, snapshot)
    assert out["verified"] is True
    assert out["matched_via"] == "line-numbered"
    assert (out["start_line"], out["end_line"]) == (9, 10)


def test_gutter_line_numbers_override_a_wrong_lines_marker(snapshot):
    citation = {
        "key": 1,
        "file": "pipeline/run.py",
        "start_line": 200,
        "end_line": 240,
        "reference": "9| def select_direction(candidates):",
    }
    out = verify_code_citation(citation, snapshot)
    assert out["verified"] is True
    assert out["start_line"] == 9


def test_gutter_stripping_is_escape_aware(snapshot):
    """Gutter + JSON escaping together — both live behaviors at once."""
    citation = {
        "key": 1,
        "file": "pipeline/run.py",
        "start_line": 4,
        "end_line": 5,
        "reference": (
            "4| def get_refusal_direction(harmful, harmless):\\n"
            "5|     diff = harmful.mean(dim=0) - harmless.mean(dim=0)"
        ),
    }
    out = verify_code_citation(citation, snapshot)
    assert out["verified"] is True
    assert (out["start_line"], out["end_line"]) == (4, 5)


def test_real_code_with_pipes_is_not_mistaken_for_a_gutter(tmp_path: Path):
    """`flags = READ | WRITE` must not be mangled by gutter stripping."""
    source = "FLAGS = 1 | 2\nMASK = 3 | 4\nOTHER = 5\n"
    root = tmp_path / "snap"
    root.mkdir()
    (root / "a.py").write_text(source, encoding="utf-8")
    snap = RepoSnapshot(
        paper_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        owner="o", repo="r", ref="main", commit_sha="c" * 40,
        root=root, files=[{"path": "a.py", "size": len(source)}],
    )
    out = verify_code_citation(
        {"key": 1, "file": "a.py", "start_line": 1, "end_line": 2,
         "reference": "FLAGS = 1 | 2\nMASK = 3 | 4"},
        snap,
    )
    assert out["verified"] is True
    assert (out["start_line"], out["end_line"]) == (1, 2)


def test_quote_with_json_escaped_inner_quotes_is_matched(tmp_path: Path):
    """Observed live: models escape inner quotes as `\\"` when the span
    contains an f-string, so the escape decoder must handle more than
    newlines."""
    source = 'def show(x):\n    print(f"value: {x:.4f}")\n    return x\n'
    root = tmp_path / "snap"
    root.mkdir()
    (root / "a.py").write_text(source, encoding="utf-8")
    snap = RepoSnapshot(
        paper_id="88888888-8888-8888-8888-888888888888",
        owner="o", repo="r", ref="main", commit_sha="a" * 40,
        root=root, files=[{"path": "a.py", "size": len(source)}],
    )
    citation = {
        "key": 1,
        "file": "a.py",
        "start_line": 1,
        "end_line": 3,
        "reference": 'def show(x):\\n    print(f\\"value: {x:.4f}\\")\\n    return x',
    }
    out = verify_code_citation(citation, snap)
    assert out["verified"] is True
    assert (out["start_line"], out["end_line"]) == (1, 3)


def test_unknown_escapes_are_preserved(tmp_path: Path):
    """A quoted regex literal keeps its `\\d` / `\\s` — decoding those would
    corrupt the very text being verified."""
    source = 'PATTERN = r"\\d+\\s*"\n'
    root = tmp_path / "snap"
    root.mkdir()
    (root / "a.py").write_text(source, encoding="utf-8")
    snap = RepoSnapshot(
        paper_id="99999999-9999-9999-9999-999999999999",
        owner="o", repo="r", ref="main", commit_sha="b" * 40,
        root=root, files=[{"path": "a.py", "size": len(source)}],
    )
    out = verify_code_citation(
        {"key": 1, "file": "a.py", "start_line": 1, "end_line": 1,
         "reference": 'PATTERN = r"\\d+\\s*"'},
        snap,
    )
    assert out["verified"] is True


def test_abridged_quote_is_verified_on_its_head(snapshot):
    """A long span quoted as `head ...` is still real evidence: verify the
    head and keep the claimed range."""
    citation = {
        "key": 1,
        "file": "pipeline/run.py",
        "start_line": 4,
        "end_line": 11,
        "reference": (
            "def get_refusal_direction(harmful, harmless):\\n"
            "    diff = harmful.mean(dim=0) - harmless.mean(dim=0)\\n..."
        ),
    }
    out = verify_code_citation(citation, snapshot)
    assert out["verified"] is True
    assert out["matched_via"] == "elided"
    assert (out["start_line"], out["end_line"]) == (4, 11)


def test_abridged_quote_with_a_wrong_range_is_repaired(snapshot):
    citation = {
        "key": 1,
        "file": "pipeline/run.py",
        "start_line": 80,
        "end_line": 90,
        "reference": "def select_direction(candidates):\\n    scored = [(score(c), c) for c in candidates]\\n…",
    }
    out = verify_code_citation(citation, snapshot)
    assert out["verified"] is True
    assert out["matched_via"] == "elided-repaired"
    assert out["start_line"] == 9


def test_a_short_head_before_an_ellipsis_is_not_enough(snapshot):
    """An ellipsis after a couple of characters must not verify anything."""
    citation = {
        "key": 1,
        "file": "pipeline/run.py",
        "start_line": 4,
        "end_line": 11,
        "reference": "import ...",
    }
    out = verify_code_citation(citation, snapshot)
    assert out["verified"] is False


def test_fabricated_quote_is_unverified_and_loses_its_line_anchor(snapshot):
    citation = {
        "key": 1,
        "file": "pipeline/run.py",
        "start_line": 4,
        "end_line": 6,
        "reference": "def totally_invented_function(sneaky):",
    }
    out = verify_code_citation(citation, snapshot)
    assert out["verified"] is False
    assert "start_line" not in out and "end_line" not in out
    # No permalink at all: binding revision 7 attaches `github_url` ONLY
    # after verification, and even a file-level link would read as
    # "we checked this".
    assert out["github_url"] is None


def test_a_too_short_quote_cannot_verify_an_arbitrary_range(snapshot):
    """An in-bounds line range is not evidence — a one-character quote must
    not earn a trusted permalink for lines nobody checked."""
    out = verify_code_citation(
        {"key": 1, "file": "pipeline/run.py", "start_line": 2, "end_line": 40,
         "reference": "x"},
        snapshot,
    )
    assert out["verified"] is False
    assert out["github_url"] is None
    assert "start_line" not in out


def test_unknown_file_is_unverified_with_no_link(snapshot):
    out = verify_code_citation(
        {"key": 1, "file": "does/not/exist.py", "start_line": 1, "reference": "x"},
        snapshot,
    )
    assert out["verified"] is False
    assert out["github_url"] is None


def test_bare_basename_resolves_when_unambiguous(snapshot):
    out = verify_code_citation(
        {"key": 1, "file": "run.py", "start_line": 1, "end_line": 1,
         "reference": "import torch"},
        snapshot,
    )
    assert out["file"] == "pipeline/run.py"
    assert out["verified"] is True


def test_verify_without_a_snapshot_marks_everything_unverified():
    out = verify_code_citations(
        [{"key": 1, "file": "a.py", "start_line": 2, "reference": "x"}], None
    )
    assert out[0]["verified"] is False
    assert out[0]["github_url"] is None
    assert "start_line" not in out[0]


def test_non_code_citations_pass_through_untouched(snapshot):
    out = verify_code_citations([{"key": 1, "page": 3, "reference": "quote"}], snapshot)
    assert out == [{"key": 1, "page": 3, "reference": "quote"}]


# -- reconciler branching -------------------------------------------------


def test_reconciler_handles_code_citations_before_the_page_shortcircuit(snapshot):
    """A code citation has no `page`; the legacy short-circuit would have
    passed it through verbatim (unverified, unlinked)."""
    citations = [
        {"key": 1, "file": "pipeline/run.py", "start_line": 6, "end_line": 6,
         "reference": "return diff / diff.norm()"},
        {"key": 2, "page": None, "reference": "legacy citation"},
    ]
    out = asyncio.run(
        reconcile_citations(
            citations, None, family_index={}, repo_snapshot=snapshot
        )
    )
    assert out[0]["verified"] is True
    assert out[0]["github_url"].endswith("#L6")
    assert out[1] == {"key": 2, "page": None, "reference": "legacy citation"}


def test_flattened_multiline_gutter_quote_is_matched(snapshot):
    """The evidence parser joins a quote's lines with spaces, so a three-line
    `read()` quote reaches verification as ONE line: `4| def ... 5| ...`."""
    text = (
        "---EVIDENCE---\n@cite[1|file=pipeline/run.py|lines=4-6]\n"
        "4| def get_refusal_direction(harmful, harmless):\n"
        "5|     diff = harmful.mean(dim=0) - harmless.mean(dim=0)\n"
        "6|     return diff / diff.norm()\n---END-EVIDENCE---"
    )
    [citation] = extract_citations(text)
    assert "\n" not in citation["reference"]
    out = verify_code_citation(citation, snapshot)
    assert out["verified"] is True
    assert (out["start_line"], out["end_line"]) == (4, 6)


def test_inline_pipes_in_real_code_are_not_split_as_gutters():
    from app.llm.repo.code_citations import _split_inline_gutters

    assert _split_inline_gutters("mask = 1|2 | 3|4") is None
    assert _split_inline_gutters("x = a | b") is None
    assert _split_inline_gutters("9| one 3| two") is None  # not increasing
    assert _split_inline_gutters("4| a 5| b 6| c") == ["4| a", "5| b", "6| c"]


def test_exact_match_is_narrowed_to_the_quoted_lines(snapshot):
    """A short quote under a wide `lines=` range verifies, but the range it
    earns is the quote's own — not the 400-line span the model claimed."""
    citation = {
        "key": 1,
        "file": "pipeline/run.py",
        "start_line": 1,
        "end_line": 6,
        "reference": (
            "diff = harmful.mean(dim=0) - harmless.mean(dim=0)\n"
            "return diff / diff.norm()"
        ),
    }
    out = verify_code_citation(citation, snapshot)
    assert out["verified"] is True
    assert (out["start_line"], out["end_line"]) == (5, 6)
    assert "#L5-L6" in out["github_url"]
