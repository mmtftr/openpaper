"""`source` stage (stub).

Store the PDF in S3, open it with pymupdf (reject corrupt/encrypted), record
page count. Runs inside the upload request (`runs_in_request`), so the PDF
is readable before the response returns.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.ingest.config import Resource
from app.ingest.stages.base import Stage, StageContext


class Source(Stage[Any]):
    name = "source"
    needs = ()
    resource = Resource.CPU
    timeout_s = 60.0
    applies_to_supplementary = True
    runs_in_request = True
    max_attempts = 1

    async def run(self, ctx: StageContext) -> Any:
        raise NotImplementedError

    def save(self, session: Session, ctx: StageContext, output: Any) -> None:
        raise NotImplementedError
