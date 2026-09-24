"""`figures` stage (stub).

Render the OCR figure boxes at 300 DPI to S3 -> `paper_figures`.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.ingest.config import Resource
from app.ingest.stages.base import Stage, StageContext


class Figures(Stage[Any]):
    name = "figures"
    needs = ("ocr",)
    resource = Resource.CPU
    timeout_s = 300.0
    applies_to_supplementary = True

    async def run(self, ctx: StageContext) -> Any:
        raise NotImplementedError

    def save(self, session: Session, ctx: StageContext, output: Any) -> None:
        raise NotImplementedError
