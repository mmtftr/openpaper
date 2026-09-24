"""The declarative `Base` every table (ingest's and references' too) shares.

Every table gets `created_at` / `updated_at` from here: both are filled by
Postgres (`now()`), and `updated_at` is bumped by SQLAlchemy on each UPDATE
it issues. The columns are nullable in the schema, but a flushed row always
has them, so they're typed as plain `datetime`.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=True, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        server_default=func.now(),
        onupdate=func.now(),
    )

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__} id={getattr(self, 'id', None)}>"
