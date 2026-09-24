"""Reader outline grounding, OCR coordinates, caching and access control."""

import uuid
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import paper_api
from app.auth.dependencies import get_current_user
from app.database.database import get_db
from app.llm import paper_outline as outline


def title_block(content, top=250, bottom=275):
    return {"type": "title", "content": content, "top_left_x": 80,
            "top_left_y": top, "bottom_right_x": 500, "bottom_right_y": bottom}


@pytest.fixture
def paper():
    return SimpleNamespace(
        id=uuid.uuid4(), parser="mistral", page_count=3, generated_outline=None,
        ocr={"pages": [
            {"index": 0, "dimensions": {"width": 791, "height": 1000, "dpi": 93},
             "markdown": "# A Paper\nAuthor text\n# Abstract\nText\n# 1 Introduction\nText",
             "blocks": [title_block("# A Paper", 100, 150),
                        title_block("# Abstract", 200, 225),
                        title_block("# 1 Introduction", 600, 625)]},
            {"index": 2, "dimensions": {"height": 2000},
             "markdown": "# 2 Methods\nText\n# 2.1 **Data**\nText\n# Figure 1: data\n# arXiv:1234\n# Running header",
             "blocks": [title_block("# 2 Methods", 1000, 1050),
                        title_block("# 2.1 Data", 1600, 1650),
                        {"type": "header", "content": "# Running header"}]},
        ]},
    )


def test_candidates_reuse_markdown_headings_and_pixel_coordinates(paper):
    candidates = outline.extract_candidates(paper)
    assert [c["title"] for c in candidates] == [
        "A Paper", "Abstract", "1 Introduction", "2 Methods", "2.1 Data"]
    assert [c["page"] for c in candidates] == [1, 1, 1, 3, 3]
    assert [c["top_percent"] for c in candidates] == [10, 20, 60, 50, 80]
    assert candidates[-1]["level"] == 2  # all markdown headings were '#'


@pytest.mark.parametrize("dimensions,top,bottom", [
    ({}, 0, 10), ({"height": 0}, 0, 10), ({"height": 100}, -1, 10),
    ({"height": 100}, 101, 110), ({"height": 100}, 50, 49),
    ({"height": "bad"}, 0, 10), ({"height": 100}, float("nan"), 20),
    ({"height": float("inf")}, 0, 20),
])
def test_invalid_coordinates_omit_position(paper, dimensions, top, bottom):
    page = paper.ocr["pages"][0]
    page["dimensions"] = dimensions
    page["blocks"][0].update(top_left_y=top, bottom_right_y=bottom)
    assert outline.extract_candidates(paper)[0]["top_percent"] is None


def test_zero_position_and_repeated_headings_match_in_reading_order(paper):
    page = paper.ocr["pages"][0]
    page["markdown"] = "## Summary\ntext\n## Summary"
    page["blocks"] = [title_block("## Summary", 0, 20), title_block("## Summary", 500, 520)]
    assert [c["top_percent"] for c in outline.extract_candidates(paper)[:2]] == [0, 50]


@pytest.mark.parametrize("ocr", [None, [], {"pages": None}, {"pages": [None, {"index": "bad"}]}])
def test_missing_or_malformed_ocr_is_empty(paper, ocr, llm):
    paper.ocr = ocr
    assert outline.generate_outline(paper) == []


def test_pymupdf_has_no_invented_page_numbers(paper, llm):
    paper.parser = "pymupdf"
    paper.raw_content = "# Introduction\nHello"
    assert outline.generate_outline(paper) == []


def selection(entries):
    return outline.OutlineSelection.model_validate({"entries": [
        {"candidate_id": i, "title": title, "level": level} for i, title, level in entries
    ]})


def test_selection_cleans_numbering_and_builds_grounded_tree(paper):
    result = outline.validate_selection(outline.extract_candidates(paper), selection([
        (1, "Abstract", 1), (2, "1. Introduction", 1),
        (3, "2. Methods", 1), (4, "2.1. Data", 2),
    ]))
    assert [e["title"] for e in result] == ["Abstract", "1. Introduction", "2. Methods"]
    assert result[-1]["children"][0] == {
        "title": "2.1. Data", "level": 2, "page": 3, "top_percent": 80, "children": []}


def test_orphan_sections_cannot_be_nested_under_abstract(paper):
    result = outline.validate_selection(outline.extract_candidates(paper), selection([
        (1, "Abstract", 1), (2, "1 Introduction", 2),
        (3, "2 Methods", 2), (4, "2.1 Data", 3),
    ]))
    assert [e["title"] for e in result] == ["Abstract", "1 Introduction", "2 Methods"]
    assert result[0]["children"] == []
    assert result[-1]["children"][0]["level"] == 2


@pytest.mark.parametrize("entries, titles", [
    ([(99, "Invented", 1)], []),
    ([(1, "Abstract", 1), (1, "Abstract", 1)], ["Abstract"]),
    ([(2, "1 Introduction", 1), (1, "Abstract", 1)], ["1 Introduction"]),
    ([(1, "Abstract", 2)], ["Abstract"]),
    ([(1, "New conclusions", 1)], ["Abstract"]),
])
def test_bad_entries_are_repaired_not_fatal(paper, entries, titles):
    result = outline.validate_selection(outline.extract_candidates(paper), selection(entries))
    assert [e["title"] for e in result] == titles
    assert all(e["level"] == 1 for e in result)


