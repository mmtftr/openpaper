"""`figures` stage: render the OCR figure boxes at 300 DPI to S3.

Reads the `paper_figures` rows the `ocr` stage created (bbox in PDF points,
top-left origin), renders each from the PDF (`pdf.figures`), uploads it to
`papers/{id}/figures/{figure_id}.png` and sets `s3_key`, `width`, `height`.

A figure whose box can't be rendered (off the page, degenerate) keeps
`s3_key = NULL` and is logged — as in `jobs/`; chat reports it as
unavailable. S3 failures fail the stage (temporary → retried).
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.ingest import storage
from app.ingest.config import Resource
from app.ingest.models import PaperFigure
from app.ingest.pdf.figures import FigureBox, FigureImage, render_figures
from app.ingest.stages.base import Stage, StageContext

# Uploads in flight at once.
_UPLOAD_CONCURRENCY = 8


@dataclass(frozen=True)
class StoredFigure:
    figure_id: str
    s3_key: str
    width: int
    height: int


def load_boxes(session: Session, paper_id: object) -> list[FigureBox]:
    rows = session.execute(
        select(PaperFigure.id, PaperFigure.page_no, PaperFigure.bbox)
        .where(PaperFigure.paper_id == paper_id)
        .order_by(PaperFigure.page_no, PaperFigure.ocr_image_id)
    ).all()
    return [
        FigureBox(figure_id=str(fid), page_no=page_no, bbox=dict(bbox or {}))
        for fid, page_no, bbox in rows
    ]


class Figures(Stage[list[StoredFigure]]):
    name = "figures"
    needs = ("ocr",)
    resource = Resource.CPU
    timeout_s = 300.0
    applies_to_supplementary = True

    async def run(self, ctx: StageContext) -> list[StoredFigure]:
        boxes = await ctx.read(lambda s: load_boxes(s, ctx.paper_id))
        if not boxes:
            return []
        await ctx.progress(0, len(boxes))
        pdf_bytes = await storage.load_pdf(ctx)
        images = await ctx.cpu(render_figures, pdf_bytes, boxes)

        stored: list[StoredFigure] = []
        done = 0
        limit = asyncio.Semaphore(_UPLOAD_CONCURRENCY)

        async def upload_one(image: FigureImage) -> None:
            nonlocal done
            if image.png is not None:
                key = storage.figure_key(ctx.paper_id, image.figure_id)
                async with limit:
                    await storage.upload(ctx, key, image.png, storage.PNG_CONTENT_TYPE)
                stored.append(
                    StoredFigure(
                        image.figure_id, key, image.width or 0, image.height or 0
                    )
                )
            done += 1
            await ctx.progress(done, len(boxes))

        await asyncio.gather(*(upload_one(image) for image in images))
        failed = len(boxes) - len(stored)
        if failed:
            ctx.log.warning(
                "%d of %d figures could not be rendered", failed, len(boxes)
            )
        return stored

    def save(
        self, session: Session, ctx: StageContext, output: list[StoredFigure]
    ) -> None:
        for fig in output:
            session.execute(
                update(PaperFigure)
                .where(
                    PaperFigure.id == uuid.UUID(fig.figure_id),
                    PaperFigure.paper_id == ctx.paper_id,
                )
                .values(s3_key=fig.s3_key, width=fig.width, height=fig.height)
            )
