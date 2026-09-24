"""`ocr_repair` stage: pick each page's final text.

Ported from `jobs/src/parser_mistral.py` + `openai_ocr_client.py`, same
thresholds and prompt:

1. Score every page's OCR against its text layer (`pdf.quality`):
   ok / unchecked → the OCR text stands (`markdown_source='ocr'`).
2. Suspect pages: render the page (220 DPI) and re-OCR it with the
   `ingest.ocr_repair` vision model. A repair that scores well replaces the
   page (`'ocr_repair'`, also kept in `repair_markdown`); if it fails or
   scores poorly, the page falls back to its text layer (`'text_layer'`),
   or keeps the OCR text when the text layer is empty.

A failed repair never fails the stage (per-page fallback, as before).
`ocr_quality` holds the OCR score plus, for suspect pages, `repair:
{model, quality | error}` and `model` when the repair was used.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from dataclasses import dataclass
from typing import Any, Optional

import pymupdf
from pydantic import BaseModel, Field
from pydantic_ai import BinaryContent
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.core.deadline import Deadline
from app.core.errors import classify
from app.ingest import storage
from app.ingest.config import Resource
from app.ingest.models import MarkdownSource, PaperPage
from app.ingest.pdf import quality
from app.ingest.stages.base import Stage, StageContext
from app.llm import oneshot

SLOT = "ingest.ocr_repair"
REPAIR_DPI = int(os.environ.get("OPENAI_OCR_REPAIR_DPI", "220"))
REPAIR_TIMEOUT_S = 120.0
# Suspect pages of one paper re-OCR'd at once.
REPAIR_PARALLEL = 4
# Repairs stop this long before the stage's budget runs out; the pages not
# repaired by then fall back, so a paper with many suspect pages still
# finishes instead of timing out and redoing every repair on retry.
REPAIR_MARGIN_S = 30.0

REPAIR_PROMPT = (
    "OCR this PDF page into markdown. Preserve reading "
    "order, headings, paragraphs, footnotes, tables, "
    "equations, figure captions, and image placeholders "
    "when visible. Return only JSON matching the schema."
)


class OcrPageText(BaseModel):
    """The repair model's answer (a `final_result` tool call)."""

    markdown: str = Field(description="OCR markdown for the page.")


@dataclass(frozen=True)
class PageInput:
    page_no: int
    ocr_markdown: str
    text_layer: str


@dataclass(frozen=True)
class PageText:
    page_no: int
    markdown: str
    markdown_source: MarkdownSource
    ocr_quality: dict[str, Any]
    repair_markdown: Optional[str] = None


def render_page_png(pdf_bytes: bytes, page_no: int, dpi: int) -> bytes:
    """A 1-based page as PNG bytes (picklable, process pool)."""
    doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    try:
        if not 1 <= page_no <= len(doc):
            raise ValueError(f"Page {page_no} out of range")
        pix = doc[page_no - 1].get_pixmap(dpi=dpi, alpha=False)
        return pix.tobytes("png")
    finally:
        doc.close()


async def reocr_page(png: bytes) -> str:
    """Vision OCR of one rendered page on the `ingest.ocr_repair` model."""
    out = await oneshot.complete(
        SLOT,
        [
            REPAIR_PROMPT,
            BinaryContent(
                data=png, media_type="image/png", vendor_metadata={"detail": "high"}
            ),
        ],
        output_type=OcrPageText,
    )
    return out.markdown.replace("\x00", "").strip()


def load_pages(session: Session, paper_id: uuid.UUID) -> list[PageInput]:
    rows = session.execute(
        select(PaperPage.page_no, PaperPage.ocr_markdown, PaperPage.text_layer)
        .where(PaperPage.paper_id == paper_id)
        .order_by(PaperPage.page_no)
    )
    return [
        PageInput(page_no=no, ocr_markdown=ocr or "", text_layer=text or "")
        for no, ocr, text in rows
    ]


