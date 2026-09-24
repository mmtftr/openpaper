import logging
import uuid
from typing import Optional

from app.database.crud.projects.project_base_crud import ProjectBaseCRUD
from app.database.crud.projects.project_crud import project_crud
from app.database.models import Project, ProjectAudioOverview
from app.schemas.user import CurrentUser
from pydantic import BaseModel
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


class ProjectAudioOverviewBase(BaseModel):
    audio_overview_id: uuid.UUID


class ProjectAudioOverviewCreate(ProjectAudioOverviewBase):
    pass


class ProjectAudioOverviewUpdate(ProjectAudioOverviewBase):
    pass


class ProjectAudioOverviewCRUD(
    ProjectBaseCRUD[
        ProjectAudioOverview, ProjectAudioOverviewCreate, ProjectAudioOverviewUpdate
    ]
):
    def create(
        self,
        db: Session,
        *,
        obj_in: ProjectAudioOverviewCreate,
        user: Optional[CurrentUser] = None,
        project_id: Optional[uuid.UUID] = None,
    ) -> Optional[ProjectAudioOverview]:
        # Validate required parameters for this implementation
        if user is None:
            raise ValueError(
                "user parameter is required for ProjectAudioOverviewCRUD.create"
            )
        if project_id is None:
            raise ValueError(
                "project_id parameter is required for ProjectAudioOverviewCRUD.create"
            )

        try:
            # Check if the user owns this project
            project = (
                db.query(Project)
                .filter(Project.id == project_id, Project.owner_id == user.id)
                .first()
            )
            if not project:
                return None

            db_obj = ProjectAudioOverview(
                project_id=project_id, audio_overview_id=obj_in.audio_overview_id
            )
            db.add(db_obj)
            db.commit()
            db.refresh(db_obj)

            # Touch project updated_at so it sorts to top of recent projects
            project_crud.touch(db, project_id)

            return db_obj
        except Exception as e:
            db.rollback()
            logger.error(
                f"Error creating {self.model.__name__}: {str(e)}", exc_info=True
            )
            return None


project_audio_overview_crud = ProjectAudioOverviewCRUD(ProjectAudioOverview)
