import logging
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.auth.dependencies import get_required_user
from app.database.crud.highlight_crud import (
    HighlightCreate,
    HighlightUpdate,
    highlight_crud,
)
from app.database.database import get_db
from app.database.models import RoleType
from app.database.telemetry import track_event
from app.schemas.common import MessageResponse
from app.schemas.highlight import HighlightColor, HighlightResponse, ScaledPosition
from app.schemas.user import CurrentUser

logger = logging.getLogger(__name__)

# Create API router
highlight_router = APIRouter()


class CreateHighlightRequest(BaseModel):
    paper_id: uuid.UUID
    raw_text: str
    position: Optional[ScaledPosition] = None
    color: Optional[HighlightColor] = None
    # Legacy fields - kept for backwards compatibility
    start_offset: Optional[int] = None
    end_offset: Optional[int] = None
    page_number: Optional[int] = None


class UpdateHighlightRequest(BaseModel):
    raw_text: str
    position: Optional[ScaledPosition] = None
    color: Optional[HighlightColor] = None
    # Legacy fields - kept for backwards compatibility
    start_offset: Optional[int] = None
    end_offset: Optional[int] = None


def _position_json(position: Optional[ScaledPosition]) -> Optional[dict]:
    return position.to_json() if position is not None else None


@highlight_router.post("", status_code=201)
def create_highlight(
    request: CreateHighlightRequest,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_required_user),
) -> HighlightResponse:
    """Create a new highlight for a document"""
    highlight = highlight_crud.create(
        db,
        obj_in=HighlightCreate(
            paper_id=request.paper_id,
            raw_text=request.raw_text,
            start_offset=request.start_offset,
            end_offset=request.end_offset,
            page_number=request.page_number,
            position=_position_json(request.position),
            role=RoleType.USER,
            color=request.color,
        ),
        user=current_user,
    )
    if not highlight:
        raise HTTPException(
            status_code=400,
            detail="Failed to create highlight, please check the input data.",
        )

    track_event("highlight_created", user_id=str(current_user.id))
    return HighlightResponse.model_validate(highlight)


@highlight_router.get("/{paper_id}")
def get_document_highlights(
    paper_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_required_user),
) -> list[HighlightResponse]:
    """Get all highlights for a specific document"""
    highlights = highlight_crud.get_highlights_by_paper_id(
        db, paper_id=str(paper_id), user=current_user
    )
    return [HighlightResponse.model_validate(highlight) for highlight in highlights]


@highlight_router.delete("/{highlight_id}")
def delete_highlight(
    highlight_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_required_user),
) -> MessageResponse:
    """Delete a specific highlight"""
    existing_highlight = highlight_crud.get(db, id=highlight_id, user=current_user)
    if not existing_highlight:
        raise HTTPException(
            status_code=404, detail=f"Highlight with ID {highlight_id} not found."
        )

    if existing_highlight.role == RoleType.ASSISTANT:
        raise HTTPException(
            status_code=403, detail="Cannot delete assistant highlights."
        )

    if not highlight_crud.remove(db, id=highlight_id):
        raise HTTPException(status_code=500, detail="Failed to delete highlight.")
    return MessageResponse(message="Highlight deleted successfully")


@highlight_router.patch("/{highlight_id}")
def update_highlight(
    highlight_id: uuid.UUID,
    request: UpdateHighlightRequest,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_required_user),
) -> HighlightResponse:
    """Update an existing highlight"""
    existing_highlight = highlight_crud.get(db, id=highlight_id, user=current_user)
    if not existing_highlight:
        raise HTTPException(
            status_code=404, detail=f"Highlight with ID {highlight_id} not found."
        )

    if existing_highlight.role == RoleType.ASSISTANT:
        raise HTTPException(
            status_code=403, detail="Cannot update assistant highlights."
        )

    highlight = highlight_crud.update(
        db,
        db_obj=existing_highlight,
        obj_in=HighlightUpdate(
            paper_id=existing_highlight.paper_id,
            raw_text=request.raw_text,
            start_offset=request.start_offset,
            end_offset=request.end_offset,
            position=_position_json(request.position),
            color=request.color,
        ),
    )
    if not highlight:
        raise HTTPException(
            status_code=400,
            detail="Failed to update highlight, please check the input data.",
        )

    track_event("highlight_updated", user_id=str(current_user.id))
    return HighlightResponse.model_validate(highlight)
