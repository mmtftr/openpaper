"""Read side of `paper_pages` / `paper_figures` for chat, search and the API.

Consumers (chat tools, chat context, the figures and markdown endpoints,
AI-highlight offsets) read a paper's text and figures through here instead
of querying the tables themselves. Replaces the legacy `papers.ocr`,
`papers.raw_content` and `papers.page_offset_map` columns:

- `pages()` → one `Page` per PDF page, in order; `markdown` is the final
  text `ocr_repair` chose ('' until it ran).
- `full_text()` → the pages joined with a blank line, plus each page's
  `(start, end)` offsets in that string — exactly what `raw_content` /
  `page_offset_map` used to hold.
- `figures()` → the paper's figures in document order (page, then Mistral's
  image number), as the legacy `ocr.figures` list was ordered.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from typing import Any, Optional, Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ingest.models import PaperFigure, PaperPage

# Between two pages in `full_text` (and in every "whole paper" read).
PAGE_SEPARATOR = "\n\n"

# A `uuid.UUID` or its string. `Any` so the legacy Column-typed `Paper.id`
# (sqlalchemy2-stubs) passes without a cast at every call site.
PaperId = Any


@dataclass(frozen=True)
class Page:
    page_no: int  # 1-based
    markdown: str  # final text; '' until ocr_repair has run


@dataclass(frozen=True)
class Figure:
    id: uuid.UUID  # paper_figures.id
    ocr_image_id: str  # Mistral's id, e.g. "img-3.jpeg" (unique per page)
    page_no: int
    label: Optional[str]
    caption: Optional[str]
    s3_key: Optional[str]

    @property
    def available(self) -> bool:
        """Rendered to S3 (the `figures` stage ran for it)."""
        return bool(self.s3_key)


def _as_uuid(paper_id: PaperId) -> uuid.UUID:
    return paper_id if isinstance(paper_id, uuid.UUID) else uuid.UUID(str(paper_id))


def pages(session: Session, paper_id: PaperId) -> list[Page]:
    rows = session.execute(
        select(PaperPage.page_no, PaperPage.markdown)
        .where(PaperPage.paper_id == _as_uuid(paper_id))
        .order_by(PaperPage.page_no)
    )
    return [Page(page_no=no, markdown=md or "") for no, md in rows]


def text_layer(session: Session, paper_id: PaperId, page_no: int) -> Optional[str]:
    """The pymupdf text of one page (what the PDF highlighter searches)."""
    return session.execute(
        select(PaperPage.text_layer).where(
            PaperPage.paper_id == _as_uuid(paper_id), PaperPage.page_no == page_no
        )
    ).scalar_one_or_none()


def full_text(
    paper_pages: Sequence[Page],
) -> tuple[str, dict[int, tuple[int, int]]]:
    """The pages joined by `PAGE_SEPARATOR`, and `{page_no: (start, end)}`
    of each page's text in it (the separator belongs to neither page)."""
    offsets: dict[int, tuple[int, int]] = {}
    cursor = 0
    for i, page in enumerate(paper_pages):
        if i:
            cursor += len(PAGE_SEPARATOR)
        offsets[page.page_no] = (cursor, cursor + len(page.markdown))
        cursor += len(page.markdown)
    return PAGE_SEPARATOR.join(p.markdown for p in paper_pages), offsets


_IMAGE_NUMBER_RE = re.compile(r"\d+")


def _document_order(figure: Figure) -> tuple[int, int, str]:
    # "img-10.jpeg" sorts after "img-9.jpeg": Mistral numbers images in
    # reading order (restarting per OCR batch, but batches are page ranges).
    match = _IMAGE_NUMBER_RE.search(figure.ocr_image_id)
    number = int(match.group()) if match else 0
    return (figure.page_no, number, figure.ocr_image_id)


def figures(session: Session, paper_id: PaperId) -> list[Figure]:
    rows = session.execute(
        select(
            PaperFigure.id,
            PaperFigure.ocr_image_id,
            PaperFigure.page_no,
            PaperFigure.label,
            PaperFigure.caption,
            PaperFigure.s3_key,
        ).where(PaperFigure.paper_id == _as_uuid(paper_id))
    )
    found = [
        Figure(
            id=fid,
            ocr_image_id=image_id,
            page_no=page_no,
            label=label,
            caption=caption,
            s3_key=s3_key,
        )
        for fid, image_id, page_no, label, caption, s3_key in rows
    ]
    return sorted(found, key=_document_order)


_LABEL_SPACE_RE = re.compile(r"\s+")


def normalize_label(label: str) -> str:
    """'Fig. 3a' / 'figure 3a' / 'FIGURE  3A' all become 'figure 3a'."""
    label = label.strip().lower().replace("fig.", "figure")
    return _LABEL_SPACE_RE.sub(" ", label)


def resolve_figure(
    paper_figures: Sequence[Figure], label_or_id: str
) -> Optional[Figure]:
    """Find a figure by id or by its human label.

    Tries, in document order: the row id or Mistral image id exactly (old
    chat history and the markdown reader use "img-N.jpeg"), then the
    normalized label exactly, then a label containing the input (so "3a"
    finds "Figure 3a").
    """
    for fig in paper_figures:
        if label_or_id in (str(fig.id), fig.ocr_image_id):
            return fig

    target = normalize_label(label_or_id)
    for fig in paper_figures:
        if fig.label and normalize_label(fig.label) == target:
            return fig
    for fig in paper_figures:
        if fig.label and target in normalize_label(fig.label):
            return fig
    return None


__all__ = [
    "PAGE_SEPARATOR",
    "Figure",
    "Page",
    "figures",
    "full_text",
    "normalize_label",
    "pages",
    "resolve_figure",
    "text_layer",
]
