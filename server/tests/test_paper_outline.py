"""Reader outline grounding, OCR coordinates, LLM cleanup and the endpoint."""

import asyncio
import uuid
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.paper import detail as paper_detail
from app.api.paper import paper_router
from app.auth.dependencies import get_current_user
from app.database.database import get_db
from app.llm import paper_outline as outline


def candidates_of(paper) -> list[dict]:
    """Heading candidates from the fixture's Mistral-shaped OCR pages."""
    return outline.candidates_from_pages(
        outline.OutlinePage(
            page=page["index"] + 1,
            markdown=page["markdown"],
            blocks=page.get("blocks") or [],
            dimensions=page.get("dimensions") or {},
        )
        for page in paper.ocr["pages"]
    )


def generate(paper) -> list[dict]:
    return asyncio.run(outline.clean_outline(candidates_of(paper)))


def title_block(content, top=250, bottom=275):
    return {
        "type": "title",
        "content": content,
        "top_left_x": 80,
        "top_left_y": top,
        "bottom_right_x": 500,
        "bottom_right_y": bottom,
    }


@pytest.fixture
def paper():
    return SimpleNamespace(
        id=uuid.uuid4(),
        page_count=3,
        generated_outline=None,
        ocr={
            "pages": [
                {
                    "index": 0,
                    "dimensions": {"width": 791, "height": 1000, "dpi": 93},
                    "markdown": "# A Paper\nAuthor text\n# Abstract\nText\n# 1 Introduction\nText",
                    "blocks": [
                        title_block("# A Paper", 100, 150),
                        title_block("# Abstract", 200, 225),
                        title_block("# 1 Introduction", 600, 625),
                    ],
                },
                {
                    "index": 2,
                    "dimensions": {"height": 2000},
                    "markdown": "# 2 Methods\nText\n# 2.1 **Data**\nText\n# Figure 1: data\n# arXiv:1234\n# Running header",
                    "blocks": [
                        title_block("# 2 Methods", 1000, 1050),
                        title_block("# 2.1 Data", 1600, 1650),
                        {"type": "header", "content": "# Running header"},
                    ],
                },
            ]
        },
    )


def test_candidates_reuse_markdown_headings_and_pixel_coordinates(paper):
    candidates = candidates_of(paper)
    assert [c["title"] for c in candidates] == [
        "A Paper",
        "Abstract",
        "1 Introduction",
        "2 Methods",
        "2.1 Data",
    ]
    assert [c["page"] for c in candidates] == [1, 1, 1, 3, 3]
    assert [c["top_percent"] for c in candidates] == [10, 20, 60, 50, 80]
    assert candidates[-1]["level"] == 2  # all markdown headings were '#'


@pytest.mark.parametrize(
    "dimensions,top,bottom",
    [
        ({}, 0, 10),
        ({"height": 0}, 0, 10),
        ({"height": 100}, -1, 10),
        ({"height": 100}, 101, 110),
        ({"height": 100}, 50, 49),
        ({"height": "bad"}, 0, 10),
        ({"height": 100}, float("nan"), 20),
        ({"height": float("inf")}, 0, 20),
    ],
)
def test_invalid_coordinates_omit_position(paper, dimensions, top, bottom):
    page = paper.ocr["pages"][0]
    page["dimensions"] = dimensions
    page["blocks"][0].update(top_left_y=top, bottom_right_y=bottom)
    assert candidates_of(paper)[0]["top_percent"] is None


def test_zero_position_and_repeated_headings_match_in_reading_order(paper):
    page = paper.ocr["pages"][0]
    page["markdown"] = "## Summary\ntext\n## Summary"
    page["blocks"] = [
        title_block("## Summary", 0, 20),
        title_block("## Summary", 500, 520),
    ]
    assert [c["top_percent"] for c in candidates_of(paper)[:2]] == [0, 50]


