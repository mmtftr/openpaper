"""`outline` stage (stub).

Today's `app.llm.paper_outline`, at ingest -> `papers.generated_outline`.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.ingest.config import Resource
from app.ingest.stages.base import Stage, StageContext


class Outline(Stage[Any]):
    name = "outline"
    needs = ("ocr_repair",)
    resource = Resource.LLM
    timeout_s = 180.0
    model_slot = "ingest.outline"
    applies_to_supplementary = True

    async def run(self, ctx: StageContext) -> Any:
        raise NotImplementedError

    def save(self, session: Session, ctx: StageContext, output: Any) -> None:
        raise NotImplementedError
