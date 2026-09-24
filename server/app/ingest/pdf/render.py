"""Rendering PDF regions to PNG."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import pymupdf

from app.ingest.pdf.document import open_pdf

PDF_POINTS_PER_INCH = 72  # PDF user space is 72 units per inch by definition.

# The preview matches what `jobs/` produced: page 1 at 2x, at most 800 px wide.
PREVIEW_MAX_ZOOM = 2.0
PREVIEW_MAX_WIDTH = 800

# Guard against enormous bitmaps (a poster-sized page at 300 DPI is ~140 MP):
# above this, the zoom is lowered to fit.
MAX_PIXELS = 40_000_000


@dataclass(frozen=True)
class RenderedImage:
    png: bytes
    width: int  # pixels
    height: int


def render_clip(
    page: pymupdf.Page, clip: Optional[pymupdf.Rect], zoom: float
) -> RenderedImage:
    """Render `clip` (page coordinates as in `page.rect`: points, top-left
    origin, rotation applied; None = whole page) at `zoom` x 72 DPI.

    The clip is clamped to the page; raises ValueError if nothing is left.
    """
    area = page.rect if clip is None else pymupdf.Rect(clip) & page.rect
    if area.is_empty or area.is_infinite:
        raise ValueError(f"empty region {clip} on page {page.number + 1}")  # type: ignore[operator]
    pixels = area.width * area.height * zoom * zoom
    if pixels > MAX_PIXELS:
        zoom *= (MAX_PIXELS / pixels) ** 0.5
    pix = page.get_pixmap(  # type: ignore[attr-defined]
        matrix=pymupdf.Matrix(zoom, zoom), clip=area, alpha=False
    )
    return RenderedImage(png=pix.tobytes("png"), width=pix.width, height=pix.height)


def render_preview(pdf_bytes: bytes) -> RenderedImage:
    """First-page thumbnail: 2x zoom, scaled down to at most 800 px wide."""
    doc = open_pdf(pdf_bytes)
    try:
        page = doc.load_page(0)
        zoom = min(PREVIEW_MAX_ZOOM, PREVIEW_MAX_WIDTH / page.rect.width)
        return render_clip(page, None, zoom)
    finally:
        doc.close()
