"""Saved Discover searches."""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any, Optional

from sqlalchemy import UUID, ForeignKey, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.models.base import Base

if TYPE_CHECKING:
    from app.database.models.user import User


class DiscoverSearch(Base):
    __tablename__ = "discover_searches"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    question: Mapped[str] = mapped_column(Text, nullable=False)
    subqueries: Mapped[Optional[list[str]]] = mapped_column(JSONB, nullable=True)
    # {subquery: [result dicts]}
    results: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONB, nullable=True)

    user: Mapped[User] = relationship()
