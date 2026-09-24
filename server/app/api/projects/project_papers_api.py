import logging
import uuid
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.auth.dependencies import get_required_user
from app.database.crud.projects.project_paper_crud import (
    ProjectPaperCreate,
    project_paper_crud,
)
from app.database.database import get_db
from app.database.telemetry import track_event
from app.helpers.s3 import s3_service
from app.schemas.common import MessageResponse
from app.schemas.project import (
    ProjectPaperItem,
    ProjectPapersResponse,
    ProjectResponse,
)
from app.schemas.user import CurrentUser

logger = logging.getLogger(__name__)

# Create API router
project_papers_router = APIRouter()


class AddPaperToProjectRequest(BaseModel):
    paper_ids: List[uuid.UUID]


@project_papers_router.post("/{project_id}", status_code=201)
def add_paper_to_project(
    project_id: uuid.UUID,
    request: AddPaperToProjectRequest,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_required_user),
) -> MessageResponse:
    """Add papers to a project. Papers that can't be added (already in it,
    not the user's) are skipped and logged."""
    for paper_id in request.paper_ids:
        project_paper = project_paper_crud.create(
            db,
            obj_in=ProjectPaperCreate(paper_id=paper_id),
            user=current_user,
            project_id=project_id,
        )
        if not project_paper:
            logger.error(
                f"Failed to add paper {paper_id} to project {project_id}. Check permissions or if the paper already exists in the project."
            )

    track_event(
        "papers_added_to_project",
        user_id=str(current_user.id),
        properties={
            "project_id": str(project_id),
            "n_papers": len(request.paper_ids),
        },
    )

    return MessageResponse(message="Papers added to project successfully")


@project_papers_router.get("/{project_id}")
def get_project_papers(
    project_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_required_user),
) -> ProjectPapersResponse:
    """Get all papers for a specific project"""
    papers = project_paper_crud.get_all_papers_by_project_id(
        db, project_id=project_id, user=current_user
    )

    # Bulk retrieve presigned URLs for all papers (optimized with parallelization)
    file_urls = s3_service.get_cached_presigned_urls_bulk(db=db, papers=papers)

    return ProjectPapersResponse(
        papers=[
            ProjectPaperItem.model_validate(
                {
                    "id": paper.id,
                    "title": paper.title,
                    "created_at": paper.created_at,
                    "abstract": paper.abstract,
                    "authors": paper.authors,
                    "institutions": paper.institutions,
                    "keywords": paper.keywords,
                    "status": paper.status,
                    "journal": paper.journal,
                    "publisher": paper.publisher,
                    "doi": paper.doi,
                    "publish_date": paper.publish_date,
                    "file_url": file_urls.get(str(paper.id)),
                }
            )
            for paper in papers
        ]
    )


@project_papers_router.get("/from/{paper_id}")
def get_projects_from_paper_id(
    paper_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_required_user),
) -> list[ProjectResponse]:
    """Get all projects associated with a specific paper"""
    projects = project_paper_crud.get_projects_by_paper_id(
        db, paper_id=paper_id, user=current_user
    )
    return [ProjectResponse.model_validate(project) for project in projects]


@project_papers_router.delete("/{project_id}/{project_paper_id}")
def remove_paper_from_project(
    project_id: uuid.UUID,
    project_paper_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_required_user),
) -> MessageResponse:
    """Remove a paper from a project. Despite its name, `project_paper_id`
    is the paper's id."""
    removed_project_paper = project_paper_crud.remove_by_paper_and_project(
        db,
        paper_id=project_paper_id,
        project_id=project_id,
        user=current_user,
    )
    if not removed_project_paper:
        raise HTTPException(
            status_code=404,
            detail="Project paper association not found or user does not have permission to delete.",
        )

    track_event("paper_removed_from_project", user_id=str(current_user.id))
    return MessageResponse(message="Paper removed from project successfully")
