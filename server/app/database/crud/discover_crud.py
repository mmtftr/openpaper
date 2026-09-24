"""CRUD operations for DiscoverSearch."""

from typing import List, Optional
from uuid import UUID

from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database.models import DiscoverSearch
from app.schemas.user import CurrentUser


class DiscoverSearchCreate(BaseModel):
    question: str
    subqueries: list[str]
    results: dict


class DiscoverSearchCRUD:
    def create(
        self,
        db: Session,
        *,
        question: str,
        subqueries: list[str],
        results: dict,
        user: CurrentUser,
    ) -> DiscoverSearch:
        obj = DiscoverSearch(
            user_id=user.id,
            question=question,
            subqueries=subqueries,
            results=results,
        )
        db.add(obj)
        db.commit()
        db.refresh(obj)
        return obj

    def get_history(
        self,
        db: Session,
        *,
        user: CurrentUser,
        limit: int = 20,
    ) -> List[DiscoverSearch]:
        return (
            db.query(DiscoverSearch)
            .filter(DiscoverSearch.user_id == user.id)
            .order_by(DiscoverSearch.created_at.desc())
            .limit(limit)
            .all()
        )

    def get_by_id(
        self,
        db: Session,
        *,
        search_id: UUID,
        user: CurrentUser,
    ) -> Optional[DiscoverSearch]:
        return (
            db.query(DiscoverSearch)
            .filter(DiscoverSearch.id == search_id, DiscoverSearch.user_id == user.id)
            .first()
        )


discover_search_crud = DiscoverSearchCRUD()
