"""Owner settings stored in the database."""

from __future__ import annotations

from typing import Optional

from sqlalchemy import Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database.models.base import Base


class ModelSlot(Base):
    """A per-call-site model override (Settings -> Models).

    Global, not per-user: this is a single-user deployment. A missing row, or
    a NULL column, means the slot's built-in default (`app.llm.model_slots`).
    """

    __tablename__ = "model_slots"

    slot: Mapped[str] = mapped_column(Text, primary_key=True)
    provider: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    model: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    reasoning_effort: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    def __repr__(self) -> str:
        return f"<ModelSlot slot={self.slot}>"
