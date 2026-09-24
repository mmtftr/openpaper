"""`metadata` stage: identify the paper and fetch its record (design §5).

Candidates, best first: DOIs / arXiv ids in the PDF's embedded metadata,
the filename, the source URL, then pages 1-2 of the text layer (header
area first); after those, a title search on Crossref + OpenAlex with the
page-1 title guess. The first record that verifies against page 1 (title
matches, an author's surname appears) is written to `papers`, and
`metadata_fallback` is marked skipped so title/authors don't wait for OCR.

No verified record: the stage still succeeds, writes nothing, and
`metadata_fallback` runs once OCR is done.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from typing import Optional

from sqlalchemy.orm import Session

from app.core.errors import classify
from app.core.http import shared_client
from app.ingest import metadata_ids as ids
from app.ingest import storage
from app.ingest.config import Resource
from app.ingest.metadata_ids import Identifier, PdfHints
from app.ingest.metadata_lookup import (
    Attempt,
    FieldValues,
    resolve,
    skip_fallback,
    write_fields,
)
from app.ingest.models import MetadataSource
from app.ingest.stages.base import Stage, StageContext

# Lookup errors (API down) retry the stage this many attempts in total
# before it gives up and lets the fallback take over.
LOOKUP_ERROR_ATTEMPTS = 3


@dataclass
class PageText:
    text_layer: str = ""
    markdown: str = ""  # final OCR text (empty until ocr_repair ran)


@dataclass
class MetadataInputs:
    pages: dict[int, PageText]  # page_no (1-based) -> text, first pages only
    s3_key: Optional[str] = None
    # What the upload recorded: the original file name / the URL the PDF
    # was fetched from (`papers.source_filename` / `source_url`).
    filename: Optional[str] = None
    source_url: Optional[str] = None

    def page(self, page_no: int) -> PageText:
        return self.pages.get(page_no) or PageText()


@dataclass
class MetadataOutput:
    fields: FieldValues = field(default_factory=dict)  # empty = not resolved
    via: Optional[str] = None  # what resolved it, for the skip reason / log


def load_inputs(
    session: Session, paper_id: uuid.UUID, pages: int = 3
) -> MetadataInputs:
    from app.database.models import Paper
    from app.ingest.models import PaperPage

    paper = session.get(Paper, paper_id)
    rows = (
        session.query(PaperPage)
        .filter(PaperPage.paper_id == paper_id, PaperPage.page_no <= pages)
        .all()
    )
    s3_key = str(paper.s3_object_key) if paper and paper.s3_object_key else None
    filename = str(paper.source_filename) if paper and paper.source_filename else None
    source_url = str(paper.source_url) if paper and paper.source_url else None
    return MetadataInputs(
        pages={
            row.page_no: PageText(
                text_layer=row.text_layer or "",
                markdown=row.markdown or row.ocr_markdown or "",
            )
            for row in rows
        },
        s3_key=s3_key,
        filename=filename or (s3_key.rsplit("/", 1)[-1] if s3_key else None),
        source_url=source_url,
    )


async def load_pdf_hints(ctx: StageContext, s3_key: Optional[str]) -> PdfHints:
    """The PDF's embedded metadata and layout title guess.

    Best effort: an unreadable PDF or S3 hiccup costs these candidates, not
    the stage (the text layer still has the page text).
    """
    if not s3_key:
        return PdfHints()
    try:
        data = await asyncio.to_thread(storage.get_bytes, ctx.get_s3(), s3_key)
        return await ctx.cpu(ids.read_pdf_hints, data)
    except Exception as exc:
        ctx.log.warning(
            "Could not read the PDF's own metadata: %s", classify(exc).message
        )
        return PdfHints()


def identifier_candidates(
    hints: PdfHints, inputs: MetadataInputs, page_texts: list[str]
) -> list[Identifier]:
    """Every identifier, best first (design §5 order), without repeats."""
    return ids.unique(
        [
            *hints.identifiers,
            *ids.filename_identifiers(inputs.filename),
            *ids.url_identifiers(inputs.source_url),
            *ids.page_identifiers(page_texts),
        ]
    )


def title_candidates(*guesses: Optional[str]) -> list[str]:
    """Non-empty guesses, first of each normalized form."""
    seen: set[str] = set()
    out: list[str] = []
    for guess in guesses:
        key = " ".join(ids.tokens(guess))
        if guess and key and key not in seen:
            seen.add(key)
            out.append(guess)
    return out


def with_embedded_keywords(fields: FieldValues, hints: PdfHints) -> FieldValues:
    """Author-supplied PDF keywords fill in when the record has none."""
    if "keywords" not in fields and hints.embedded_keywords:
        return {
            **fields,
            "keywords": (hints.embedded_keywords, MetadataSource.EMBEDDED),
        }
    return fields


class Metadata(Stage[MetadataOutput]):
    name = "metadata"
    needs = ("text_layer",)
    resource = Resource.NETWORK
    timeout_s = 60.0

    async def run(self, ctx: StageContext) -> MetadataOutput:
        inputs = await ctx.read(lambda s: load_inputs(s, ctx.paper_id, pages=2))
        hints = await load_pdf_hints(ctx, inputs.s3_key)
        page1 = inputs.page(1).text_layer
        page2 = inputs.page(2).text_layer

        attempt = Attempt()
        resolution = await resolve(
            shared_client(),
            ctx.deadline,
            identifiers=identifier_candidates(hints, inputs, [page1, page2]),
            titles=title_candidates(
                hints.layout_title, hints.embedded_title, ids.title_from_text(page1)
            ),
            page1=page1,
            attempt=attempt,
        )
        if resolution is None:
            error = attempt.retryable_error
            if error is not None and ctx.attempt < LOOKUP_ERROR_ATTEMPTS:
                raise error  # an API was down: worth another go before the LLM
            ctx.log.info("No verified record; tried: %s", "; ".join(attempt.tried))
            return MetadataOutput()

        ctx.log.info("Resolved by %s", resolution.via)
        return MetadataOutput(
            fields=with_embedded_keywords(resolution.fields(), hints),
            via=resolution.via,
        )

    def save(self, session: Session, ctx: StageContext, output: MetadataOutput) -> None:
        if not output.fields:
            return  # not resolved: metadata_fallback runs after OCR
        write_fields(session, ctx.paper_id, output.fields)
        skip_fallback(session, ctx.paper_id, f"Found by lookup: {output.via}")
