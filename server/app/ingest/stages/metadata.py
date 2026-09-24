"""`metadata` stage (stub).

Find a DOI / arXiv id (embedded metadata, filename, source URL, first two
pages) and look it up on Crossref / OpenAlex / arXiv (design §5). When it
resolves, `save()` also marks `metadata_fallback` skipped. Never overwrites
fields whose `papers.metadata_source` is "user".
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.ingest.config import Resource
from app.ingest.stages.base import Stage, StageContext


class Metadata(Stage[Any]):
    name = "metadata"
    needs = ("text_layer",)
    resource = Resource.NETWORK
    timeout_s = 60.0

    async def run(self, ctx: StageContext) -> Any:
        raise NotImplementedError

    def save(self, session: Session, ctx: StageContext, output: Any) -> None:
        raise NotImplementedError
