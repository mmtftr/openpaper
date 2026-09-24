"""`outline` stage: the reader's table of contents, built at ingest.

- The PDF's own bookmarks when it has any (no model call).
- Otherwise today's `app.llm.paper_outline`: headings from each page's final
  markdown (`paper_pages.markdown`, positioned with the OCR layout blocks in
  `ocr_payload`), cleaned up by the `ingest.outline` model.

Saved to `papers.generated_outline` (what `/api/paper/outline` reads), as the
`OutlineEntry` tree. An empty list means "no outline" and is final.
"""

from __future__ import annotations

import math
from typing import Any

import pymupdf
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.database.models import Paper
from app.ingest import storage
from app.ingest.config import Resource
from app.ingest.models import PaperPage
from app.ingest.stages.base import Stage, StageContext
from app.llm import paper_outline


def read_bookmarks(pdf: bytes) -> list[dict[str, Any]]:
    """The PDF's bookmarks as flat `{title, level, page, top_percent}` entries.

    Bookmarks that don't resolve to a page are dropped.
    """
    entries: list[dict[str, Any]] = []
    with pymupdf.open(stream=pdf, filetype="pdf") as doc:
        for level, title, page, *rest in doc.get_toc(simple=False):
            title = " ".join(str(title).split())
            if not title or not 1 <= page <= doc.page_count:
                continue
            dest = rest[0] if rest else {}
            entries.append(
                {
                    "title": title,
                    "level": level,
                    "page": page,
                    "top_percent": _top_percent(dest, doc[page - 1].rect.height),
                }
            )
    return entries


def _top_percent(dest: dict[str, Any], page_height: float) -> float | None:
    """How far down the page the bookmark points, in percent from the top.

    pymupdf gives an explicit destination's `to` point in its own top-left
    coordinates, but a *named* destination's (LaTeX/hyperref's usual kind,
    `nameddest: "section.3"`) as the raw PDF /XYZ value, bottom-left origin —
    checked against the heading text on 1,200 bookmarks of real papers.
    """
    point = dest.get("to") if isinstance(dest, dict) else None
    if point is None or page_height <= 0:
        return None
    y = float(point.y)
    if dest.get("kind") == pymupdf.LINK_NAMED:
        y = page_height - y
    if not math.isfinite(y) or not 0 <= y <= page_height:
        return None
    return round(100 * y / page_height, 3)


def load_outline_pages(
    session: Session, paper_id: Any
) -> list[paper_outline.OutlinePage]:
    rows = session.execute(
        select(PaperPage.page_no, PaperPage.markdown, PaperPage.ocr_payload)
        .where(PaperPage.paper_id == paper_id, PaperPage.markdown.isnot(None))
        .order_by(PaperPage.page_no)
    ).all()
    pages = []
    for page_no, markdown, payload in rows:
        payload = payload if isinstance(payload, dict) else {}
        blocks = payload.get("blocks")
        dimensions = payload.get("dimensions")
        pages.append(
            paper_outline.OutlinePage(
                page=page_no,
                markdown=markdown,
                blocks=[b for b in blocks if isinstance(b, dict)]
                if isinstance(blocks, list)
                else [],
                dimensions=dimensions if isinstance(dimensions, dict) else {},
            )
        )
    return pages


class Outline(Stage[list[dict[str, Any]]]):
    name = "outline"
    needs = ("ocr_repair",)
    resource = Resource.LLM
    timeout_s = 300.0
    model_slot = "ingest.outline"
    applies_to_supplementary = True

    async def run(self, ctx: StageContext) -> list[dict[str, Any]]:
        pdf = await storage.load_pdf(ctx)
        bookmarks = await ctx.cpu(read_bookmarks, pdf)
        if bookmarks:
            ctx.log.info("outline from %d PDF bookmarks", len(bookmarks))
            return paper_outline.build_tree(bookmarks)

        pages = await ctx.read(lambda s: load_outline_pages(s, ctx.paper_id))
        candidates = paper_outline.candidates_from_pages(pages)
        if paper_outline.needs_cleanup(candidates):
            self.resolve_model(ctx)
        return await paper_outline.clean_outline(candidates)

    def save(
        self, session: Session, ctx: StageContext, output: list[dict[str, Any]]
    ) -> None:
        session.execute(
            update(Paper)
            .where(Paper.id == ctx.paper_id)
            .values(generated_outline=output)
        )