def test_skipped_level_is_clamped(paper):
    result = outline.validate_selection(outline.extract_candidates(paper), selection([
        (3, "2 Methods", 1), (4, "2.1 Data", 3),
    ]))
    assert result[0]["children"][0]["level"] == 2


def test_one_ungrounded_title_keeps_the_rest_of_the_cleanup(paper):
    result = outline.validate_selection(outline.extract_candidates(paper), selection([
        (1, "Abstract", 1), (3, "Our Wonderful Methods", 1), (4, "2.1. Data", 2),
    ]))
    assert [e["title"] for e in result] == ["Abstract", "2 Methods"]
    assert result[1]["children"][0]["title"] == "2.1. Data"


class FakeOneshot:
    """Stands in for `oneshot.complete_sync`. The JSON payload is validated
    against the requested output type the way pydantic-ai's tool-output
    validation would, and raises when it doesn't fit."""

    def __init__(self):
        self.payload = None
        self.error = None
        self.calls = []

    def __call__(self, slot, prompt, *, output_type=str, instructions=None):
        self.calls.append({"slot": slot, "prompt": prompt, "output_type": output_type,
                           "instructions": instructions})
        if self.error is not None:
            raise self.error
        return output_type.model_validate_json(self.payload)


@pytest.fixture
def llm(monkeypatch):
    fake = FakeOneshot()
    monkeypatch.setattr(outline.oneshot, "complete_sync", fake)
    return fake


@pytest.mark.parametrize("payload", [
    "not json",
    '{"entries":[{"candidate_id":1,"title":"Abstract","level":1,"page":99}]}',
    '{"entries":[{"candidate_id":1,"title":"Abstract","level":true}]}',
])
def test_bad_llm_output_serves_deterministic_outline_uncached(paper, payload, llm):
    llm.payload = payload
    with pytest.raises(outline.OutlineCleanupUnavailable) as exc:
        outline.generate_outline(paper)
    result = exc.value.fallback
    assert [e["title"] for e in result] == ["A Paper", "Abstract", "1 Introduction", "2 Methods"]
    assert result[-1]["children"][0]["title"] == "2.1 Data"
    assert llm.calls[-1]["slot"] == "ingest.outline"
    assert llm.calls[-1]["output_type"] is outline.OutlineSelection
    assert llm.calls[-1]["instructions"] == outline.OUTLINE_PROMPT


def test_llm_failure_falls_back_without_nesting_same_level_headings(paper, llm):
    paper.ocr["pages"][0]["markdown"] = "### One\n### Two\n### Three"
    llm.error = RuntimeError("offline")
    with pytest.raises(outline.OutlineCleanupUnavailable) as exc:
        outline.generate_outline(paper)
    assert [e["title"] for e in exc.value.fallback[:3]] == ["One", "Two", "Three"]


def test_success_and_empty_selection_are_accepted(paper, llm):
    llm.payload = selection([(1, "Abstract", 1)]).model_dump_json()
    assert outline.generate_outline(paper)[0]["page"] == 1
    llm.payload = '{"entries":[]}'
    assert outline.generate_outline(paper) == []


def test_default_slot_uses_openai_fast_deployment():
    from app.llm.model_registry import (
        LLMProvider,
        ModelRegistry,
        ModelSpec,
        _ProviderConfig,
    )
    from app.llm.model_slots import resolve_slot

    configs = {
        LLMProvider.OPENAI: _ProviderConfig("k", None, "gpt-5.5", "gpt-5.4-mini"),
        LLMProvider.CODEX_PROXY: _ProviderConfig(
            "k", "http://proxy/v1", "gpt-6-astra", "gpt-5.4-mini"),
    }
    specs = [ModelSpec(id="gpt-5.4-mini", provider=LLMProvider.OPENAI, display_name="mini")]
    # Even while the codex proxy is the default provider.
    registry = ModelRegistry(specs, configs, LLMProvider.CODEX_PROXY)
    spec = resolve_slot("ingest.outline", registry).spec
    assert (spec.provider, spec.id) == (LLMProvider.OPENAI, "gpt-5.4-mini")


def test_missing_blocks_still_produce_page_targets_and_bad_pages_are_skipped(paper):
    paper.ocr["pages"][0]["blocks"] = None
    paper.ocr["pages"].extend([
        {"index": -1, "markdown": "# Invalid"},
        {"index": 3, "markdown": "# Outside PDF"},
        {"index": True, "markdown": "# Not a page"},
    ])
    candidates = outline.extract_candidates(paper)
    assert len(candidates) == 5
    assert candidates[0]["page"] == 1
    assert candidates[0]["top_percent"] is None


