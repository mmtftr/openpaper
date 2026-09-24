"""`text_layer` stage: the PDF's own text per page, plus its embedded metadata.

Writes
- `paper_pages.text_layer`, `width_pt`, `height_pt` for every page (upsert;
  the `ocr` stage fills other columns of the same rows concurrently);
- the embedded title / authors / DOI / arXiv id onto `papers.title`,
  `authors`, `doi`, `arxiv_id` with `papers.metadata_source[field] =
  "embedded"` — only into fields that are empty or were themselves
  embedded (never over "user" or a looked-up value).

For the `metadata` stage: embedded values are unverified hints. Read them
back as the paper fields whose `metadata_source` is "embedded", and
overwrite them freely with what Crossref/OpenAlex/arXiv resolve. The raw
info-dict strings (author/subject/keywords) are only in this stage's
in-memory output (`TextLayerResult.embedded`), not persisted.
"""

from __future__ import annotations

from typing import Any, cast

from sqlalchemy import func
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.database.models import Paper
from app.ingest import storage
from app.ingest.config import Resource
from app.ingest.models import MetadataSource, PaperPage
from app.ingest.pdf.text import EmbeddedMetadata, TextLayerResult, extract_text_layer
from app.ingest.stages.base import Stage, StageContext

# Rows per INSERT (5 bound params each; Postgres allows 65535 per statement).
_PAGE_BATCH = 1000


class TextLayer(Stage[TextLayerResult]):
    name = "text_layer"
    needs = ("source",)
    resource = Resource.CPU
    timeout_s = 300.0
    applies_to_supplementary = True

    async def run(self, ctx: StageContext) -> TextLayerResult:
        pdf_bytes = await storage.load_pdf(ctx)
        result = await ctx.cpu(extract_text_layer, pdf_bytes)
        await ctx.progress(len(result.pages), len(result.pages))
        return result

    def save(
        self, session: Session, ctx: StageContext, output: TextLayerResult
    ) -> None:
        paper = session.get(Paper, ctx.paper_id)
        if paper is None:  # deleted meanwhile; its pages went with it
            return
        save_pages(session, ctx.paper_id, output)
        apply_embedded_metadata(paper, output.embedded)


def save_pages(session: Session, paper_id: Any, output: TextLayerResult) -> None:
    rows = [
        {
            "paper_id": paper_id,
            "page_no": page.page_no,
            "text_layer": page.text,
            "width_pt": page.width_pt,
            "height_pt": page.height_pt,
        }
        for page in output.pages
    ]
    for start in range(0, len(rows), _PAGE_BATCH):
        stmt = insert(PaperPage).values(rows[start : start + _PAGE_BATCH])
        stmt = stmt.on_conflict_do_update(
            index_elements=[PaperPage.paper_id, PaperPage.page_no],
            set_={
                "text_layer": stmt.excluded.text_layer,
                "width_pt": stmt.excluded.width_pt,
                "height_pt": stmt.excluded.height_pt,
                "updated_at": func.now(),
            },
        )
        session.execute(stmt)


def apply_embedded_metadata(paper: Paper, embedded: EmbeddedMetadata) -> list[str]:
    """Copy embedded values onto `paper` where allowed; returns the fields set."""
    values: dict[str, Any] = {
        "title": embedded.title,
        "authors": embedded.authors or None,
        "doi": embedded.doi,
        "arxiv_id": embedded.arxiv_id,
    }
    current = cast("dict[str, str] | None", paper.metadata_source)
    sources = dict(current or {})
    written: list[str] = []
    for field, value in values.items():
        if not value:
            continue
        source = sources.get(field)
        if source == MetadataSource.EMBEDDED or (
            source is None and not getattr(paper, field)
        ):
            setattr(paper, field, value)
            sources[field] = MetadataSource.EMBEDDED.value
            written.append(field)
    if written:
        # A new dict, so SQLAlchemy sees the JSONB change.
        paper.metadata_source = sources  # pyright: ignore[reportAttributeAccessIssue]
    return written
