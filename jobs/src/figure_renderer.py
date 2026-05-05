"""
Re-render figure regions from the source PDF at 300+ DPI and upload to S3.

Mistral returns image_base64 at ~200 DPI which is borderline for figure-reading
agents on dense diagrams. We have the bbox in pixel coords at the page DPI plus
the source PDF, so re-rendering at 300 DPI from pymupdf is straightforward and
gives us much sharper bitmaps for agent consumption.
"""

import logging
import os
from typing import Any, Dict, List, Optional

import pymupdf  # type: ignore

from src.s3_service import s3_service

logger = logging.getLogger(__name__)

TARGET_DPI = int(os.environ.get("FIGURE_RENDER_DPI", "300"))
PDF_POINT_DPI = 72  # PDF user space is 72 DPI by definition.


def _bbox_to_pdf_rect(
    bbox: Dict[str, Any],
    source_dpi: float,
) -> Optional[pymupdf.Rect]:
    """Convert pixel-space bbox at source_dpi into PDF point space."""
    try:
        x0 = float(bbox.get("top_left_x") or 0.0)
        y0 = float(bbox.get("top_left_y") or 0.0)
        x1 = float(bbox.get("bottom_right_x") or 0.0)
        y1 = float(bbox.get("bottom_right_y") or 0.0)
    except (TypeError, ValueError):
        return None

    if not source_dpi or source_dpi <= 0:
        return None

    scale = PDF_POINT_DPI / source_dpi
    rect = pymupdf.Rect(x0 * scale, y0 * scale, x1 * scale, y1 * scale)
    if rect.is_empty or rect.is_infinite:
        return None
    return rect


def render_and_upload_figures(
    pdf_path: str,
    ocr_jsonb: Dict[str, Any],
    job_id: str,
    target_dpi: int = TARGET_DPI,
    key_prefix: Optional[str] = None,
) -> Dict[str, Any]:
    """Walk the OCR pages, re-render each image region at target_dpi from the
    source PDF, upload PNG to S3, and update the ocr_jsonb in place with the
    s3_key per image. Returns the updated ocr_jsonb.

    Errors on individual figures don't fail the pipeline — the figure simply
    won't have an s3_key and the chat layer will report it as unavailable.
    """
    pages = ocr_jsonb.get("pages") or []
    if not pages:
        return ocr_jsonb

    figures: List[Dict[str, Any]] = ocr_jsonb.get("figures") or []
    figure_by_id_page: Dict[str, Dict[str, Any]] = {}
    for fig in figures:
        key = f"{fig.get('page')}::{fig.get('id')}"
        figure_by_id_page[key] = fig

    doc = pymupdf.open(pdf_path)
    try:
        for page_dict in pages:
            page_index = page_dict.get("index")
            if page_index is None:
                continue
            try:
                page = doc[int(page_index)]
            except (IndexError, ValueError):
                continue

            dimensions = page_dict.get("dimensions") or {}
            source_dpi = dimensions.get("dpi") or 200

            for img in page_dict.get("images") or []:
                img_id = img.get("id")
                if not img_id:
                    continue

                rect = _bbox_to_pdf_rect(
                    {
                        "top_left_x": img.get("top_left_x"),
                        "top_left_y": img.get("top_left_y"),
                        "bottom_right_x": img.get("bottom_right_x"),
                        "bottom_right_y": img.get("bottom_right_y"),
                    },
                    source_dpi=float(source_dpi),
                )
                if rect is None:
                    logger.warning(
                        "Skipping figure %s on page %s: invalid bbox",
                        img_id,
                        page_index,
                    )
                    continue

                # Clamp the clip rect to the page so pymupdf doesn't error on
                # bbox values that overshoot a page edge by a fraction.
                page_rect = page.rect
                clip = rect & page_rect
                if clip.is_empty:
                    continue

                zoom = target_dpi / PDF_POINT_DPI
                matrix = pymupdf.Matrix(zoom, zoom)
                try:
                    pix = page.get_pixmap(matrix=matrix, clip=clip)  # type: ignore
                    png_bytes = pix.tobytes("png")
                except Exception as e:
                    logger.warning(
                        "Failed to render figure %s on page %s: %s",
                        img_id,
                        page_index,
                        e,
                    )
                    continue

                safe_img_id = str(img_id).replace("/", "_")
                prefix = key_prefix or f"figures/{job_id}"
                object_key = f"{prefix}/{safe_img_id}.png"
                try:
                    s3_service.s3_client.put_object(
                        Bucket=s3_service.bucket_name,
                        Key=object_key,
                        Body=png_bytes,
                        ContentType="image/png",
                    )
                except Exception as e:
                    logger.warning(
                        "Failed to upload figure %s to S3: %s", img_id, e
                    )
                    continue

                img["s3_key"] = object_key
                img["dpi"] = target_dpi
                img["width_px"] = pix.width
                img["height_px"] = pix.height

                fig_key = f"{(int(page_index) + 1)}::{img_id}"
                fig_entry = figure_by_id_page.get(fig_key)
                if fig_entry is not None:
                    fig_entry["s3_key"] = object_key
                    fig_entry["dpi"] = target_dpi
    finally:
        doc.close()

    return ocr_jsonb
