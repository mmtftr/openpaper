"""`ocr_repair` stage (stub).

Score each page's OCR against its text layer; vision re-OCR of bad pages
(per-page fallback to the text layer); writes each page's final `markdown`
+ `markdown_source` + `ocr_quality`.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.ingest.config import Resource
from app.ingest.stages.base import Stage, StageContext


class OcrRepair(Stage[Any]):
    name = "ocr_repair"
    needs = ("ocr", "text_layer")
    resource = Resource.LLM
    timeout_s = 600.0
    model_slot = "ingest.ocr_repair"
    applies_to_supplementary = True

    async def run(self, ctx: StageContext) -> Any:
        raise NotImplementedError

    def save(self, session: Session, ctx: StageContext, output: Any) -> None:
        raise NotImplementedError
