"""`highlights` stage (stub).

3-5 AI highlights anchored to PDF rects (port of
`jobs/src/highlight_anchor.py`), `origin='ai'`. AI highlights the owner has
annotated are kept on regeneration.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.ingest.config import Resource
from app.ingest.stages.base import Stage, StageContext


class Highlights(Stage[Any]):
    name = "highlights"
    needs = ("ocr_repair", "text_layer")
    resource = Resource.LLM
    timeout_s = 300.0
    model_slot = "ingest.highlights"

    async def run(self, ctx: StageContext) -> Any:
        raise NotImplementedError

    def save(self, session: Session, ctx: StageContext, output: Any) -> None:
        raise NotImplementedError
