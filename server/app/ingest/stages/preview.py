"""`preview` stage (stub).

First-page thumbnail to S3 -> `papers.preview_url`.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.ingest.config import Resource
from app.ingest.stages.base import Stage, StageContext


class Preview(Stage[Any]):
    name = "preview"
    needs = ("source",)
    resource = Resource.CPU
    timeout_s = 60.0
    applies_to_supplementary = True

    async def run(self, ctx: StageContext) -> Any:
        raise NotImplementedError

    def save(self, session: Session, ctx: StageContext, output: Any) -> None:
        raise NotImplementedError
