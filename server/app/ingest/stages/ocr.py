"""`ocr` stage (stub).

Mistral OCR in `ocr_config().batch_pages`-page batches. Each batch's pages are
saved as it lands (`paper_pages.ocr_markdown` / `ocr_payload`), so a retry only
redoes missing pages; progress = pages done. Model: `ocr_config().model`
(service config, not a model slot) -> `ctx.model_used`.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.ingest.config import Resource, ocr_config
from app.ingest.stages.base import Stage, StageContext


class Ocr(Stage[Any]):
    name = "ocr"
    needs = ("source",)
    resource = Resource.OCR
    timeout_s = 900.0
    applies_to_supplementary = True

    def check_config(self) -> None:
        ocr_config().require()

    async def run(self, ctx: StageContext) -> Any:
        raise NotImplementedError

    def save(self, session: Session, ctx: StageContext, output: Any) -> None:
        raise NotImplementedError
