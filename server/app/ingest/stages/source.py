"""`source` stage: validate the PDF, store it in S3, record the page count.

Runs inside the upload request (`runs_in_request`), so the PDF is readable
before the response returns. The upload route calls:

    try:
        stored = await store_source(session, paper, pdf_bytes, filename)
    except InvalidPdfError as exc:
        raise HTTPException(400, str(exc))
    ...  # add the ingest_stages rows (source = succeeded), commit;
    # if the commit fails: storage.delete_paper_objects(s3, paper.id)

No page limit. The worker only runs this stage when it is reprocessed: it
re-reads the stored PDF and re-records the page count.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional

from sqlalchemy import update
from sqlalchemy.orm import Session

from app.database.models import Paper
from app.ingest import storage
from app.ingest.config import Resource
from app.ingest.pdf.document import inspect_pdf
from app.ingest.stages.base import Stage, StageContext

if TYPE_CHECKING:
    from app.helpers.s3 import S3Service


@dataclass(frozen=True)
class StoredSource:
    s3_object_key: str
    file_url: str
    page_count: int
    size_in_kb: int


async def store_source(
    session: Session,
    paper: Paper,
    pdf_bytes: bytes,
    filename: Optional[str],
    s3: Optional["S3Service"] = None,
) -> StoredSource:
    """Validate `pdf_bytes`, upload it to `papers/{id}/{filename}.pdf`, and
    set `file_url`, `s3_object_key`, `page_count`, `size_in_kb` on `paper`
    (given an id if it has none) and add it to `session`. Doesn't commit.

    Raises `InvalidPdfError` (a `PermanentError`; message fit for a 400)
    before anything is uploaded when the PDF is empty, corrupt, encrypted
    or has no pages. S3 failures raise `TemporaryError` / `PermanentError`.
    """
    page_count = await asyncio.to_thread(inspect_pdf, pdf_bytes)

    if s3 is None:
        from app.helpers.s3 import s3_service

        s3 = s3_service
    if paper.id is None:
        paper.id = uuid.uuid4()  # pyright: ignore[reportAttributeAccessIssue]
    key = storage.source_key(paper.id, filename)  # pyright: ignore[reportArgumentType]
    await asyncio.to_thread(
        storage.put_bytes, s3, key, pdf_bytes, storage.PDF_CONTENT_TYPE
    )

    stored = StoredSource(
        s3_object_key=key,
        file_url=storage.public_url(s3, key),
        page_count=page_count,
        size_in_kb=len(pdf_bytes) // 1024,
    )
    paper.s3_object_key = stored.s3_object_key  # pyright: ignore[reportAttributeAccessIssue]
    paper.file_url = stored.file_url  # pyright: ignore[reportAttributeAccessIssue]
    paper.page_count = stored.page_count  # pyright: ignore[reportAttributeAccessIssue]
    paper.size_in_kb = stored.size_in_kb  # pyright: ignore[reportAttributeAccessIssue]
    session.add(paper)
    return stored


class Source(Stage[int]):
    name = "source"
    needs = ()
    resource = Resource.CPU
    timeout_s = 60.0
    applies_to_supplementary = True
    runs_in_request = True
    max_attempts = 1

    async def run(self, ctx: StageContext) -> int:
        """Reprocess only: re-validate the stored PDF; returns its page count."""
        pdf_bytes = await storage.load_pdf(ctx)
        return await ctx.cpu(inspect_pdf, pdf_bytes)

    def save(self, session: Session, ctx: StageContext, output: int) -> None:
        session.execute(
            update(Paper).where(Paper.id == ctx.paper_id).values(page_count=output)
        )
