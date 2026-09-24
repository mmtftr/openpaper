import logging
import uuid

from app.auth.dependencies import get_required_user
from app.database.crud.projects.project_crud import (
    ProjectCreate,
    ProjectUpdate,
    project_crud,
)
from app.database.database import get_db
from app.database.telemetry import track_event
from app.schemas.common import MessageResponse
from app.schemas.project import ProjectResponse
from app.schemas.user import CurrentUser
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

projects_router = APIRouter()


class CreateProjectRequest(BaseModel):
    title: str
    description: str | None = None


class UpdateProjectRequest(BaseModel):
    title: str | None = None
    description: str | None = None


@projects_router.post("", status_code=201)
def create_project(
    request: CreateProjectRequest,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_required_user),
) -> ProjectResponse:
    """Create a new project"""
    project = project_crud.create(
        db,
        obj_in=ProjectCreate(
            title=request.title,
            description=request.description,
        ),
        user=current_user,
    )
    if not project:
        raise HTTPException(
            status_code=400,
            detail="Failed to create project, please check the input data.",
        )

    track_event("project_created", user_id=str(current_user.id))
    return ProjectResponse.model_validate(project)


@projects_router.get("")
def get_projects(
    db: Session = Depends(get_db),
    detailed: bool = False,
    limit: int | None = None,
    current_user: CurrentUser = Depends(get_required_user),
) -> list[ProjectResponse]:
    """All of the current user's projects; `detailed=true` adds each
    project's paper count (newest-updated first, optionally `limit`ed)."""
    if detailed:
        return project_crud.get_all_projects_by_user_with_metadata(
            db, user=current_user, limit=limit
        )
    return [
        ProjectResponse.model_validate(project)
        for project in project_crud.get_multi_by_user(db, user=current_user)
    ]


def _project_not_found(project_id: uuid.UUID, action: str) -> HTTPException:
    return HTTPException(
        status_code=404,
        detail=f"Project with ID {project_id} not found or user does not have permission to {action}.",
    )


@projects_router.get("/{project_id}")
def get_project(
    project_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_required_user),
) -> ProjectResponse:
    """Get a single project by ID"""
    project = project_crud.get(db, id=project_id, user=current_user)
    if not project:
        raise HTTPException(
            status_code=404, detail=f"Project with ID {project_id} not found."
        )
    return ProjectResponse.model_validate(project)


@projects_router.patch("/{project_id}")
def update_project(
    project_id: uuid.UUID,
    request: UpdateProjectRequest,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_required_user),
) -> ProjectResponse:
    """Update an existing project"""
    project = project_crud.update(
        db,
        id=project_id,
        obj_in=ProjectUpdate(**request.model_dump(exclude_unset=True)),
        user=current_user,
    )
    if not project:
        raise _project_not_found(project_id, "update")

    track_event("project_updated", user_id=str(current_user.id))
    return ProjectResponse.model_validate(project)


@projects_router.delete("/{project_id}")
def delete_project(
    project_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_required_user),
) -> MessageResponse:
    """Delete a specific project"""
    if not project_crud.remove(db, id=project_id, user=current_user):
        raise _project_not_found(project_id, "delete")

    track_event("project_deleted", user_id=str(current_user.id))
    return MessageResponse(message="Project deleted successfully")
