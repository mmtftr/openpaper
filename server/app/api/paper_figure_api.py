"""
Endpoints for high-DPI figure bitmaps re-rendered from Mistral-parsed papers.

The OCR pipeline stores a `figures` map on the paper's `ocr` jsonb where
each entry has `{label, id, page, s3_key, caption}`. This module resolves
human labels ("Figure 2", "Fig. 3a", "Table 4") or the internal bbox id
to the s3_key and streams the PNG.
"""

import logging
import re
from typing import Any, Dict, List, Optional

from app.auth.dependencies import get_required_user
from app.database.crud.paper_crud import paper_crud
from app.database.database import get_db
from app.helpers.s3 import s3_service
from app.schemas.user import CurrentUser
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse, Response
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

paper_figure_router = APIRouter()


_LABEL_NORMALIZE_RE = re.compile(r"\s+")


def _normalize_label(s: str) -> str:
    """'Fig. 3a' / 'figure 3 a' / 'FIG3A' all collapse to 'fig 3a' style.

    The agent and the user both call figures by varying conventions;
    normalize once for the lookup so we don't litter the resolver with
    branches.
    """
    s = s.strip().lower()
    s = s.replace("fig.", "figure")
    s = _LABEL_NORMALIZE_RE.sub(" ", s)
    return s


def _figures_from_ocr(ocr: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    if not ocr:
        return []
    figures = ocr.get("figures") or []
    if not isinstance(figures, list):
        return []
    return figures


def resolve_figure(
    ocr: Optional[Dict[str, Any]], label_or_id: str
) -> Optional[Dict[str, Any]]:
    """Resolve a label or internal id against the paper's figures map.

    Returns the figure dict (with s3_key, caption, page) on hit, None on miss.
    Tries id-exact first, then label-normalized exact, then a label
    contains-match for partial inputs like '3a' that omit the kind.
    """
    figures = _figures_from_ocr(ocr)
    if not figures:
        return None

    target_norm = _normalize_label(label_or_id)

    # 1. id exact match
    for fig in figures:
        if str(fig.get("id") or "") == label_or_id:
            return fig

    # 2. label exact (normalized)
    for fig in figures:
        label = fig.get("label")
        if label and _normalize_label(label) == target_norm:
            return fig

    # 3. label contains target — covers "3a" matching "Figure 3a"
    for fig in figures:
        label = fig.get("label")
        if label and target_norm in _normalize_label(label):
            return fig

    return None


@paper_figure_router.get("/{paper_id}/figure/{label_or_id}")
async def get_paper_figure(
    paper_id: str,
    label_or_id: str,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_required_user),
):
    """Stream a high-DPI figure PNG for the given paper.

    `label_or_id` accepts the human label ("Figure 2", "Fig. 3a", "Table 4")
    or the internal Mistral bbox id (e.g. "img-0.jpeg"). Returns the PNG
    bytes inline so it can be embedded directly in the chat UI.
    """
    paper = paper_crud.get(db, id=paper_id, user=current_user)
    if not paper:
        raise HTTPException(status_code=404, detail="Paper not found")

    if str(getattr(paper, "parser", "") or "") != "mistral":
        raise HTTPException(
            status_code=404,
            detail="Figures are only available on Mistral-parsed papers",
        )

    figure = resolve_figure(getattr(paper, "ocr", None), label_or_id)
    if not figure:
        raise HTTPException(status_code=404, detail="Figure not found")

    s3_key = figure.get("s3_key")
    if not s3_key:
        raise HTTPException(
            status_code=404, detail="Figure not yet rendered"
        )

    try:
        png_bytes = s3_service.get_object_bytes(str(s3_key))
    except Exception as e:
        logger.error(f"Failed to fetch figure {s3_key} from S3: {e}")
        raise HTTPException(status_code=502, detail="Figure storage unavailable")

    headers = {}
    if figure.get("label"):
        headers["X-Figure-Label"] = str(figure["label"])
    if figure.get("page") is not None:
        headers["X-Figure-Page"] = str(figure["page"])

    return Response(content=png_bytes, media_type="image/png", headers=headers)


@paper_figure_router.get("/{paper_id}/figures")
async def list_paper_figures(
    paper_id: str,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_required_user),
) -> JSONResponse:
    """List all figures for a paper with their labels, captions and pages.

    Used by the client to render a figure index in the chat UI without
    fetching the bitmaps.
    """
    paper = paper_crud.get(db, id=paper_id, user=current_user)
    if not paper:
        raise HTTPException(status_code=404, detail="Paper not found")

    figures = _figures_from_ocr(getattr(paper, "ocr", None))
    return JSONResponse(
        content=[
            {
                "id": fig.get("id"),
                "label": fig.get("label"),
                "caption": fig.get("caption"),
                "page": fig.get("page"),
                "available": bool(fig.get("s3_key")),
            }
            for fig in figures
        ]
    )
