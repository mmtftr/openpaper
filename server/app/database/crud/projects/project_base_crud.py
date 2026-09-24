from typing import Any, Dict, List, Optional, Union

from sqlalchemy.orm import Query, Session

from app.database.crud.base_crud import (
    CreateSchemaType,
    CRUDBase,
    ModelType,
    UpdateSchemaType,
)
from app.database.models import Project, ProjectPaper
from app.schemas.user import CurrentUser


class ProjectBaseCRUD(CRUDBase[ModelType, CreateSchemaType, UpdateSchemaType]):
    def _get_base_query(self, db: Session) -> Query:
        if self.model is Project:
            return db.query(self.model)
        else:
            return db.query(self.model).join(
                Project, self._col("project_id") == Project.id
            )

    def get(
        self,
        db: Session,
        id: Any,
        *,
        user: CurrentUser,
        update_last_accessed: bool = False,  # no such column on project rows
    ) -> Optional[ModelType]:
        query = self._get_base_query(db)
        return query.filter(self._col("id") == id, Project.owner_id == user.id).first()

    def get_multi_by_user(
        self, db: Session, *, user: CurrentUser, skip: int = 0, limit: int = 100
    ) -> List[ModelType]:
        query = self._get_base_query(db)
        return (
            query.filter(Project.owner_id == user.id)
            .order_by(self.model.created_at.desc())
            .offset(skip)
            .limit(limit)
            .all()
        )

    def update(
        self,
        db: Session,
        *,
        id: Any,
        obj_in: Union[UpdateSchemaType, Dict[str, Any]],
        user: CurrentUser,
    ) -> Optional[ModelType]:
        """Update a row of one of the user's projects; None if there is none."""
        query = self._get_base_query(db)
        db_obj = query.filter(
            self._col("id") == id, Project.owner_id == user.id
        ).first()
        if not db_obj:
            return None

        if isinstance(obj_in, dict):
            update_data = obj_in
        else:
            update_data = obj_in.model_dump(exclude_unset=True)

        for field, value in update_data.items():
            setattr(db_obj, field, value)

        db.add(db_obj)
        db.commit()
        db.refresh(db_obj)
        return db_obj

    def remove(self, db: Session, *, id: Any, user: CurrentUser) -> Optional[ModelType]:
        """Delete a row of one of the user's projects (a project takes its
        paper links with it); None if there is none."""
        query = self._get_base_query(db)
        obj = query.filter(self._col("id") == id, Project.owner_id == user.id).first()
        if not obj:
            return None
        if self.model is Project:
            db.query(ProjectPaper).filter(ProjectPaper.project_id == id).delete(
                synchronize_session=False
            )
        db.delete(obj)
        db.commit()
        return obj
