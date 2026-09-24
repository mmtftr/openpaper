"""Ingest `ocr` + `ocr_repair`: Mistral request/response handling, per-batch
saving and resume, quality scoring, vision repair with per-page fallback.

No database and no network: stages run on a fake `StageContext` (the DB
helpers they call are swapped for recorders), Mistral is an
`httpx.MockTransport` answering with responses shaped like real ones, and
the repair model is a pydantic-ai `FunctionModel`.
"""

from __future__ import annotations

import asyncio
import base64
import json
import uuid
from dataclasses import replace
from typing import Any

import httpx
import pymupdf
import pytest
from pydantic_ai import BinaryContent
from pydantic_ai.messages import ModelResponse, ToolCallPart, UserPromptPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from sqlalchemy.dialects import postgresql

from app.core.deadline import Deadline
from app.core.errors import ErrorKind, classify
from app.ingest.config import OcrConfig
from app.ingest.models import MarkdownSource
from app.ingest.pdf import quality
from app.ingest.sources import mistral
from app.ingest.stages import ocr as ocr_stage
from app.ingest.stages import ocr_repair as repair_stage
from app.ingest.stages.base import StageContext
from app.llm import model_slots, oneshot
from app.llm._pai_compat import attach_transport_closer
from app.llm.model_registry import LLMProvider, ModelSpec

PAPER = uuid.UUID("00000000-0000-0000-0000-00000000abcd")
CONFIG = OcrConfig(
    api_key="test-key",
    endpoint="https://mistral.test/v1/ocr",
    model="mistral-ocr-latest",
)

WORDS = (
    "transformer attention gradient descent parameter embedding residual "
    "network layer neuron feature activation optimizer benchmark dataset "
    "evaluation baseline ablation training inference sparse dictionary"
)


def page_text(page_no: int) -> str:
    return f"Page{page_no:03d} marker. {WORDS} {WORDS}"


def make_pdf(pages: int) -> bytes:
    doc = pymupdf.open()
    for n in range(1, pages + 1):
        page = doc.new_page(width=612, height=792)
        page.insert_textbox(pymupdf.Rect(72, 72, 540, 720), page_text(n), fontsize=11)
    data = doc.tobytes()
    doc.close()
    return data


# -- a Mistral stand-in ---------------------------------------------------------


def mistral_page(index: int, markdown: str, *, with_image: bool) -> dict[str, Any]:
    """One page object as Mistral returns it (fields seen in stored OCR)."""
    images = []
    if with_image:
        images.append(
            {
                "id": "img-0.jpeg",
                "top_left_x": 186,
                "top_left_y": 93,
                "bottom_right_x": 372,
                "bottom_right_y": 279,
                "image_base64": None,
                "image_annotation": None,
            }
        )
    return {
        "index": index,
        "markdown": markdown,
        "images": images,
        "tables": [],
        "hyperlinks": [],
        "header": None,
        "footer": None,
        "dimensions": {"dpi": 93, "height": 1023, "width": 791},
        "confidence_scores": None,
        "blocks": [
            {
                "type": "text",
                "content": markdown[:40],
                "top_left_x": 90,
                "top_left_y": 90,
                "bottom_right_x": 700,
                "bottom_right_y": 300,
                "confidence_scores": None,
            }
        ],
    }


class FakeMistral:
    """Answers each OCR request by reading the batch PDF it was sent."""

    def __init__(self, fail: dict[int, httpx.Response] | None = None) -> None:
        self.requests: list[dict[str, Any]] = []
        self.batches: list[list[int]] = []
        self.fail = fail or {}  # call number (0-based) -> response

    def handler(self, request: httpx.Request) -> httpx.Response:
        call = len(self.requests)
        body = json.loads(request.content)
        self.requests.append({"body": body, "headers": dict(request.headers)})
        if call in self.fail:
            return self.fail[call]
        url = body["document"]["document_url"]
        prefix = "data:application/pdf;base64,"
        assert url.startswith(prefix)
        doc = pymupdf.open(stream=base64.b64decode(url[len(prefix) :]), filetype="pdf")
        pages, page_nos = [], []
        for i in range(doc.page_count):
            text = str(doc[i].get_text("text"))
            page_no = int(text.split("Page", 1)[1][:3])
            page_nos.append(page_no)
            markdown = f"![img-0.jpeg](img-0.jpeg)\nFigure {page_no}: A plot.\n\n{page_text(page_no)}\x00"
            pages.append(mistral_page(i, markdown, with_image=page_no % 2 == 1))
        doc.close()
        self.batches.append(page_nos)
        return httpx.Response(
            200,
            json={
                "pages": pages,
                "model": "mistral-ocr-2512",
                "document_annotation": None,
                "usage_info": {"pages_processed": len(pages), "doc_size_bytes": 1234},
            },
        )

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self.handler))


