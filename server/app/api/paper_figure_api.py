"""
Endpoints for the high-DPI figure bitmaps rendered at ingest.

Figures are `paper_figures` rows (`{label, ocr_image_id, page_no, s3_key,
caption}`, read via `app.ingest.content`). This module resolves human labels
("Figure 2", "Fig. 3a", "Table 4"), the Mistral image id ("img-0.jpeg") or
the row id to the stored PNG and streams it. Stored keys are never rewritten,
so images saved in chat history (legacy `figures/{paper_id}/img-N.jpeg.png`
keys included) keep resolving.
"""

import logging
from typing import List
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.auth.dependencies import get_required_user
from app.database.crud.paper_crud import paper_crud
from app.database.database import get_db
from app.helpers.s3 import s3_service
from app.ingest import content
from app.schemas.paper import PaperFigureSummary
from app.schemas.user import CurrentUser

logger = logging.getLogger(__name__)

paper_figure_router = APIRouter()


@paper_figure_router.get(
    "/{paper_id}/figure/{label_or_id}",
    response_class=Response,
    responses={
        200: {
            "content": {
                "image/png": {"schema": {"type": "string", "format": "binary"}}
            },
            "description": "The figure bitmap. `X-Figure-Label` / `X-Figure-Page` "
            "headers carry its label and page when known.",
        }
    },
)
def get_paper_figure(
    paper_id: UUID,
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

    figure = content.resolve_figure(content.figures(db, paper_id), label_or_id)
    if not figure:
        raise HTTPException(status_code=404, detail="Figure not found")

    if not figure.s3_key:
        raise HTTPException(status_code=404, detail="Figure not yet rendered")

    try:
        png_bytes = s3_service.get_object_bytes(figure.s3_key)
    except Exception as e:
        logger.error(f"Failed to fetch figure {figure.s3_key} from S3: {e}")
        raise HTTPException(status_code=502, detail="Figure storage unavailable")

    headers = {"X-Figure-Page": str(figure.page_no)}
    if figure.label:
        headers["X-Figure-Label"] = figure.label

    return Response(content=png_bytes, media_type="image/png", headers=headers)


@paper_figure_router.get("/{paper_id}/figures")
def list_paper_figures(
    paper_id: UUID,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_required_user),
) -> List[PaperFigureSummary]:
    """List all figures for a paper with their labels, captions and pages.

    Used by the client to render a figure index in the chat UI without
    fetching the bitmaps.
    """
    paper = paper_crud.get(db, id=paper_id, user=current_user)
    if not paper:
        raise HTTPException(status_code=404, detail="Paper not found")

    return [
        PaperFigureSummary(
            id=fig.ocr_image_id,
            label=fig.label,
            caption=fig.caption,
            page=fig.page_no,
            available=fig.available,
        )
        for fig in content.figures(db, paper_id)
    ]
