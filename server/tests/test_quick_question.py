"""Unit tests for the ephemeral quick-question endpoint.

Covers the three things that can silently go wrong: which failures map to
which HTTP status, how a large file is windowed around the selection, and
that a stray evidence block never reaches the client.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest

from app.llm.chat.quick_question import (
    CODE_FULL_LINE_LIMIT,
    HEAD_LINES,
    MAX_QUESTION_CHARS,
    TRUNCATION_MARKER,
    WINDOW_CONTEXT_LINES,
    QuickQuestionError,
    build_code_context,
    build_quick_question_prompt,
    load_quick_question_code,
)
from app.llm.repo import storage

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
    path, content = load_quick_question_code(
        paper_id=PAPER_ID, file_path="pkg/mod.py"
    )
    assert path == "pkg/mod.py"
    assert content == SMALL_FILE


def test_load_tolerates_a_repo_prefixed_path(ready_snapshot):
    path, _ = load_quick_question_code(
        paper_id=PAPER_ID, file_path="/repo/pkg/mod.py"
    )
    assert path == "pkg/mod.py"


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
