import logging
import uuid

from app.auth.dependencies import get_required_user
from app.database.crud.annotation_crud import (
    AnnotationCreate,
    AnnotationUpdate,
    annotation_crud,
)
from app.database.database import get_db
from app.database.models import RoleType
from app.database.telemetry import track_event
from app.schemas.common import MessageResponse
from app.schemas.highlight import AnnotationResponse
from app.schemas.user import CurrentUser
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

# Create API router
annotation_router = APIRouter()


class CreateAnnotationRequest(BaseModel):
    paper_id: uuid.UUID
    highlight_id: uuid.UUID
    content: str


class UpdateAnnotationRequest(BaseModel):
    content: str


def _annotation_not_found(annotation_id: uuid.UUID) -> HTTPException:
    return HTTPException(
        status_code=404, detail=f"Annotation with ID {annotation_id} not found."
    )


@annotation_router.post("", status_code=201)
def create_annotation(
    request: CreateAnnotationRequest,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_required_user),
) -> AnnotationResponse:
    """Create a new annotation for a highlight"""
    annotation = annotation_crud.create(
        db,
        obj_in=AnnotationCreate(
            paper_id=request.paper_id,
            highlight_id=request.highlight_id,
            content=request.content,
            role=RoleType.USER,
        ),
        user=current_user,
    )
    if not annotation:
        raise HTTPException(
            status_code=400,
            detail="Failed to create annotation, please check the input data.",
        )

    track_event("annotation_created", user_id=str(current_user.id))
    return AnnotationResponse.model_validate(annotation)


@annotation_router.get("/{paper_id}")
def get_document_annotations(
    paper_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_required_user),
) -> list[AnnotationResponse]:
    """Get all annotations for a specific document"""
    annotations = annotation_crud.get_annotations_by_paper_id(
        db, paper_id=paper_id, user=current_user
    )
    return [AnnotationResponse.model_validate(a) for a in annotations]


@annotation_router.delete("/{annotation_id}")
def delete_annotation(
    annotation_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_required_user),
) -> MessageResponse:
    """Delete a specific annotation"""
    existing_annotation = annotation_crud.get(db, id=annotation_id, user=current_user)
    if not existing_annotation:
        raise _annotation_not_found(annotation_id)

    if existing_annotation.role == RoleType.ASSISTANT:
        raise HTTPException(
            status_code=403, detail="Cannot delete assistant annotations."
        )

    if not annotation_crud.remove(db, id=annotation_id, user=current_user):
        raise HTTPException(status_code=500, detail="Failed to delete annotation.")
    return MessageResponse(message="Annotation deleted successfully")


@annotation_router.patch("/{annotation_id}")
def update_annotation(
    annotation_id: uuid.UUID,
    request: UpdateAnnotationRequest,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_required_user),
) -> AnnotationResponse:
    """Update an existing annotation"""
    existing_annotation = annotation_crud.get(db, id=annotation_id, user=current_user)
    if not existing_annotation:
        raise _annotation_not_found(annotation_id)

    if existing_annotation.role == RoleType.ASSISTANT:
        raise HTTPException(
            status_code=403, detail="Cannot update assistant annotations."
        )

    annotation = annotation_crud.update(
        db,
        db_obj=existing_annotation,
        obj_in=AnnotationUpdate(
            paper_id=uuid.UUID(str(existing_annotation.paper_id)),
            highlight_id=uuid.UUID(str(existing_annotation.highlight_id)),
            content=request.content,
        ),
        user=current_user,
    )
    if not annotation:
        raise HTTPException(
            status_code=400,
            detail="Failed to update annotation, please check the input data.",
        )

    track_event("annotation_updated", user_id=str(current_user.id))
    return AnnotationResponse.model_validate(annotation)
