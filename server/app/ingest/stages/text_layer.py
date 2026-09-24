"""`text_layer` stage (stub).

pymupdf text per page -> `paper_pages.text_layer` (+ `width_pt`/`height_pt`),
and the PDF's embedded (XMP/info) metadata for the `metadata` stage.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.ingest.config import Resource
from app.ingest.stages.base import Stage, StageContext


class TextLayer(Stage[Any]):
    name = "text_layer"
    needs = ("source",)
    resource = Resource.CPU
    timeout_s = 120.0
    applies_to_supplementary = True

    async def run(self, ctx: StageContext) -> Any:
        raise NotImplementedError

    def save(self, session: Session, ctx: StageContext, output: Any) -> None:
        raise NotImplementedError
