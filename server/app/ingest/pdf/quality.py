"""OCR quality: does a page's OCR text describe the same page as its text layer?

Ported unchanged from `jobs/src/parser_mistral.py` (thresholds included).
pymupdf's text layer is a cheap independent read for born-digital PDFs: when
OCR hallucinates a whole page, the tokens it shares with the text layer
collapse. Pages with too little text-layer text (scans, figure pages) can't
be checked and are `unchecked`.

`page_quality()` returns the dict stored in `paper_pages.ocr_quality`:
    {status: "ok" | "suspect" | "unchecked", reason, pymupdf_token_count,
     ocr_token_count, common_token_count, pymupdf_token_recall,
     ocr_token_precision}
(`unchecked` carries only the two counts, as before.)
"""

from __future__ import annotations

import re
from typing import Any

_COMPARE_TOKEN_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{2,}")
MIN_COMPARE_TOKENS = 20
MIN_COMPARE_CHARS = 120
SUSPECT_RECALL_THRESHOLD = 0.35
SUSPECT_PRECISION_THRESHOLD = 0.50
SUSPECT_COMMON_TOKEN_THRESHOLD = 12
# A vision re-OCR replaces the page only if it clears these.
REPAIRED_RECALL_THRESHOLD = 0.20
REPAIRED_COMMON_TOKEN_THRESHOLD = 8

OK = "ok"
SUSPECT = "suspect"
UNCHECKED = "unchecked"


def tokens(text: str) -> set[str]:
    """Normalize text into stable tokens for a rough OCR-vs-PDF comparison."""
    return {token.lower() for token in _COMPARE_TOKEN_RE.findall(text)}


def page_quality(markdown: str, text_layer: str) -> dict[str, Any]:
    """Score OCR `markdown` against the page's pymupdf `text_layer`."""
    pdf_tokens = tokens(text_layer)
    ocr_tokens = tokens(markdown)

    if (
        len(text_layer.strip()) < MIN_COMPARE_CHARS
        or len(pdf_tokens) < MIN_COMPARE_TOKENS
    ):
        return {
            "status": UNCHECKED,
            "reason": "insufficient_pymupdf_text",
            "pymupdf_token_count": len(pdf_tokens),
            "ocr_token_count": len(ocr_tokens),
        }

    common = pdf_tokens & ocr_tokens
    recall = len(common) / len(pdf_tokens)
    precision = len(common) / len(ocr_tokens) if ocr_tokens else 0.0
    status, reason = OK, None
    if (
        recall < SUSPECT_RECALL_THRESHOLD
        and precision < SUSPECT_PRECISION_THRESHOLD
        and len(common) < SUSPECT_COMMON_TOKEN_THRESHOLD
    ):
        status, reason = SUSPECT, "low_pymupdf_token_recall"

    return {
        "status": status,
        "reason": reason,
        "pymupdf_token_count": len(pdf_tokens),
        "ocr_token_count": len(ocr_tokens),
        "common_token_count": len(common),
        "pymupdf_token_recall": round(recall, 4),
        "ocr_token_precision": round(precision, 4),
    }


def repair_is_good(markdown: str, quality: dict[str, Any]) -> bool:
    """Whether a vision re-OCR (scored with `page_quality`) may replace the page."""
    return bool(
        markdown
        and quality.get("status") != SUSPECT
        and (
            quality.get("pymupdf_token_recall", 0) >= REPAIRED_RECALL_THRESHOLD
            or quality.get("common_token_count", 0) >= REPAIRED_COMMON_TOKEN_THRESHOLD
        )
    )