# -- a fake StageContext --------------------------------------------------------


class FakeSession:
    def execute(self, *args: Any, **kwargs: Any) -> None:
        return None

    def commit(self) -> None: ...
    def rollback(self) -> None: ...
    def close(self) -> None: ...


def make_ctx(
    stage: str, attempt: int = 1, budget: float = 900
) -> tuple[StageContext, list]:
    progress: list[tuple[int, int]] = []

    async def report(done: int, total: int) -> None:
        progress.append((done, total))

    ctx = StageContext(
        paper_id=PAPER,
        stage=stage,
        attempt=attempt,
        is_supplementary=False,
        deadline=Deadline(budget),
        report_progress=report,
        session_factory=FakeSession,  # type: ignore[arg-type]
    )
    return ctx, progress


@pytest.fixture
def ocr_env(monkeypatch):
    """Wire the ocr stage to a 20-page PDF, a fake Mistral, and recorders."""
    pdf = make_pdf(20)
    fake = FakeMistral()
    state: dict[str, Any] = {"saved": [], "cleared": 0, "already": set()}

    async def load_pdf(ctx):
        return pdf

    def save_batch(session, paper_id, pages):
        state["saved"].append(list(pages))

    def clear(session, paper_id):
        state["cleared"] += 1

    monkeypatch.setattr(ocr_stage.storage, "load_pdf", load_pdf)
    monkeypatch.setattr(ocr_stage, "save_batch", save_batch)
    monkeypatch.setattr(ocr_stage, "clear_ocr", clear)
    monkeypatch.setattr(ocr_stage, "saved_pages", lambda s, pid: set(state["already"]))
    monkeypatch.setattr(ocr_stage, "ocr_config", lambda: CONFIG)
    client_box: dict[str, httpx.AsyncClient] = {}

    def shared_client():
        client_box["c"] = fake.client()
        return client_box["c"]

    monkeypatch.setattr(ocr_stage, "shared_client", shared_client)
    state["fake"] = fake
    state["pdf"] = pdf
    return state


# -- quality ---------------------------------------------------------------------


def test_quality_ok_suspect_unchecked():
    text = page_text(1)
    ok = quality.page_quality(f"# Heading\n\n{text}", text)
    assert ok["status"] == "ok" and ok["reason"] is None
    assert ok["pymupdf_token_recall"] == 1.0

    bad = quality.page_quality("Completely unrelated hallucinated words here", text)
    assert bad["status"] == "suspect" and bad["reason"] == "low_pymupdf_token_recall"

    short = quality.page_quality("anything", "Figure 3")
    assert short == {
        "status": "unchecked",
        "reason": "insufficient_pymupdf_text",
        "pymupdf_token_count": 1,
        "ocr_token_count": 1,
    }


def test_repair_acceptance_thresholds():
    assert quality.repair_is_good("x", {"status": "ok", "pymupdf_token_recall": 0.2})
    assert quality.repair_is_good("x", {"status": "ok", "common_token_count": 8})
    assert not quality.repair_is_good(
        "x", {"status": "ok", "pymupdf_token_recall": 0.19, "common_token_count": 7}
    )
    assert not quality.repair_is_good("", {"status": "ok", "pymupdf_token_recall": 1})
    assert not quality.repair_is_good(
        "x", {"status": "suspect", "pymupdf_token_recall": 1}
    )


