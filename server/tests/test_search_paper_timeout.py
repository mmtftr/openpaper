"""search_paper must not hang on a catastrophic model-written regex."""

from types import SimpleNamespace

from app.ingest.content import Page
from app.llm.tools import section_tools as st


def _patch_single_page(monkeypatch, markdown: str) -> None:
    paper = SimpleNamespace(id="p")
    pages = [Page(page_no=1, markdown=markdown)]
    monkeypatch.setattr(st, "_load_pages", lambda db, p: pages)
    monkeypatch.setattr(st, "_get_paper_or_raise", lambda *a, **k: paper)
    monkeypatch.setattr(st, "_resolve_target_paper_id", lambda *a, **k: "p")


def test_catastrophic_pattern_times_out(monkeypatch):
    _patch_single_page(monkeypatch, "x" * 5000)
    monkeypatch.setattr(st, "SEARCH_TIME_BUDGET_S", 0.05)
    result = st.search_paper("p", "(x+x+)+y", None, None)
    assert "timed out" in result["error"]


def test_normal_pattern_still_matches(monkeypatch):
    _patch_single_page(monkeypatch, "intro\ncircuit breakers reroute\noutro")
    result = st.search_paper("p", "circuit\\s+break", None, None)
    assert [h["match"] for h in result["hits"]] == ["circuit breakers reroute"]


def test_invalid_pattern_reports_error(monkeypatch):
    _patch_single_page(monkeypatch, "anything")
    assert "Invalid regex" in st.search_paper("p", "(", None, None)["error"]


def test_malformed_inline_flags_report_error(monkeypatch):
    _patch_single_page(monkeypatch, "foo")
    for pattern in ["(?u)(?L)foo", "(?V0)(?V1)foo"]:
        assert "Invalid regex" in st.search_paper("p", pattern, None, None)["error"]
