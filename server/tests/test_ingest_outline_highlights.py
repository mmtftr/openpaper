"""The `outline` and `highlights` ingest stages with a fake model and no DB.

The model is a pydantic-ai `FunctionModel` behind the real one-shot path
(`oneshot.complete`, tool output); the PDF is generated in the test; DB reads
are replaced by the stages' loader functions.
"""

import asyncio
import uuid
from unittest.mock import Mock

import pymupdf
import pytest
from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from app.core.deadline import Deadline
from app.ingest.stages import highlights as highlights_stage
from app.ingest.stages import outline as outline_stage
from app.ingest.stages.base import StageContext, StageSkipped
from app.llm import model_slots, oneshot
from app.llm._pai_compat import attach_transport_closer
from app.llm.model_registry import LLMProvider, ModelSpec
from app.llm.paper_outline import OutlinePage


class FakeRegistry:
    """Every slot resolves to one spec; the model is `FunctionModel(fn)`."""

    def __init__(self, fn):
        self.fn = fn
        self.spec = ModelSpec(
            id="gpt-5.5", provider=LLMProvider.OPENAI, display_name="m"
        )
        self.calls = 0

    def resolve(self, provider=None, model_id=None, role=None):
        return self.spec

    def build_model(self, spec):
        self.calls += 1

        async def close():
            pass

        return attach_transport_closer(FunctionModel(self.fn), close)

    def build_settings(self, spec, reasoning_effort=None, *, cache_key=None):
        return None


@pytest.fixture
def model(monkeypatch):
    """Install a fake model: set `.fn(messages, info) -> ModelResponse`."""
    registry = FakeRegistry(lambda messages, info: pytest.fail("no LLM call expected"))
    monkeypatch.setattr(oneshot, "get_registry", lambda: registry)
    monkeypatch.setattr(model_slots, "get_registry", lambda: registry)
    return registry


def answer(payload):
    def fn(messages, info: AgentInfo):
        answer.seen = messages  # type: ignore[attr-defined]
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, payload)])

    return fn


def make_ctx(stage):
    return StageContext(
        paper_id=uuid.uuid4(),
        stage=stage,
        attempt=1,
        is_supplementary=False,
        deadline=Deadline(60),
        session_factory=Mock,  # reads go through the patched loaders
    )


def pdf_with(pages: int = 3, toc=None) -> bytes:
    doc = pymupdf.open()
    for i in range(pages):
        doc.new_page().insert_text((72, 100), f"Page {i + 1} text.", fontsize=11)
    if toc:
        doc.set_toc(toc)
    return doc.tobytes()


def serve_pdf(monkeypatch, module, pdf: bytes):
    async def load(ctx):
        return pdf

    monkeypatch.setattr(module.storage, "load_pdf", load)


# -- outline -------------------------------------------------------------------------


def test_outline_uses_pdf_bookmarks_without_a_model_call(monkeypatch, model):
    goto = pymupdf.LINK_GOTO
    toc = [
        [
            1,
            "1  Introduction",
            1,
            {"kind": goto, "page": 0, "to": pymupdf.Point(0, 421)},
        ],
        [
            2,
            "1.1 Background",
            2,
            {"kind": goto, "page": 1, "to": pymupdf.Point(0, 84.2)},
        ],
        [1, "References", 3],
    ]
    serve_pdf(monkeypatch, outline_stage, pdf_with(3, toc))
    monkeypatch.setattr(
        outline_stage,
        "load_outline_pages",
        lambda s, paper_id: pytest.fail("OCR headings not needed"),
    )
    ctx = make_ctx("outline")

    result = asyncio.run(outline_stage.Outline().run(ctx))

    assert [e["title"] for e in result] == ["1 Introduction", "References"]
    intro = result[0]
    assert (intro["page"], intro["level"], intro["top_percent"]) == (1, 1, 50.0)
    assert intro["children"][0] == {
        "title": "1.1 Background",
        "level": 2,
        "page": 2,
        "top_percent": 10.0,
        "children": [],
    }
    assert result[1]["page"] == 3
    assert model.calls == 0 and ctx.model_used is None


