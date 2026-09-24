"""`reference_resolutions`: the resolved-reference cache.

Global (single-user deployment), keyed by the sha256 of the normalized
entry text, so the same reference cited by two papers resolves once.
Found results never expire (`expires_at` NULL) unless refreshed;
`unresolved` ones expire so a later lookup can find a paper that has since
been indexed. Transient failures are never stored.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

# pyright ignores: SQLAlchemy 2.0 names the 1.4-era sqlalchemy2-stubs
# (resolved first by pyright) don't know. Runtime is SQLAlchemy 2.0.
from sqlalchemy import DateTime, Text, func  # type: ignore
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import (  # type: ignore
    Mapped,
    mapped_column,  # pyright: ignore[reportAttributeAccessIssue]
)

from app.database.models import Base


class ReferenceResolution(Base):
    __tablename__ = "reference_resolutions"

    key: Mapped[str] = mapped_column(Text, primary_key=True)
    entry_text: Mapped[str] = mapped_column(Text, nullable=False)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    # A `ResolvedReference` (without the library overlay).
    result: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    resolved_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    expires_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    def __repr__(self) -> str:
        return f"<ReferenceResolution {self.key[:12]} {self.kind}>"
