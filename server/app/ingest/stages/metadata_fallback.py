"""`metadata_fallback` stage (stub).

Skipped if `metadata` resolved. Else retry the lookup on OCR text, then LLM
extraction (fields marked `MetadataSource.LLM`, i.e. unverified).
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.ingest.config import Resource
from app.ingest.stages.base import Stage, StageContext


class MetadataFallback(Stage[Any]):
    name = "metadata_fallback"
    needs = ("metadata", "ocr_repair")
    resource = Resource.LLM
    timeout_s = 180.0
    model_slot = "ingest.metadata"

    async def run(self, ctx: StageContext) -> Any:
        raise NotImplementedError

    def save(self, session: Session, ctx: StageContext, output: Any) -> None:
        raise NotImplementedError