def test_named_bookmark_destinations_are_flipped_to_top_left():
    """hyperref's bookmarks use named destinations, whose `to` pymupdf leaves
    in PDF (bottom-left) coordinates; explicit ones it converts."""
    doc = pymupdf.open()
    for _ in range(2):
        doc.new_page()  # 842 pt high
    doc.set_toc([[1, "Named", 2], [1, "Explicit", 1]])
    # /XYZ 0 642 = 200 pt below the top of page 2.
    dest = f"[{doc[1].xref} 0 R /XYZ 0 642 0]"
    doc.xref_set_key(doc.pdf_catalog(), "Dests", f"<< /section.1 {dest} >>")
    doc.xref_set_key(doc.get_outline_xrefs()[0], "A", "<< /S /GoTo /D /section.1 >>")
    pdf = doc.tobytes()
    with pymupdf.open(stream=pdf, filetype="pdf") as check:
        assert check.get_toc(simple=False)[0][3]["kind"] == pymupdf.LINK_NAMED

    named, explicit = outline_stage.read_bookmarks(pdf)
    assert (named["page"], named["top_percent"]) == (2, round(100 * 200 / 842, 3))
    assert explicit["page"] == 1 and explicit["top_percent"] < 10


def test_outline_without_bookmarks_cleans_ocr_headings(monkeypatch, model):
    serve_pdf(monkeypatch, outline_stage, pdf_with(3))
    pages = [
        OutlinePage(
            page=1,
            markdown="# A Paper\nby someone\n# Abstract\ntext\n# 1 Introduction\n",
            blocks=[
                {
                    "type": "title",
                    "content": "# 1 Introduction",
                    "top_left_y": 500,
                    "bottom_right_y": 520,
                }
            ],
            dimensions={"height": 1000},
        ),
        OutlinePage(page=3, markdown="# 1.1 Setup\ntext"),
    ]
    monkeypatch.setattr(outline_stage, "load_outline_pages", lambda s, paper_id: pages)
    model.fn = answer(
        {
            "entries": [
                {"candidate_id": 1, "title": "Abstract", "level": 1},
                {"candidate_id": 2, "title": "1. Introduction", "level": 1},
                {"candidate_id": 3, "title": "1.1 Setup", "level": 2},
            ]
        }
    )
    ctx = make_ctx("outline")

    result = asyncio.run(outline_stage.Outline().run(ctx))

    assert [e["title"] for e in result] == ["Abstract", "1. Introduction"]
    assert result[1]["top_percent"] == 50
    assert result[1]["children"][0]["page"] == 3
    assert ctx.model_used == "openai/gpt-5.5"
    prompt = answer.seen[0].parts[-1].content  # type: ignore[attr-defined]
    assert '"title": "A Paper"' in prompt  # candidates go to the model as JSON


def test_outline_with_no_headings_is_empty_without_a_model_call(monkeypatch, model):
    serve_pdf(monkeypatch, outline_stage, pdf_with(1))
    monkeypatch.setattr(
        outline_stage,
        "load_outline_pages",
        lambda s, paper_id: [OutlinePage(page=1, markdown="no headings here")],
    )
    assert asyncio.run(outline_stage.Outline().run(make_ctx("outline"))) == []
    assert model.calls == 0


def test_outline_model_failure_raises_for_the_stage_retry(monkeypatch, model):
    serve_pdf(monkeypatch, outline_stage, pdf_with(1))
    monkeypatch.setattr(
        outline_stage,
        "load_outline_pages",
        lambda s, paper_id: [OutlinePage(page=1, markdown="# Methods\n")],
    )

    def down(messages, info):
        raise ConnectionError("provider down")

    model.fn = down
    with pytest.raises(ConnectionError):
        asyncio.run(outline_stage.Outline().run(make_ctx("outline")))


def test_outline_pages_come_from_final_markdown_and_ocr_layout():
    session = Mock()
    session.execute.return_value.all.return_value = [
        (
            1,
            "# Intro",
            {"blocks": [{"type": "title"}, "junk"], "dimensions": {"height": 9}},
        ),
        (2, "text only", None),
    ]
    pages = outline_stage.load_outline_pages(session, uuid.uuid4())
    assert pages == [
        OutlinePage(1, "# Intro", [{"type": "title"}], {"height": 9}),
        OutlinePage(2, "text only", [], {}),
    ]