# -- mistral: request / response -------------------------------------------------


def test_split_pdf_keeps_the_requested_pages_in_order():
    sub = pymupdf.open(stream=mistral.split_pdf(make_pdf(5), [2, 4, 5]), filetype="pdf")
    assert [str(sub[i].get_text("text")).split()[0] for i in range(sub.page_count)] == [
        "Page002",
        "Page004",
        "Page005",
    ]


def test_request_body_and_headers():
    fake = FakeMistral()

    async def go():
        async with fake.client() as client:
            return await mistral.request_ocr(
                mistral.split_pdf(make_pdf(2), [1, 2]), CONFIG, client
            )

    body = asyncio.run(go())
    sent = fake.requests[0]
    assert sent["headers"]["authorization"] == "Bearer test-key"
    assert sent["body"]["model"] == "mistral-ocr-latest"
    assert sent["body"]["include_image_base64"] is False
    assert sent["body"]["document"]["type"] == "document_url"
    assert len(body["pages"]) == 2


@pytest.mark.parametrize(
    ("response", "kind", "retry_after"),
    [
        (
            httpx.Response(
                429,
                headers={"Retry-After": "42"},
                json={
                    "object": "error",
                    "message": "Requests rate limit exceeded",
                    "type": "rate_limited",
                    "code": "1300",
                },
            ),
            ErrorKind.RATE_LIMITED,
            42.0,
        ),
        (httpx.Response(401, json={"message": "Unauthorized"}), ErrorKind.CONFIG, None),
        (httpx.Response(503, text="upstream connect error"), ErrorKind.TEMPORARY, None),
        (
            httpx.Response(
                422, json={"object": "error", "message": "Invalid document"}
            ),
            ErrorKind.PERMANENT,
            None,
        ),
    ],
)
def test_http_errors_classify(response, kind, retry_after):
    async def go():
        client = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: response))
        async with client:
            await mistral.request_ocr(b"%PDF", CONFIG, client)

    with pytest.raises(mistral.MistralHTTPError) as info:
        asyncio.run(go())
    classified = classify(info.value)
    assert classified.kind is kind
    assert classified.retry_after == retry_after
    assert f"HTTP {response.status_code}" in classified.message


def test_malformed_success_is_permanent():
    async def go(resp):
        client = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: resp))
        async with client:
            await mistral.request_ocr(b"%PDF", CONFIG, client)

    for resp in (
        httpx.Response(200, text="<html>"),
        httpx.Response(200, json={"x": 1}),
    ):
        with pytest.raises(Exception) as info:
            asyncio.run(go(resp))
        assert classify(info.value).kind is ErrorKind.PERMANENT


def test_parse_pages_maps_batch_index_and_converts_boxes():
    body = {
        "pages": [
            mistral_page(0, "Text\x00 only", with_image=False),
            mistral_page(
                1,
                "![img-0.jpeg](img-0.jpeg)\nFigure 3: Loss curves.\n\nTable 1. Results",
                with_image=True,
            ),
        ]
    }
    body["pages"][1]["images"][0]["image_base64"] = "data:image/jpeg;base64,AAAA"
    pages = mistral.parse_pages(body, [17, 18])

    assert [p.page_no for p in pages] == [17, 18]
    assert pages[0].markdown == "Text only"  # NUL stripped
    payload = pages[1].payload
    assert "markdown" not in payload
    assert payload["index"] == 17  # 0-based in the whole PDF
    assert "image_base64" not in payload["images"][0]
    assert payload["dimensions"]["dpi"] == 93

    (figure,) = pages[1].figures
    assert figure.page_no == 18 and figure.ocr_image_id == "img-0.jpeg"
    assert (figure.label, figure.caption) == ("Figure 3", "Loss curves.")
    # px * 72 / dpi(93)
    assert figure.bbox == {"x0": 144.0, "y0": 72.0, "x1": 288.0, "y1": 216.0}