def selection(entries):
    return outline.OutlineSelection.model_validate(
        {
            "entries": [
                {"candidate_id": i, "title": title, "level": level}
                for i, title, level in entries
            ]
        }
    )


def test_selection_cleans_numbering_and_builds_grounded_tree(paper):
    result = outline.validate_selection(
        candidates_of(paper),
        selection(
            [
                (1, "Abstract", 1),
                (2, "1. Introduction", 1),
                (3, "2. Methods", 1),
                (4, "2.1. Data", 2),
            ]
        ),
    )
    assert [e["title"] for e in result] == ["Abstract", "1. Introduction", "2. Methods"]
    assert result[-1]["children"][0] == {
        "title": "2.1. Data",
        "level": 2,
        "page": 3,
        "top_percent": 80,
        "children": [],
    }


def test_orphan_sections_cannot_be_nested_under_abstract(paper):
    result = outline.validate_selection(
        candidates_of(paper),
        selection(
            [
                (1, "Abstract", 1),
                (2, "1 Introduction", 2),
                (3, "2 Methods", 2),
                (4, "2.1 Data", 3),
            ]
        ),
    )
    assert [e["title"] for e in result] == ["Abstract", "1 Introduction", "2 Methods"]
    assert result[0]["children"] == []
    assert result[-1]["children"][0]["level"] == 2


@pytest.mark.parametrize(
    "entries, titles",
    [
        ([(99, "Invented", 1)], []),
        ([(1, "Abstract", 1), (1, "Abstract", 1)], ["Abstract"]),
        ([(2, "1 Introduction", 1), (1, "Abstract", 1)], ["1 Introduction"]),
        ([(1, "Abstract", 2)], ["Abstract"]),
        ([(1, "New conclusions", 1)], ["Abstract"]),
    ],
)
def test_bad_entries_are_repaired_not_fatal(paper, entries, titles):
    result = outline.validate_selection(candidates_of(paper), selection(entries))
    assert [e["title"] for e in result] == titles
    assert all(e["level"] == 1 for e in result)


def test_skipped_level_is_clamped(paper):
    result = outline.validate_selection(
        candidates_of(paper),
        selection(
            [
                (3, "2 Methods", 1),
                (4, "2.1 Data", 3),
            ]
        ),
    )
    assert result[0]["children"][0]["level"] == 2


def test_one_ungrounded_title_keeps_the_rest_of_the_cleanup(paper):
    result = outline.validate_selection(
        candidates_of(paper),
        selection(
            [
                (1, "Abstract", 1),
                (3, "Our Wonderful Methods", 1),
                (4, "2.1. Data", 2),
            ]
        ),
    )
    assert [e["title"] for e in result] == ["Abstract", "2 Methods"]
    assert result[1]["children"][0]["title"] == "2.1. Data"


class FakeOneshot:
    """Stands in for `oneshot.complete`. The JSON payload is validated
    against the requested output type the way pydantic-ai's tool-output
    validation would, and raises when it doesn't fit."""

    def __init__(self):
        self.payload = None
        self.error = None
        self.calls = []

    async def __call__(
        self, slot, prompt, *, output_type: Any = str, instructions=None
    ):
        self.calls.append(
            {
                "slot": slot,
                "prompt": prompt,
                "output_type": output_type,
                "instructions": instructions,
            }
        )
        if self.error is not None:
            raise self.error
        return output_type.model_validate_json(self.payload)


@pytest.fixture
def llm(monkeypatch):
    fake = FakeOneshot()
    monkeypatch.setattr(outline.oneshot, "complete", fake)
    return fake


@pytest.mark.parametrize(
    "payload",
    [
        "not json",
        '{"entries":[{"candidate_id":1,"title":"Abstract","level":1,"page":99}]}',
        '{"entries":[{"candidate_id":1,"title":"Abstract","level":true}]}',
    ],
)
def test_bad_llm_output_raises_for_a_stage_retry(paper, payload, llm):
    llm.payload = payload
    with pytest.raises(Exception):
        generate(paper)
    assert llm.calls[-1]["slot"] == "ingest.outline"
    assert llm.calls[-1]["output_type"] is outline.OutlineSelection
    assert llm.calls[-1]["instructions"] == outline.OUTLINE_PROMPT


