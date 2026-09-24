"""Load a paper's uploaded PDF from S3 (for stages that read the PDF itself).

Integration note: the PDF stages have their own storage helper; this one can
be folded into it.
"""

from __future__ import annotations

import asyncio

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database.models import Paper
from app.ingest.stages.base import StageContext, fail_permanent


async def load_source_pdf(ctx: StageContext) -> bytes:
    def object_key(session: Session) -> str | None:
        return session.scalar(
            select(Paper.s3_object_key).where(Paper.id == ctx.paper_id)
        )

    key = await ctx.read(object_key)
    if not key:
        fail_permanent("The paper has no stored PDF")
    return await asyncio.to_thread(ctx.get_s3().get_object_bytes, key)
