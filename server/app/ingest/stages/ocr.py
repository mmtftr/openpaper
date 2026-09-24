"""`ocr` stage: Mistral OCR in page batches, saved as each batch lands.

- The PDF is split into `ocr_config().batch_pages`-page PDFs (16), OCR'd
  up to `BATCH_PARALLEL` at a time (`sources.mistral`); each request gets a
  couple of quick in-call retries (`core.retry.retry_call`), anything longer
  (e.g. a 429 with a long Retry-After) fails the attempt and becomes a
  stage retry.
- Each batch is written as soon as it lands, in its own short transaction
  (`ctx.write`): the pages' `paper_pages.ocr_markdown` + `ocr_payload`
  (upsert — `text_layer` fills other columns of the same rows) and the
  batch's image boxes as `paper_figures` rows. Progress = pages done / total.
- Every attempt (a crash, a stage retry, the owner's Retry) only OCRs the
  pages that aren't saved yet. A reprocess starts from scratch: it calls
  `reset_outputs()`, which forgets the saved OCR first.
- `save()` has nothing left to write.

Model: `ocr_config().model` (service config, not a model slot).
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from typing import Optional, Sequence

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.core.errors import TemporaryError
from app.core.http import shared_client
from app.core.retry import retry_call
from app.ingest import storage
from app.ingest.config import Resource, ocr_config
from app.ingest.models import PaperFigure, PaperPage
from app.ingest.pdf.document import page_widths
from app.ingest.sources import mistral
from app.ingest.stages.base import Stage, StageContext

# Batches of one paper OCR'd at once (on top of the OCR resource limit).
BATCH_PARALLEL = 2


@dataclass(frozen=True)
class OcrOutput:
    pages_total: int
    pages_ocred: int  # by this attempt (earlier attempts saved the rest)


# -- persistence ---------------------------------------------------------------


def saved_pages(session: Session, paper_id: uuid.UUID) -> set[int]:
    rows = session.execute(
        select(PaperPage.page_no).where(
            PaperPage.paper_id == paper_id, PaperPage.ocr_markdown.is_not(None)
        )
    )
    return {row[0] for row in rows}


def clear_ocr(session: Session, paper_id: uuid.UUID) -> None:
    """Forget a previous run's OCR (a reprocess starts from scratch).

    Figure rows go too; their rendered images stay in S3 (chat history may
    point at them)."""
    session.execute(
        update(PaperPage)
        .where(PaperPage.paper_id == paper_id)
        .values(ocr_markdown=None, ocr_payload=None)
    )
    session.execute(
        PaperFigure.__table__.delete().where(PaperFigure.paper_id == paper_id)
    )


def save_batch(
    session: Session, paper_id: uuid.UUID, pages: Sequence[mistral.OcrPage]
) -> None:
    """Upsert one batch's pages and figure boxes."""
    if not pages:
        return
    page_rows = [
        {
            "paper_id": paper_id,
            "page_no": p.page_no,
            "ocr_markdown": p.markdown,
            "ocr_payload": p.payload,
        }
        for p in pages
    ]
    stmt = insert(PaperPage.__table__).values(page_rows)
    session.execute(
        stmt.on_conflict_do_update(
            index_elements=["paper_id", "page_no"],
            set_={
                "ocr_markdown": stmt.excluded.ocr_markdown,
                "ocr_payload": stmt.excluded.ocr_payload,
                "updated_at": func.now(),
            },
        )
    )

    figure_rows = [
        {
            "id": uuid.uuid4(),
            "paper_id": paper_id,
            "page_no": f.page_no,
            "ocr_image_id": f.ocr_image_id,
            "label": f.label,
            "caption": f.caption,
            "bbox": f.bbox,
        }
        for p in pages
        for f in p.figures
    ]
    if figure_rows:
        fstmt = insert(PaperFigure.__table__).values(figure_rows)
        session.execute(
            fstmt.on_conflict_do_update(
                constraint="uq_paper_figures_image",
                set_={
                    "label": fstmt.excluded.label,
                    "caption": fstmt.excluded.caption,
                    "bbox": fstmt.excluded.bbox,
                    "updated_at": func.now(),
                },
            )
        )


def batches(pages: Sequence[int], size: int) -> list[list[int]]:
    return [list(pages[i : i + size]) for i in range(0, len(pages), size)]


# -- the stage -----------------------------------------------------------------


class Ocr(Stage[OcrOutput]):
    name = "ocr"
    needs = ("source",)
    resource = Resource.OCR
    timeout_s = 900.0
    applies_to_supplementary = True

    def check_config(self) -> None:
        ocr_config().require()

    async def run(self, ctx: StageContext) -> OcrOutput:
        config = ocr_config().require()
        ctx.model_used = config.model
        pdf = await storage.load_pdf(ctx)
        widths: dict[int, float] = await ctx.cpu(page_widths, pdf)
        total = len(widths)

        done = await ctx.read(lambda s: saved_pages(s, ctx.paper_id))
        missing = [p for p in range(1, total + 1) if p not in done]
        await ctx.progress(total - len(missing), total)
        if missing:
            ctx.log.info(
                "OCR %s of %s pages in batches of %s",
                len(missing),
                total,
                config.batch_pages,
            )

        client = shared_client()
        gate = asyncio.Semaphore(BATCH_PARALLEL)
        ocred = 0

        async def run_batch(page_nos: list[int]) -> None:
            nonlocal ocred
            async with gate:
                sub_pdf: bytes = await ctx.cpu(mistral.split_pdf, pdf, page_nos)
                label = f"Mistral OCR pages {page_nos[0]}-{page_nos[-1]}"
                body = await retry_call(
                    lambda: mistral.request_ocr(
                        sub_pdf,
                        config,
                        client,
                        timeout=ctx.deadline.timeout(mistral.REQUEST_TIMEOUT_S),
                    ),
                    deadline=ctx.deadline,
                    what=label,
                )
                pages = mistral.parse_pages(body, page_nos, widths)
                await ctx.write(lambda s: save_batch(s, ctx.paper_id, pages))
                landed = {p.page_no for p in pages}
                done.update(landed)
                ocred += len(landed)
                await ctx.progress(len(done), total)
                lost = [p for p in page_nos if p not in landed]
                if lost:
                    raise TemporaryError(
                        f"{label}: no result for page(s) {', '.join(map(str, lost))}"
                    )

        results = await asyncio.gather(
            *(run_batch(b) for b in batches(missing, config.batch_pages)),
            return_exceptions=True,
        )
        # Every batch that could land has been saved; now surface a failure.
        first: Optional[BaseException] = next(
            (r for r in results if isinstance(r, Exception)),
            next((r for r in results if isinstance(r, BaseException)), None),
        )
        if first is not None:
            raise first
        return OcrOutput(pages_total=total, pages_ocred=ocred)

    def save(self, session: Session, ctx: StageContext, output: OcrOutput) -> None:
        """Nothing to do: every batch was saved as it landed."""

    def reset_outputs(self, session: Session, paper_id: uuid.UUID) -> None:
        clear_ocr(session, paper_id)