def test_success_and_empty_selection_are_accepted(paper, llm):
    llm.payload = selection([(1, "Abstract", 1)]).model_dump_json()
    assert generate(paper)[0]["page"] == 1
    llm.payload = '{"entries":[]}'
    assert generate(paper) == []


def test_tree_without_cleanup_keeps_same_level_headings_flat(paper):
    paper.ocr["pages"][0]["markdown"] = "### One\n### Two\n### Three"
    tree = outline.build_tree(candidates_of(paper))
    assert [e["title"] for e in tree[:3]] == ["One", "Two", "Three"]


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
            "k", "http://proxy/v1", "gpt-6-astra", "gpt-5.4-mini"
        ),
    }
    specs = [
        ModelSpec(id="gpt-5.4-mini", provider=LLMProvider.OPENAI, display_name="mini")
    ]
    # Even while the codex proxy is the default provider.
    registry = ModelRegistry(specs, configs, LLMProvider.CODEX_PROXY)
    spec = resolve_slot("ingest.outline", registry).spec
    assert (spec.provider, spec.id) == (LLMProvider.OPENAI, "gpt-5.4-mini")


def test_missing_blocks_still_produce_page_targets(paper):
    paper.ocr["pages"][0]["blocks"] = None
    candidates = candidates_of(paper)
    assert len(candidates) == 5
    assert candidates[0]["page"] == 1
    assert candidates[0]["top_percent"] is None


def test_large_outlines_skip_llm_without_losing_headings(paper, llm):
    paper.ocr["pages"][0]["markdown"] = "\n".join(f"# Topic {i}" for i in range(301))
    paper.ocr["pages"] = paper.ocr["pages"][:1]
    assert len(generate(paper)) == 301
    assert llm.calls == []


@pytest.fixture
def api(monkeypatch, paper):
    app = FastAPI()
    app.include_router(paper_router)
    user = SimpleNamespace(id=uuid.uuid4())
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_db] = lambda: None
    paper.generated_outline = [
        {"title": "Methods", "level": 1, "page": 3, "top_percent": 50}
    ]
    get = Mock(return_value=paper)
    monkeypatch.setattr(paper_detail.paper_crud, "get", get)
    return SimpleNamespace(
        client=TestClient(app), app=app, get=get, user=user, paper=paper
    )


def test_owner_endpoint_passes_user_and_displayed_paper_id(api):
    response = api.client.get(f"/api/paper/outline?id={api.paper.id}")
    assert response.status_code == 200
    assert response.json()[0]["top_percent"] == 50
    api.get.assert_called_once_with(None, id=api.paper.id, user=api.user)


def test_outline_not_built_yet_is_empty(api):
    api.paper.generated_outline = None
    response = api.client.get(f"/api/paper/outline?id={api.paper.id}")
    assert response.status_code == 200 and response.json() == []


def test_private_endpoint_requires_auth(api):
    api.app.dependency_overrides[get_current_user] = lambda: None
    assert api.client.get(f"/api/paper/outline?id={api.paper.id}").status_code == 401


def test_private_endpoint_rejects_nonowner_and_bad_id(api):
    api.get.return_value = None
    assert api.client.get(f"/api/paper/outline?id={api.paper.id}").status_code == 404
    assert api.client.get("/api/paper/outline?id=invalid").status_code == 422


def test_no_public_share_outline_endpoint(api):
    api.app.dependency_overrides[get_current_user] = lambda: None
    assert api.client.get("/api/paper/share/outline?id=share-token").status_code in (
        404,
        405,
        422,
    )