def test_large_outlines_skip_llm_without_losing_headings(paper, llm):
    paper.ocr["pages"][0]["markdown"] = "\n".join(f"# Topic {i}" for i in range(301))
    paper.ocr["pages"] = paper.ocr["pages"][:1]
    assert len(outline.generate_outline(paper)) == 301
    assert llm.calls == []


def _cleaned(monkeypatch, result):
    fn = Mock(return_value=result)
    monkeypatch.setattr(outline, "_outline_from_candidates", fn)
    return fn


def test_cache_stores_cleaned_outline_with_conditional_update(paper, monkeypatch):
    generate = _cleaned(monkeypatch, [{"title": "Abstract"}])
    db = Mock()
    assert outline.cached_outline(db, paper) == [{"title": "Abstract"}]
    generate.assert_called_once()
    stmt = str(db.execute.call_args.args[0])
    assert "UPDATE papers" in stmt and "generated_outline IS NULL" in stmt
    db.commit.assert_called_once()


def test_read_transaction_ends_before_the_llm_call(paper, monkeypatch):
    db = Mock()
    order = []
    db.rollback.side_effect = lambda: order.append("rollback")
    monkeypatch.setattr(outline, "_outline_from_candidates",
                        Mock(side_effect=lambda c: order.append("llm") or []))
    outline.cached_outline(db, paper)
    assert order[:2] == ["rollback", "llm"]


def test_cached_outline_is_served_without_regenerating(paper, monkeypatch):
    generate = _cleaned(monkeypatch, [])
    paper.generated_outline = [{"title": "Cached"}]
    db = Mock()
    assert outline.cached_outline(db, paper) == [{"title": "Cached"}]
    generate.assert_not_called()
    db.execute.assert_not_called()


def test_no_candidates_is_empty_and_uncached(paper, monkeypatch):
    # e.g. OCR hasn't finished yet: caching [] would pin it after OCR lands.
    generate = _cleaned(monkeypatch, [])
    paper.ocr = None
    db = Mock()
    assert outline.cached_outline(db, paper) == []
    generate.assert_not_called()
    db.execute.assert_not_called()


def test_concurrent_request_gets_ocr_headings_instead_of_waiting(paper, monkeypatch):
    generate = _cleaned(monkeypatch, [])
    monkeypatch.setattr(outline, "_in_flight", {str(paper.id)})
    db = Mock()
    result = outline.cached_outline(db, paper)
    assert [e["title"] for e in result][:2] == ["A Paper", "Abstract"]
    generate.assert_not_called()
    db.execute.assert_not_called()


def test_cleanup_failure_is_served_but_not_cached(paper, monkeypatch):
    monkeypatch.setattr(outline, "_outline_from_candidates", Mock(
        side_effect=outline.OutlineCleanupUnavailable([{"title": "X"}])))
    db = Mock()
    assert outline.cached_outline(db, paper) == [{"title": "X"}]
    db.execute.assert_not_called()
    db.commit.assert_not_called()
    assert outline._in_flight == set()


def test_write_failure_rolls_back_and_releases_in_flight(paper, monkeypatch):
    _cleaned(monkeypatch, [{"title": "A"}])
    db = Mock()
    db.execute.side_effect = RuntimeError("row deleted")
    with pytest.raises(RuntimeError):
        outline.cached_outline(db, paper)
    assert db.rollback.call_count == 2
    assert outline._in_flight == set()


@pytest.fixture
def api(monkeypatch, paper):
    app = FastAPI()
    app.include_router(paper_api.paper_router, prefix="/api/paper")
    user = SimpleNamespace(id=uuid.uuid4())
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_db] = lambda: None
    get = Mock(return_value=paper)
    cached = Mock(return_value=[{"title": "Methods", "level": 1, "page": 3, "top_percent": 50}])
    monkeypatch.setattr(paper_api.paper_crud, "get", get)
    monkeypatch.setattr(paper_api, "cached_outline", cached)
    return SimpleNamespace(client=TestClient(app), app=app, get=get,
                           cached=cached, user=user, paper=paper)


def test_owner_endpoint_passes_user_and_displayed_paper_id(api):
    response = api.client.get(f"/api/paper/outline?id={api.paper.id}")
    assert response.status_code == 200
    assert response.json()[0]["top_percent"] == 50
    api.get.assert_called_once_with(None, id=api.paper.id, user=api.user)


def test_private_endpoint_requires_auth_even_if_cached(api):
    api.app.dependency_overrides[get_current_user] = lambda: None
    assert api.client.get(f"/api/paper/outline?id={api.paper.id}").status_code == 401
    api.cached.assert_not_called()


def test_private_endpoint_rejects_nonowner_and_bad_id(api):
    api.get.return_value = None
    assert api.client.get(f"/api/paper/outline?id={api.paper.id}").status_code == 404
    assert api.client.get("/api/paper/outline?id=invalid").status_code == 422
    api.cached.assert_not_called()


def test_no_public_share_outline_endpoint(api):
    # Removed: unauthenticated callers could trigger (and retry) LLM calls.
    api.app.dependency_overrides[get_current_user] = lambda: None
    assert api.client.get("/api/paper/share/outline?id=share-token").status_code in (404, 405, 422)
    api.cached.assert_not_called()
