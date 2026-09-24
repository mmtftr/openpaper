"""`preview` stage: first-page thumbnail.

Renders page 1 (2x, at most 800 px wide — as `jobs/` did) to
`papers/{id}/preview.png` and sets `papers.preview_url` to its public URL,
which the library cards already show.
"""

from __future__ import annotations

from sqlalchemy import update
from sqlalchemy.orm import Session

from app.database.models import Paper
from app.ingest import storage
from app.ingest.config import Resource
from app.ingest.pdf.render import render_preview
from app.ingest.stages.base import Stage, StageContext


class Preview(Stage[str]):
    name = "preview"
    needs = ("source",)
    resource = Resource.CPU
    timeout_s = 60.0
    applies_to_supplementary = True

    async def run(self, ctx: StageContext) -> str:
        """Returns the preview's public URL."""
        pdf_bytes = await storage.load_pdf(ctx)
        image = await ctx.cpu(render_preview, pdf_bytes)
        return await storage.upload(
            ctx, storage.preview_key(ctx.paper_id), image.png, storage.PNG_CONTENT_TYPE
        )

    def save(self, session: Session, ctx: StageContext, output: str) -> None:
        session.execute(
            update(Paper).where(Paper.id == ctx.paper_id).values(preview_url=output)
        )
