import logging
from datetime import datetime, timezone
from typing import List, Optional
from uuid import UUID

from app.database.crud.projects.project_base_crud import ProjectBaseCRUD
from app.database.models import (
    ConversableType,
    Project,
    ProjectPaper,
)
from app.schemas.project import ProjectResponse
from app.schemas.user import CurrentUser
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


# Pydantic models
class ProjectBase(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None


class ProjectCreate(ProjectBase):
    title: Optional[str] = None
    description: Optional[str] = None


class ProjectUpdate(ProjectBase):
    pass


class ProjectCRUD(ProjectBaseCRUD[Project, ProjectCreate, ProjectUpdate]):
    def create(
        self, db: Session, *, obj_in: ProjectCreate, user: Optional[CurrentUser] = None
    ) -> Optional[Project]:

        if user is None:
            raise ValueError("user parameter is required for ProjectCRUD.create")

        try:
            # Create the project
            db_obj = Project(
                title=obj_in.title,
                description=obj_in.description,
                owner_id=user.id,
            )
            db.add(db_obj)
            db.commit()
            db.refresh(db_obj)

            return db_obj
        except Exception as e:
            db.rollback()
            logger.error(f"Error creating {Project.__name__}: {str(e)}", exc_info=True)
            return None

    def get_all_projects_by_user_with_metadata(
        self, db: Session, user: CurrentUser, limit: Optional[int] = None
    ) -> List[ProjectResponse]:
        """
        Get all projects for a user with metadata (num_papers) in a single query.
        """
        try:
            # Build a query that joins all necessary tables and aggregates the counts
            query = (
                db.query(
                    Project,
                    func.coalesce(func.count(ProjectPaper.id.distinct()), 0).label(
                        "num_papers"
                    ),
                )
                .outerjoin(ProjectPaper, Project.id == ProjectPaper.project_id)
                .filter(Project.owner_id == user.id)
                .group_by(Project.id)
                .order_by(Project.updated_at.desc())
                .limit(limit)
                .all()
            )

            # Convert the results to ProjectResponse objects
            annotated_projects = []
            for (
                project,
                num_papers,
            ) in query:
                annotated_project = ProjectResponse.model_validate(project)
                annotated_project.num_papers = num_papers
                annotated_projects.append(annotated_project)

            return annotated_projects

        except Exception as e:
            logger.error(
                f"Error fetching projects with metadata for user {user.id}: {str(e)}",
                exc_info=True,
            )
            return []

    def touch(self, db: Session, project_id: UUID) -> None:
        """Update the project's updated_at timestamp to now."""
        try:
            project = db.query(Project).filter(Project.id == project_id).first()
            if project:
                project.updated_at = datetime.now(timezone.utc)
                db.commit()
        except Exception as e:
            db.rollback()
            logger.error(f"Error touching project {project_id}: {str(e)}")


project_crud = ProjectCRUD(Project)
