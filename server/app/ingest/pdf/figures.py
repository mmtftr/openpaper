"""Render OCR figure boxes from the source PDF at 300 DPI.

Mistral returns figure crops at ~200 DPI, borderline for reading dense
diagrams, so the `figures` stage re-renders each box from the PDF (port of
`jobs/src/figure_renderer.py`). Boxes are clamped to the page; no padding,
and captions are text (`paper_figures.caption`), not part of the image —
the same as the old renderer. A figure that can't be rendered is reported
as an error on that figure only.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Mapping, Optional

import pymupdf

from app.ingest.pdf.document import open_pdf
from app.ingest.pdf.render import PDF_POINTS_PER_INCH, render_clip

logger = logging.getLogger(__name__)

FIGURE_DPI = 300


@dataclass(frozen=True)
class FigureBox:
    figure_id: str  # `paper_figures.id`
    page_no: int  # 1-based
    # PDF points, top-left origin, as on the rendered page (`page.rect`).
    bbox: Mapping[str, Any]  # {"x0", "y0", "x1", "y1"}


@dataclass(frozen=True)
class FigureImage:
    figure_id: str
    png: Optional[bytes]  # None when it couldn't be rendered
    width: Optional[int] = None
    height: Optional[int] = None
    error: Optional[str] = None


def bbox_rect(bbox: Mapping[str, Any]) -> Optional[pymupdf.Rect]:
    """`{"x0","y0","x1","y1"}` -> Rect, or None if missing/degenerate."""
    try:
        rect = pymupdf.Rect(
            float(bbox["x0"]), float(bbox["y0"]), float(bbox["x1"]), float(bbox["y1"])
        )
    except (KeyError, TypeError, ValueError):
        return None
    if rect.is_empty or rect.is_infinite:
        return None
    return rect


def render_figures(
    pdf_bytes: bytes, boxes: list[FigureBox], dpi: int = FIGURE_DPI
) -> list[FigureImage]:
    """One `FigureImage` per box, in order."""
    zoom = dpi / PDF_POINTS_PER_INCH
    doc = open_pdf(pdf_bytes)
    try:
        return [_render_one(doc, box, zoom) for box in boxes]
    finally:
        doc.close()


def _render_one(doc: pymupdf.Document, box: FigureBox, zoom: float) -> FigureImage:
    def failed(reason: str) -> FigureImage:
        logger.warning("figure %s (page %s): %s", box.figure_id, box.page_no, reason)
        return FigureImage(figure_id=box.figure_id, png=None, error=reason)

    if not 1 <= box.page_no <= doc.page_count:
        return failed(f"page {box.page_no} is outside the PDF")
    rect = bbox_rect(box.bbox)
    if rect is None:
        return failed(f"invalid bbox {dict(box.bbox)}")
    try:
        image = render_clip(doc.load_page(box.page_no - 1), rect, zoom)
    except Exception as exc:  # bbox off the page, pymupdf render error
        return failed(str(exc))
    return FigureImage(
        figure_id=box.figure_id,
        png=image.png,
        width=image.width,
        height=image.height,
    )
