"""
PDF -> Mistral OCR -> typed result. Returns the joined-markdown raw_content
plus the per-page jsonb the agentic chat layer reads.

The figure re-render (high-DPI bitmaps from the source PDF) lives in slice 2;
this module is concerned only with calling Mistral and shaping the response.
"""

import logging
import re
from typing import Any, Dict, List, Optional, Tuple

import pymupdf  # type: ignore

from src.mistral_client import MistralOCRClient, MistralOCRUnavailable

logger = logging.getLogger(__name__)


def _strip_image_base64(pages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Drop image_base64 from each image so the stored jsonb stays small.

    The 200 DPI bitmaps Mistral returns are borderline for figure-reading
    agents anyway; slice 2 re-renders from the source PDF at 300+ DPI and
    stores the bitmaps in S3, not in jsonb.

    Also strips NULL bytes from the page markdown — Postgres jsonb rejects
    them and Mistral occasionally leaks them through from the source PDF.
    """
    cleaned_pages = []
    for page in pages:
        new_page = dict(page)
        if isinstance(new_page.get("markdown"), str):
            new_page["markdown"] = new_page["markdown"].replace("\x00", "")
        images = page.get("images") or []
        new_images = []
        for img in images:
            new_img = {k: v for k, v in img.items() if k != "image_base64"}
            new_images.append(new_img)
        new_page["images"] = new_images
        cleaned_pages.append(new_page)
    return cleaned_pages


def _join_pages_markdown(pages: List[Dict[str, Any]]) -> Tuple[str, Dict[int, List[int]]]:
    """Join per-page markdown into a single string and build the page offset map.

    The offset map indexes into the joined string (1-based page numbers,
    matching the existing convention from pymupdf's flow). Highlight matching
    and FTS already work against this concatenated text shape.
    """
    chunks: List[str] = []
    offsets: Dict[int, List[int]] = {}
    cursor = 0
    sep = "\n\n"

    for page in pages:
        # Mistral pages are 0-indexed via "index"; we publish them 1-indexed.
        page_idx_raw = page.get("index")
        page_num = (int(page_idx_raw) + 1) if page_idx_raw is not None else (len(chunks) + 1)
        text = page.get("markdown") or page.get("text") or ""
        if not text:
            continue
        start = cursor
        chunks.append(text)
        cursor += len(text)
        offsets[page_num] = [start, cursor]
        if page is not pages[-1]:
            chunks.append(sep)
            cursor += len(sep)

    return "".join(chunks), offsets


_FIG_LABEL_RE = re.compile(
    r"(?im)^\s*(?:\*\*|_)?\s*(figure|fig\.?|table)\s+(\d+[a-z]?)\b[\s.:\)\-]*([^\n]*)$"
)


def _extract_figure_label_for_image(
    page_markdown: str, image: Dict[str, Any]
) -> Tuple[Optional[str], Optional[str]]:
    """Look for the nearest 'Figure N' / 'Table N' caption near the image's
    vertical position in the page markdown.

    Heuristic: the page markdown is a single string; we don't have
    line<->bbox mapping, so we accept the *first* caption line on the page as
    the label for any unlabeled image on that page when there's exactly one
    image. When there are multiple images we tag each with the Nth caption
    we find. This is loose but the agent has the bbox id as a fallback.

    Returns (label, caption_text) — either may be None.
    """
    matches = list(_FIG_LABEL_RE.finditer(page_markdown))
    if not matches:
        return None, None
    # Best-effort: caller passes images one at a time; we hand back the first
    # match. Multi-image pages are handled in the page-level walker below.
    m = matches[0]
    kind = m.group(1).strip().rstrip(".")
    num = m.group(2).strip()
    rest = (m.group(3) or "").strip()
    label = f"{kind.title()} {num}"
    return label, rest or None


def label_images_for_page(page: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Walk the images on a single page and tag them with the captions found
    in that page's markdown, in order. Returns a list of figure dicts ready
    to merge into the figure map (without S3 keys — those come in slice 2).
    """
    md = page.get("markdown") or ""
    matches = list(_FIG_LABEL_RE.finditer(md))
    images = page.get("images") or []
    page_idx_raw = page.get("index")
    page_num = (int(page_idx_raw) + 1) if page_idx_raw is not None else None

    figs: List[Dict[str, Any]] = []
    for i, img in enumerate(images):
        label: Optional[str] = None
        caption: Optional[str] = None
        if i < len(matches):
            m = matches[i]
            kind = m.group(1).strip().rstrip(".")
            num = m.group(2).strip()
            rest = (m.group(3) or "").strip()
            label = f"{kind.title()} {num}"
            caption = rest or None
        figs.append(
            {
                "id": img.get("id"),
                "label": label,
                "caption": caption,
                "page": page_num,
                "bbox": {
                    "top_left_x": img.get("top_left_x"),
                    "top_left_y": img.get("top_left_y"),
                    "bottom_right_x": img.get("bottom_right_x"),
                    "bottom_right_y": img.get("bottom_right_y"),
                },
            }
        )
    return figs


def build_figures_map(pages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Build the flat figure list across all pages."""
    out: List[Dict[str, Any]] = []
    for page in pages:
        out.extend(label_images_for_page(page))
    return out


class MistralOCRResult:
    """Structured result of a Mistral OCR pass.

    Kept as a plain object (not pydantic) because parser.py and
    pdf_processor.py work in the jobs container which likes dicts. The
    server side has its own schema for the persisted columns.
    """

    def __init__(
        self,
        raw_content: str,
        page_offset_map: Dict[int, List[int]],
        ocr_jsonb: Dict[str, Any],
        figures: List[Dict[str, Any]],
        page_count: int,
    ) -> None:
        self.raw_content = raw_content
        self.page_offset_map = page_offset_map
        self.ocr_jsonb = ocr_jsonb
        self.figures = figures
        self.page_count = page_count

    @property
    def figure_count(self) -> int:
        return len(self.figures)


def _extract_pymupdf_pages(pdf_path: str) -> Dict[int, str]:
    """Per-page plain-text extraction from the source PDF via pymupdf.

    Stored alongside Mistral's markdown so the citation reconciliation step
    can match model-emitted quotes (which are grounded in OCR text) against
    the actual PDF text the highlighter searches.

    Keyed by 1-indexed page number to align with Mistral's `index + 1`.
    """
    out: Dict[int, str] = {}
    try:
        doc = pymupdf.open(pdf_path)
    except Exception as e:
        logger.warning("pymupdf failed to open %s: %s", pdf_path, e)
        return out
    try:
        for i, page in enumerate(doc):
            try:
                txt = page.get_text("text") or ""  # type: ignore
                # Postgres jsonb rejects NULL bytes; strip them so the
                # upstream caller can persist this jsonb.
                out[i + 1] = txt.replace("\x00", "")
            except Exception as e:
                logger.warning("pymupdf failed on page %d: %s", i + 1, e)
                out[i + 1] = ""
    finally:
        doc.close()
    return out


def extract_with_mistral(
    pdf_path: str,
    client: Optional[MistralOCRClient] = None,
) -> MistralOCRResult:
    """Run Mistral OCR on the given PDF path and return a typed result.

    Raises MistralOCRUnavailable when Mistral isn't configured or fails after
    retries — caller should catch this and fall back to pymupdf.
    """
    client = client or MistralOCRClient()
    if not client.is_configured:
        raise MistralOCRUnavailable("MISTRAL_API_KEY not set")

    response = client.ocr_pdf(pdf_path)

    pages = response.get("pages") or []
    if not pages:
        raise MistralOCRUnavailable("Mistral returned no pages")

    raw_content, page_offset_map = _join_pages_markdown(pages)
    figures = build_figures_map(pages)

    # Side-by-side pymupdf text, keyed by 1-indexed page number. The chat
    # citation reconciliation step uses this to map OCR-grounded quotes onto
    # what the highlighter will actually find on the rendered PDF.
    pymupdf_pages = _extract_pymupdf_pages(pdf_path)

    cleaned_pages = _strip_image_base64(pages)
    for page in cleaned_pages:
        idx_raw = page.get("index")
        if idx_raw is None:
            continue
        page_num = int(idx_raw) + 1
        page["pymupdf_text"] = pymupdf_pages.get(page_num, "")

    ocr_jsonb = {
        "model": response.get("model"),
        "usage_info": response.get("usage_info"),
        "document_annotation": response.get("document_annotation"),
        "pages": cleaned_pages,
        "figures": figures,
    }

    return MistralOCRResult(
        raw_content=raw_content,
        page_offset_map=page_offset_map,
        ocr_jsonb=ocr_jsonb,
        figures=figures,
        page_count=len(pages),
    )
