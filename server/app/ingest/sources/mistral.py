"""Mistral OCR (direct API) — one request per page batch, shaped into pages.

Ported from `jobs/src/mistral_client.py` + `parser_mistral.py`. We call
api.mistral.ai directly (not Azure's gateway, ~250x slower there).

- `split_pdf(pdf, page_nos)` builds the small PDF a batch sends.
- `request_ocr(pdf, config, ...)` POSTs it and returns the JSON body. HTTP
  failures raise `MistralHTTPError`, which carries the httpx response, so
  `app.core.errors.classify` sorts it like any HTTP error (429 →
  rate_limited with Retry-After, 401/403 → config, 5xx → temporary, other
  4xx → permanent). A 2xx that isn't the expected JSON is a
  `PermanentError`. No retries here — the caller wraps a batch in
  `core.retry.retry_call` and anything longer is a stage retry.
- `parse_pages(body, page_nos)` maps the response's 0-based `index` (within
  the batch PDF) back to 1-based page numbers of the whole PDF and returns
  each page's markdown, its stored payload, and its figure boxes.
"""

from __future__ import annotations

import base64
import re
from dataclasses import dataclass, field
from typing import Any, Optional, Sequence

import httpx
import pymupdf

from app.core.errors import PermanentError
from app.ingest.config import OcrConfig

# Mistral's own cap on a request is minutes for big documents; a batch is
# 16 pages (~6 s measured), so this is generous.
REQUEST_TIMEOUT_S = 300.0


class MistralHTTPError(Exception):
    """A non-2xx reply from Mistral. `response` feeds `core.errors.classify`
    (status code, Retry-After)."""

    def __init__(self, response: httpx.Response) -> None:
        self.response = response
        self.status_code = response.status_code
        detail = response.text.strip().replace("\n", " ")[:300]
        super().__init__(
            f"Mistral OCR HTTP {response.status_code}"
            + (f": {detail}" if detail else "")
        )


# -- request --------------------------------------------------------------------


def split_pdf(pdf_bytes: bytes, page_nos: Sequence[int]) -> bytes:
    """A PDF holding just `page_nos` (1-based, in order). Picklable (runs in
    the worker's process pool)."""
    src = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    out = pymupdf.open()
    try:
        for page_no in page_nos:
            out.insert_pdf(src, from_page=page_no - 1, to_page=page_no - 1)
        return out.tobytes(garbage=3, deflate=True)
    finally:
        out.close()
        src.close()


def request_body(pdf_bytes: bytes, model: str) -> dict[str, Any]:
    b64 = base64.b64encode(pdf_bytes).decode("ascii")
    return {
        "model": model,
        "document": {
            "type": "document_url",
            "document_url": f"data:application/pdf;base64,{b64}",
        },
        # Image boxes come back either way; the bitmaps aren't needed (the
        # `figures` stage re-renders the boxes from the PDF at 300 DPI).
        "include_image_base64": False,
    }


async def request_ocr(
    pdf_bytes: bytes,
    config: OcrConfig,
    client: httpx.AsyncClient,
    *,
    timeout: float = REQUEST_TIMEOUT_S,
) -> dict[str, Any]:
    """OCR one (batch) PDF; returns Mistral's JSON body."""
    response = await client.post(
        config.endpoint,
        json=request_body(pdf_bytes, config.model),
        headers={
            "Authorization": f"Bearer {config.api_key}",
            "Accept": "application/json",
        },
        timeout=timeout,
    )
    if response.status_code >= 400:
        raise MistralHTTPError(response)
    try:
        body = response.json()
    except ValueError as exc:
        raise PermanentError(
            f"Mistral OCR returned non-JSON ({response.status_code})"
        ) from exc
    if not isinstance(body, dict) or not isinstance(body.get("pages"), list):
        raise PermanentError("Mistral OCR response has no pages")
    return body


# -- response -------------------------------------------------------------------


@dataclass(frozen=True)
class FigureBox:
    """One OCR image region; `bbox` in PDF points, top-left origin."""

    page_no: int
    ocr_image_id: str
    label: Optional[str]
    caption: Optional[str]
    bbox: dict[str, float]


@dataclass(frozen=True)
class OcrPage:
    page_no: int  # 1-based in the whole PDF
    markdown: str
    # The page object minus `markdown` and image base64 (`paper_pages.ocr_payload`).
    payload: dict[str, Any]
    figures: list[FigureBox] = field(default_factory=list)