def test_parse_pages_without_dpi_uses_the_page_width():
    page = mistral_page(0, "x", with_image=True)
    page["dimensions"] = {"width": 1224, "height": 1584}  # 2 px per point
    (parsed,) = mistral.parse_pages({"pages": [page]}, [3], {3: 612.0})
    assert parsed.figures[0].bbox == {"x0": 93.0, "y0": 46.5, "x1": 186.0, "y1": 139.5}


def test_parse_pages_rejects_out_of_range_index():
    with pytest.raises(Exception) as info:
        mistral.parse_pages({"pages": [mistral_page(5, "x", with_image=False)]}, [1, 2])
    assert classify(info.value).kind is ErrorKind.PERMANENT


# -- persistence SQL --------------------------------------------------------------


def test_save_batch_upserts_pages_and_figures():
    statements: list[str] = []

    class Recorder(FakeSession):
        def execute(self, stmt, *args, **kwargs):
            statements.append(str(stmt.compile(dialect=postgresql.dialect())))

    pages = mistral.parse_pages(
        {"pages": [mistral_page(0, "Figure 1: x", with_image=True)]}, [4]
    )
    ocr_stage.save_batch(Recorder(), PAPER, pages)  # type: ignore[arg-type]
    page_sql, figure_sql = statements
    assert "INSERT INTO paper_pages" in page_sql
    assert "ON CONFLICT (paper_id, page_no) DO UPDATE" in page_sql
    assert "text_layer" not in page_sql  # never touches text_layer's columns
    assert "INSERT INTO paper_figures" in figure_sql
    assert "ON CONFLICT ON CONSTRAINT uq_paper_figures_image" in figure_sql
    assert "s3_key" not in figure_sql.split("DO UPDATE")[1]


# -- the ocr stage ------------------------------------------------------------------


def test_ocr_runs_in_batches_saving_each(ocr_env):
    ctx, progress = make_ctx("ocr")
    out = asyncio.run(ocr_stage.Ocr().run(ctx))

    assert out == ocr_stage.OcrOutput(pages_total=20, pages_ocred=20)
    assert ocr_env["cleared"] == 0  # only a reprocess clears (reset_outputs)
    assert sorted(ocr_env["fake"].batches) == [list(range(1, 17)), list(range(17, 21))]
    saved = sorted((p.page_no for batch in ocr_env["saved"] for p in batch))
    assert saved == list(range(1, 21))
    assert progress[0] == (0, 20) and progress[-1] == (20, 20)
    assert ctx.model_used == "mistral-ocr-latest"
    # Figures only on odd pages, page numbers are the whole-PDF ones.
    figs = [f for batch in ocr_env["saved"] for p in batch for f in p.figures]
    assert sorted(f.page_no for f in figs) == list(range(1, 21, 2))
    assert all(f.label == f"Figure {f.page_no}" for f in figs)


@pytest.mark.parametrize("attempt", [1, 2])
def test_ocr_retry_only_redoes_missing_pages(ocr_env, attempt):
    # `retry_stage` resets the attempt counter, so attempt 1 must resume too.
    ocr_env["already"] = set(range(1, 17))
    ctx, progress = make_ctx("ocr", attempt=attempt)
    out = asyncio.run(ocr_stage.Ocr().run(ctx))

    assert ocr_env["cleared"] == 0
    assert ocr_env["fake"].batches == [[17, 18, 19, 20]]
    assert out.pages_ocred == 4
    assert progress == [(16, 20), (20, 20)]


def test_ocr_reset_outputs_clears_saved_ocr(ocr_env):
    ocr_stage.Ocr().reset_outputs(None, uuid.uuid4())  # type: ignore[arg-type]
    assert ocr_env["cleared"] == 1


def test_ocr_rate_limit_keeps_landed_batches_then_raises(ocr_env, monkeypatch):
    monkeypatch.setattr(ocr_stage, "BATCH_PARALLEL", 1)  # batch order is fixed
    ocr_env["fake"].fail = {
        1: httpx.Response(
            429, headers={"Retry-After": "60"}, json={"message": "rate limit"}
        )
    }
    ctx, progress = make_ctx("ocr")
    with pytest.raises(mistral.MistralHTTPError) as info:
        asyncio.run(ocr_stage.Ocr().run(ctx))

    classified = classify(info.value)
    assert classified.kind is ErrorKind.RATE_LIMITED and classified.retry_after == 60
    # The first batch landed and was saved before the failure surfaced; the
    # long Retry-After is not waited out in-call (one request, no retry).
    assert [p.page_no for p in ocr_env["saved"][0]] == list(range(1, 17))
    assert len(ocr_env["fake"].requests) == 2
    assert progress[-1] == (16, 20)