def test_outline_save_writes_generated_outline():
    session = Mock()
    ctx = make_ctx("outline")
    outline_stage.Outline().save(session, ctx, [{"title": "A"}])
    [stmt] = session.execute.call_args.args
    assert stmt.table.name == "papers"
    assert stmt.compile().params["generated_outline"] == [{"title": "A"}]
    session.commit.assert_not_called()


# -- highlights ------------------------------------------------------------------------

PAGE_1 = "Background on the topic.\n\nWe find that sparse attention halves the cost."
PAGE_2 = "## Results\n\nAccuracy improves by **12 points** on every benchmark."


def highlights_pdf() -> bytes:
    doc = pymupdf.open()
    for lines in (
        ["Background on the topic.", "We find that sparse attention halves the cost."],
        ["Results", "Accuracy improves by 12 points on every benchmark."],
    ):
        page = doc.new_page()
        for i, line in enumerate(lines):
            page.insert_text((72, 100 + 14 * i), line, fontsize=11)
    return doc.tobytes()


def pick(text, kind="result", note="Why it matters."):
    return {"text": text, "annotation": note, "type": kind}


@pytest.fixture
def paper_text(monkeypatch):
    monkeypatch.setattr(
        highlights_stage,
        "load_page_markdown",
        lambda s, paper_id: [(1, PAGE_1), (2, PAGE_2)],
    )
    serve_pdf(monkeypatch, highlights_stage, highlights_pdf())


def test_highlights_are_picked_anchored_and_placed(paper_text, model):
    model.fn = answer(
        {
            "highlights": [
                pick("sparse attention halves the cost", "method"),
                pick("Accuracy improves by **12 points** on every benchmark."),
                pick("A paraphrase that is not in the paper at all", "impact"),
            ]
        }
    )
    ctx = make_ctx("highlights")

    out = asyncio.run(highlights_stage.Highlights().run(ctx))

    assert ctx.model_used == "openai/gpt-5.5"
    request = answer.seen[0]  # type: ignore[attr-defined]
    assert request.instructions == highlights_stage.HIGHLIGHTS_PROMPT
    assert request.parts[-1].content.startswith("Paper Content:\n\nBackground")

    method, result, paraphrase = out
    assert (method.type, method.page_number) == ("method", 1)
    assert method.position is not None
    assert method.position["rects"][0]["pageNumber"] == 1
    text, _ = highlights_stage.join_pages([(1, PAGE_1), (2, PAGE_2)])
    assert text[method.start_offset : method.end_offset] == method.text

    assert result.page_number == 2 and result.position is not None
    assert result.text == "Accuracy improves by **12 points** on every benchmark."

    # Not in the PDF: stored without a position, like the old pipeline.
    assert paraphrase.position is None
    assert paraphrase.annotation == "Why it matters."


def test_highlights_are_capped_at_five(paper_text, model):
    model.fn = answer(
        {"highlights": [pick(f"sparse attention {i}") for i in range(7)] + [pick("  ")]}
    )
    out = asyncio.run(highlights_stage.Highlights().run(make_ctx("highlights")))
    assert len(out) == highlights_stage.MAX_HIGHLIGHTS


def test_highlights_skip_a_paper_without_text(monkeypatch, model):
    monkeypatch.setattr(
        highlights_stage, "load_page_markdown", lambda s, paper_id: [(1, ""), (2, "")]
    )
    with pytest.raises(StageSkipped):
        asyncio.run(highlights_stage.Highlights().run(make_ctx("highlights")))
    assert model.calls == 0


def test_join_pages_matches_the_old_raw_content_shape():
    text, offsets = highlights_stage.join_pages([(1, "ab"), (2, ""), (3, "cd")])
    assert text == "ab\n\ncd"
    assert offsets == {1: (0, 2), 3: (4, 6)}


def test_position_is_validated_as_scaled_position():
    picks = [highlights_stage.AIHighlight.model_validate(pick("x y z w"))]
    bad = {"page_number": 1, "position": {"boundingRect": {"x1": 0}, "rects": []}}
    with pytest.raises(ValueError):
        highlights_stage.place_highlights(picks, [bad], "x y z w", {1: (0, 7)})
    [good] = highlights_stage.place_highlights(picks, [None], "x y z w", {1: (0, 7)})
    assert (good.page_number, good.start_offset, good.end_offset) == (1, 0, 7)
