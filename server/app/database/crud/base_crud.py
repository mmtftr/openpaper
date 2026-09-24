"""Generic CRUD over one model.

Errors propagate: a failed query or commit raises the SQLAlchemy exception,
and the caller's session is rolled back by its owner (`get_db` closes the
request session). A caller that catches a failure and keeps using the same
session must `db.rollback()` first. A row that doesn't exist, or isn't the
user's, is `None` from `get` and `NotFound` from `require` / `update` /
`remove`.
"""

import logging
from datetime import datetime, timezone
from typing import Any, Dict, Generic, List, Optional, Type, TypeVar, Union

from pydantic import BaseModel
from sqlalchemy.orm import Query, Session

from app.database.crud.sanitization import sanitize_for_postgres
from app.database.errors import NotFound
from app.database.models import Base
from app.schemas.user import CurrentUser

ModelType = TypeVar("ModelType", bound=Base)
CreateSchemaType = TypeVar("CreateSchemaType", bound=BaseModel)
UpdateSchemaType = TypeVar("UpdateSchemaType", bound=BaseModel)

logger = logging.getLogger(__name__)


def _get_sanitized_field_names(data: Dict[str, Any]) -> List[str]:
    sanitized_fields: List[str] = []
    for field, value in data.items():
        if sanitize_for_postgres(value) != value:
            sanitized_fields.append(field)
    return sanitized_fields


class CRUDBase(Generic[ModelType, CreateSchemaType, UpdateSchemaType]):
    def __init__(self, model: Type[ModelType]):
        self.model = model

    def _col(self, name: str) -> Any:
        """A column attribute of the model (`id`, `user_id`, ...), which the
        `Base` bound of `ModelType` can't promise statically."""
        return getattr(self.model, name)

    def _filter_by_user(
        self, query: Query[ModelType], user: Optional[CurrentUser] = None
    ) -> Query[ModelType]:
        """Add user filter to query if model has user_id and user is provided"""
        if user and hasattr(self.model, "user_id"):
            return query.filter(self._col("user_id") == user.id)
        return query

    def get(
        self,
        db: Session,
        id: Any,
        *,
        user: Optional[CurrentUser] = None,
        update_last_accessed: bool = False,
    ) -> Optional[ModelType]:
        """A record by id (only the user's, when `user` is given), or None."""
        query = db.query(self.model).filter(self._col("id") == id)
        query = self._filter_by_user(query, user)
        if update_last_accessed and hasattr(self.model, "last_accessed_at"):
            query.update(
                {self._col("last_accessed_at"): datetime.now(timezone.utc)},
                synchronize_session=False,
            )
            db.commit()
        return query.first()

    def require(
        self,
        db: Session,
        id: Any,
        *,
        user: Optional[CurrentUser] = None,
        not_found: str = "Not found",
        update_last_accessed: bool = False,
    ) -> ModelType:
        """`get`, raising `NotFound(not_found)` when there is no such row."""
        obj = self.get(db, id, user=user, update_last_accessed=update_last_accessed)
        if obj is None:
            raise NotFound(not_found)
        return obj

    def get_multi(
        self,
        db: Session,
        *,
        skip: int = 0,
        limit: int = 100,
        user: Optional[CurrentUser] = None,
    ) -> List[ModelType]:
        """Get multiple records with pagination, optionally filtered by user"""
        query = self._filter_by_user(db.query(self.model), user)
        return query.offset(skip).limit(limit).all()

    def create(
        self,
        db: Session,
        *,
        obj_in: CreateSchemaType,
        user: Optional[CurrentUser] = None,
    ) -> ModelType:
        """Create and commit a record, owned by `user` if the model has one."""
        obj_in_data = obj_in.model_dump()
        if user and hasattr(self.model, "user_id"):
            obj_in_data["user_id"] = user.id
        sanitized_fields = _get_sanitized_field_names(obj_in_data)
        obj_in_data = sanitize_for_postgres(obj_in_data)
        if sanitized_fields:
            logger.warning(
                "Sanitized null characters before creating %s in fields: %s",
                self.model.__name__,
                ", ".join(sanitized_fields),
            )
        db_obj = self.model(**obj_in_data)
        db.add(db_obj)
        db.commit()
        db.refresh(db_obj)
        return db_obj

    def update(
        self,
        db: Session,
        *,
        db_obj: ModelType,
        obj_in: Union[UpdateSchemaType, Dict[str, Any]],
        user: Optional[CurrentUser] = None,
    ) -> ModelType:
        """Update and commit a record. With `user`, the record must be theirs
        (`NotFound` otherwise)."""
        if (
            user
            and hasattr(db_obj, "user_id")
            and getattr(db_obj, "user_id") != user.id
        ):
            logger.warning(
                f"User {user.id} attempted to update {self.model.__name__} owned by another user"
            )
            raise NotFound(f"{self.model.__name__} not found")

        if isinstance(obj_in, dict):
            update_data = obj_in
        else:
            update_data = obj_in.model_dump(exclude_unset=True)

        sanitized_fields = _get_sanitized_field_names(update_data)
        update_data = sanitize_for_postgres(update_data)
        if sanitized_fields:
            logger.warning(
                "Sanitized null characters before updating %s %s in fields: %s",
                self.model.__name__,
                getattr(db_obj, "id", None),
                ", ".join(sanitized_fields),
            )

        for field in update_data:
            if hasattr(db_obj, field):
                setattr(db_obj, field, update_data[field])

        db.add(db_obj)
        db.commit()
        db.refresh(db_obj)
        return db_obj

    def remove(
        self,
        db: Session,
        *,
        id: Any,
        user: Optional[CurrentUser] = None,
        not_found: Optional[str] = None,
    ) -> ModelType:
        """Delete and commit a record (only the user's, when `user` is given);
        `NotFound(not_found)` if there is none."""
        query = self._filter_by_user(
            db.query(self.model).filter(self._col("id") == id), user
        )
        obj = query.first()
        if obj is None:
            raise NotFound(not_found or f"{self.model.__name__} not found")
        db.delete(obj)
        db.commit()
        return obj