def test_ocr_short_transient_error_is_retried_in_call(ocr_env, monkeypatch):
    monkeypatch.setattr(ocr_stage, "BATCH_PARALLEL", 1)
    ocr_env["fake"].fail = {0: httpx.Response(502, text="bad gateway")}
    ctx, _ = make_ctx("ocr")
    out = asyncio.run(ocr_stage.Ocr().run(ctx))
    assert out.pages_ocred == 20
    assert len(ocr_env["fake"].requests) == 3


def test_ocr_missing_page_in_response_is_temporary(ocr_env, monkeypatch):
    fake = ocr_env["fake"]
    real = fake.handler

    def drop_last(request):
        response = real(request)
        body = response.json()
        body["pages"] = body["pages"][:-1]
        return httpx.Response(200, json=body)

    fake.handler = drop_last  # type: ignore[method-assign]
    ctx, _ = make_ctx("ocr")
    with pytest.raises(Exception) as info:
        asyncio.run(ocr_stage.Ocr().run(ctx))
    assert classify(info.value).kind is ErrorKind.TEMPORARY
    assert "16" in str(info.value) or "20" in str(info.value)
    saved = sorted(p.page_no for batch in ocr_env["saved"] for p in batch)
    assert saved == [*range(1, 16), *range(17, 20)]


# -- oneshot image input ----------------------------------------------------------


class FakeRegistry:
    def __init__(self, fn) -> None:
        self.fn = fn
        self.spec = ModelSpec(
            id="gpt-5.4-mini",
            provider=LLMProvider.OPENAI,
            display_name="m",
            supports_vision=True,
        )

    def resolve(self, provider=None, model_id=None, role=None):
        return self.spec

    def build_model(self, spec):
        async def close():
            return None

        return attach_transport_closer(FunctionModel(self.fn), close)

    def build_settings(self, spec, reasoning_effort=None, *, cache_key=None):
        return None


def use_registry(monkeypatch, fn) -> FakeRegistry:
    fake = FakeRegistry(fn)
    monkeypatch.setattr(oneshot, "get_registry", lambda: fake)
    monkeypatch.setattr(model_slots, "get_registry", lambda: fake)
    return fake


def test_oneshot_accepts_image_parts(monkeypatch):
    seen: dict[str, Any] = {}

    def fn(messages, info: AgentInfo):
        (part,) = [p for p in messages[0].parts if isinstance(p, UserPromptPart)]
        seen["content"] = part.content
        return ModelResponse(
            parts=[ToolCallPart(info.output_tools[0].name, {"markdown": " # Page \n"})]
        )

    use_registry(monkeypatch, fn)
    text = asyncio.run(repair_stage.reocr_page(b"\x89PNG fake"))
    assert text == "# Page"
    prompt, image = seen["content"]
    assert prompt == repair_stage.REPAIR_PROMPT
    assert isinstance(image, BinaryContent)
    assert image.media_type == "image/png" and image.data == b"\x89PNG fake"
    assert image.vendor_metadata == {"detail": "high"}


# -- the ocr_repair stage -------------------------------------------------------------


def repair_inputs() -> list[repair_stage.PageInput]:
    return [
        # 1: OCR fine
        repair_stage.PageInput(1, f"# Title\n{page_text(1)}", page_text(1)),
        # 2: OCR hallucinated; repair will succeed
        repair_stage.PageInput(2, "Lorem ipsum dolor", page_text(2)),
        # 3: OCR hallucinated; repair returns junk -> text layer
        repair_stage.PageInput(3, "Lorem ipsum dolor", page_text(3)),
        # 4: OCR hallucinated; repair call fails -> text layer
        repair_stage.PageInput(4, "Lorem ipsum dolor", page_text(4)),
        # 5: scanned page, no text layer -> unchecked, OCR stands
        repair_stage.PageInput(5, "Scanned text", ""),
    ]