def fallback(page: PageInput, ocr_quality: dict[str, Any]) -> PageText:
    """A suspect page whose repair didn't work out: text layer, else OCR."""
    text = page.text_layer.strip()
    if text:
        return PageText(page.page_no, text, MarkdownSource.TEXT_LAYER, ocr_quality)
    return PageText(page.page_no, page.ocr_markdown, MarkdownSource.OCR, ocr_quality)


class OcrRepair(Stage[list[PageText]]):
    name = "ocr_repair"
    needs = ("ocr", "text_layer")
    resource = Resource.LLM
    timeout_s = 900.0
    model_slot = SLOT
    applies_to_supplementary = True

    async def run(self, ctx: StageContext) -> list[PageText]:
        pages = await ctx.read(lambda s: load_pages(s, ctx.paper_id))
        results: list[PageText] = []
        suspect: list[tuple[PageInput, dict[str, Any]]] = []
        for page in pages:
            scored = quality.page_quality(page.ocr_markdown, page.text_layer)
            if scored["status"] == quality.SUSPECT:
                suspect.append((page, scored))
            else:
                results.append(
                    PageText(
                        page.page_no, page.ocr_markdown, MarkdownSource.OCR, scored
                    )
                )

        await ctx.progress(0, len(suspect))
        if suspect:
            ctx.log.info("%s of %s pages look suspect", len(suspect), len(pages))
            self.resolve_model(ctx)
            model = ctx.model_used
            pdf = await storage.load_pdf(ctx)
            gate = asyncio.Semaphore(REPAIR_PARALLEL)
            finished = 0
            budget = Deadline(max(0.0, ctx.deadline.remaining() - REPAIR_MARGIN_S))

            async def repair(page: PageInput, scored: dict[str, Any]) -> PageText:
                nonlocal finished
                async with gate:
                    result = await self._repair(ctx, budget, pdf, page, scored, model)
                finished += 1
                await ctx.progress(finished, len(suspect))
                return result

            results += await asyncio.gather(*(repair(p, q) for p, q in suspect))

        return sorted(results, key=lambda r: r.page_no)

    async def _repair(
        self,
        ctx: StageContext,
        budget: Deadline,
        pdf: bytes,
        page: PageInput,
        scored: dict[str, Any],
        model: Optional[str],
    ) -> PageText:
        ocr_quality = dict(scored)
        try:
            budget.check()  # out of time: fall back without rendering
            png: bytes = await ctx.cpu(render_page_png, pdf, page.page_no, REPAIR_DPI)
            async with asyncio.timeout(budget.timeout(REPAIR_TIMEOUT_S)):
                markdown = await reocr_page(png)
        except Exception as exc:
            message = classify(exc).message
            ctx.log.warning(
                "OCR repair failed for page %s; using the text layer: %s",
                page.page_no,
                message,
            )
            ocr_quality["repair"] = {"model": model, "error": message}
            return fallback(page, ocr_quality)

        repaired = quality.page_quality(markdown, page.text_layer)
        ocr_quality["repair"] = {"model": model, "quality": repaired}
        if quality.repair_is_good(markdown, repaired):
            ctx.log.info(
                "Repaired page %s (recall=%s)",
                page.page_no,
                repaired.get("pymupdf_token_recall"),
            )
            ocr_quality["model"] = model
            return PageText(
                page.page_no,
                markdown,
                MarkdownSource.OCR_REPAIR,
                ocr_quality,
                repair_markdown=markdown,
            )
        ctx.log.warning(
            "OCR repair of page %s scored poorly (recall=%s); using the text layer",
            page.page_no,
            repaired.get("pymupdf_token_recall"),
        )
        return fallback(page, ocr_quality)

    def save(self, session: Session, ctx: StageContext, output: list[PageText]) -> None:
        for page in output:
            session.execute(
                update(PaperPage)
                .where(
                    PaperPage.paper_id == ctx.paper_id,
                    PaperPage.page_no == page.page_no,
                )
                .values(
                    markdown=page.markdown,
                    markdown_source=page.markdown_source,
                    ocr_quality=page.ocr_quality,
                    repair_markdown=page.repair_markdown,
                )
            )