def _strip_nul(value: Any) -> Any:
    """Postgres text/jsonb reject NUL; Mistral sometimes passes them through."""
    if isinstance(value, str):
        return value.replace("\x00", "")
    if isinstance(value, list):
        return [_strip_nul(v) for v in value]
    if isinstance(value, dict):
        return {k: _strip_nul(v) for k, v in value.items()}
    return value


_FIG_LABEL_RE = re.compile(
    r"(?im)^\s*(?:\*\*|_)?\s*(figure|fig\.?|table)\s+(\d+[a-z]?)\b[\s.:\)\-]*([^\n]*)$"
)


def _labels(markdown: str, count: int) -> list[tuple[Optional[str], Optional[str]]]:
    """(label, caption) for the page's images in order: the Nth image gets
    the Nth "Figure N" / "Table N" caption line on the page (as before)."""
    matches = list(_FIG_LABEL_RE.finditer(markdown))
    out: list[tuple[Optional[str], Optional[str]]] = []
    for i in range(count):
        if i >= len(matches):
            out.append((None, None))
            continue
        m = matches[i]
        kind = m.group(1).strip().rstrip(".")
        label = f"{kind.title()} {m.group(2).strip()}"
        out.append((label, (m.group(3) or "").strip() or None))
    return out


def _px_to_pt_scale(dimensions: Any, width_pt: Optional[float]) -> Optional[float]:
    """Points per OCR-image pixel: 72 / dpi (or page width ratio without dpi)."""
    if isinstance(dimensions, dict):
        dpi = dimensions.get("dpi")
        if isinstance(dpi, (int, float)) and dpi > 0:
            return 72.0 / float(dpi)
        width_px = dimensions.get("width")
        if width_pt and isinstance(width_px, (int, float)) and width_px > 0:
            return width_pt / float(width_px)
    return None


def _figures(
    page_no: int, page: dict[str, Any], markdown: str, width_pt: Optional[float]
) -> list[FigureBox]:
    images = [img for img in page.get("images") or [] if isinstance(img, dict)]
    scale = _px_to_pt_scale(page.get("dimensions"), width_pt)
    figures: list[FigureBox] = []
    for img, (label, caption) in zip(images, _labels(markdown, len(images))):
        coords = [
            img.get(k)
            for k in ("top_left_x", "top_left_y", "bottom_right_x", "bottom_right_y")
        ]
        if scale is None or not img.get("id"):
            continue
        if not all(isinstance(c, (int, float)) for c in coords):
            continue
        x0, y0, x1, y1 = (round(float(c) * scale, 2) for c in coords)  # type: ignore[arg-type]
        figures.append(
            FigureBox(
                page_no=page_no,
                ocr_image_id=str(img["id"]),
                label=label,
                caption=caption,
                bbox={"x0": x0, "y0": y0, "x1": x1, "y1": y1},
            )
        )
    return figures


def parse_pages(
    body: dict[str, Any],
    page_nos: Sequence[int],
    widths_pt: Optional[dict[int, float]] = None,
) -> list[OcrPage]:
    """Mistral's pages for a batch PDF made of `page_nos` → `OcrPage`s.

    Pages Mistral didn't return are simply absent (the caller notices).
    `widths_pt` (page widths) only matters if a page lacks `dimensions.dpi`.
    """
    pages: list[OcrPage] = []
    for position, raw in enumerate(body.get("pages") or []):
        if not isinstance(raw, dict):
            continue
        index = raw.get("index", position)
        if not isinstance(index, int) or not 0 <= index < len(page_nos):
            raise PermanentError(f"Mistral OCR returned page index {index!r}")
        page_no = page_nos[index]
        page = _strip_nul(raw)
        markdown = page.pop("markdown", None) or ""
        page["images"] = [
            {k: v for k, v in img.items() if k != "image_base64"}
            for img in page.get("images") or []
            if isinstance(img, dict)
        ]
        # Mistral's `index` is relative to the batch PDF; store the page's
        # 0-based index in the whole PDF instead.
        page["index"] = page_no - 1
        pages.append(
            OcrPage(
                page_no=page_no,
                markdown=markdown,
                payload=page,
                figures=_figures(
                    page_no, page, markdown, (widths_pt or {}).get(page_no)
                ),
            )
        )
    return pages