def test_ocr_repair_scores_repairs_and_falls_back(monkeypatch):
    pdf = make_pdf(5)

    async def load_pdf(ctx):
        return pdf

    monkeypatch.setattr(repair_stage.storage, "load_pdf", load_pdf)
    monkeypatch.setattr(repair_stage, "load_pages", lambda s, pid: repair_inputs())

    def fn(messages, info: AgentInfo):
        (part,) = [p for p in messages[0].parts if isinstance(p, UserPromptPart)]
        image = part.content[1]
        assert isinstance(image, BinaryContent)
        page_no = rendered[image.data]  # which page was rendered
        if page_no == 4:
            raise RuntimeError("model exploded")
        markdown = page_text(2) if page_no == 2 else "nothing useful"
        return ModelResponse(
            parts=[ToolCallPart(info.output_tools[0].name, {"markdown": markdown})]
        )

    rendered: dict[bytes, int] = {}

    def render(pdf_bytes, page_no, dpi):
        assert dpi == 220
        png = f"png-{page_no}".encode()
        rendered[png] = page_no
        return png

    monkeypatch.setattr(repair_stage, "render_page_png", render)
    use_registry(monkeypatch, fn)

    ctx, progress = make_ctx("ocr_repair", budget=600)
    out = asyncio.run(repair_stage.OcrRepair().run(ctx))
    by_page = {p.page_no: p for p in out}

    assert [p.page_no for p in out] == [1, 2, 3, 4, 5]
    assert ctx.model_used == "openai/gpt-5.4-mini"
    assert sorted(rendered.values()) == [2, 3, 4]
    assert progress[0] == (0, 3) and progress[-1] == (3, 3)

    assert by_page[1].markdown_source is MarkdownSource.OCR
    assert by_page[1].ocr_quality["status"] == "ok"

    p2 = by_page[2]
    assert p2.markdown_source is MarkdownSource.OCR_REPAIR
    assert p2.markdown == p2.repair_markdown == page_text(2)
    assert p2.ocr_quality["status"] == "suspect"
    assert p2.ocr_quality["model"] == "openai/gpt-5.4-mini"
    assert p2.ocr_quality["repair"]["quality"]["status"] == "ok"

    p3 = by_page[3]
    assert p3.markdown_source is MarkdownSource.TEXT_LAYER
    assert p3.markdown == page_text(3) and p3.repair_markdown is None
    assert p3.ocr_quality["repair"]["quality"]["status"] == "suspect"
    assert "model" not in p3.ocr_quality

    p4 = by_page[4]
    assert p4.markdown_source is MarkdownSource.TEXT_LAYER
    assert "model exploded" in p4.ocr_quality["repair"]["error"]

    p5 = by_page[5]
    assert p5.markdown_source is MarkdownSource.OCR and p5.markdown == "Scanned text"
    assert p5.ocr_quality["status"] == "unchecked"


def test_ocr_repair_without_suspect_pages_makes_no_model_call(monkeypatch):
    inputs = [replace(p, ocr_markdown=p.text_layer) for p in repair_inputs()[:4]]
    monkeypatch.setattr(repair_stage, "load_pages", lambda s, pid: inputs)

    async def no_pdf(ctx):
        raise AssertionError("PDF not needed")

    monkeypatch.setattr(repair_stage.storage, "load_pdf", no_pdf)
    ctx, progress = make_ctx("ocr_repair")
    out = asyncio.run(repair_stage.OcrRepair().run(ctx))
    assert all(p.markdown_source is MarkdownSource.OCR for p in out)
    assert ctx.model_used is None
    assert progress == [(0, 0)]


def test_render_page_png_real_pdf():
    png = repair_stage.render_page_png(make_pdf(2), 2, 72)
    assert png.startswith(b"\x89PNG")
    with pytest.raises(ValueError):
        repair_stage.render_page_png(make_pdf(1), 2, 72)
