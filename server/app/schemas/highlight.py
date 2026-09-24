"""Highlight and annotation API shapes, including the stored `position` JSON.

`position` is the `ScaledPosition` the client's PDF reader produces (the
react-pdf-highlighter-extended shape): a bounding rect plus one rect per line,
each in unrotated page space with the page's own size in `width`/`height`
(the reader rescales `coord * viewport / page_dim` at render time). The jobs
service's PyMuPDF anchoring (`jobs/src/highlight_anchor.py`) writes the same
shape for AI highlights and never sets `usePdfCoordinates`.
"""

from datetime import datetime
from typing import Any, Literal, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.database.models import HighlightType

HighlightColor = Literal["yellow", "green", "blue", "pink", "purple"]
AuthorRole = Literal["user", "assistant"]


class ScaledRect(BaseModel):
    """One rect: x1,y1 top-left and x2,y2 bottom-right, in page units;
    `width`/`height` are the page's dimensions, not the rect's."""

    x1: float
    y1: float
    x2: float
    y2: float
    width: float
    height: float
    pageNumber: int


class ScaledPosition(BaseModel):
    boundingRect: ScaledRect
    rects: list[ScaledRect]
    # Only set when the rects are PDF-native (bottom-left origin). No stored
    # row sets it today; omitted from the JSON when unset.
    usePdfCoordinates: Optional[bool] = Field(
        default=None, exclude_if=lambda value: value is None
    )

    def to_json(self) -> dict[str, Any]:
        """The JSONB value to store: exactly the fields that carry a value."""
        return self.model_dump(mode="json", exclude_none=True)


class HighlightResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    paper_id: UUID
    user_id: Optional[UUID] = None
    raw_text: str
    type: Optional[HighlightType] = None
    start_offset: Optional[int] = None
    end_offset: Optional[int] = None
    page_number: Optional[int] = None
    position: Optional[ScaledPosition] = None
    role: AuthorRole
    color: Optional[HighlightColor] = None
    created_at: datetime
    updated_at: datetime


class AnnotationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    highlight_id: UUID
    paper_id: UUID
    user_id: UUID
    content: str
    role: AuthorRole
    created_at: datetime
    updated_at: datetime
