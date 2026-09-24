"""`metadata_fallback` stage: when the lookup found nothing (design §5).

`metadata` marks this stage skipped when it resolves a record, so it only
runs for papers the text layer couldn't identify — typically scans (no
text layer) or papers without a DOI whose title guess missed.

1. Retry the lookup with the OCR text: identifiers in the OCR markdown of
   pages 1-2, the markdown's first heading as the title, and page 1 = text
   layer + OCR markdown to verify against.
2. Else ask the `ingest.metadata` model to read the first pages (the prompt
   and fields of the old `jobs/` extraction, minus highlights), then try
   one title search with the title it read.
3. Else write the model's fields as `metadata_source = "llm"` (unverified).
"""

from __future__ import annotations

import asyncio
from typing import Optional

from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.http import shared_client
from app.ingest import metadata_ids as ids
from app.ingest.config import Resource
from app.ingest.metadata_lookup import Attempt, FieldValues, resolve, write_fields
from app.ingest.models import MetadataSource
from app.ingest.sources.work_record import clean_text, dedupe, parse_date
from app.ingest.stages.base import Stage, StageContext, skip
from app.ingest.stages.metadata import (
    MetadataInputs,
    MetadataOutput,
    identifier_candidates,
    load_inputs,
    load_pdf_hints,
    title_candidates,
    with_embedded_keywords,
)
from app.llm import oneshot

# The model reads this many leading pages (title page + abstract/affiliations).
LLM_PAGES = 3
LLM_MAX_CHARS = 40_000
LOOKUP_SOURCES = {
    MetadataSource.CROSSREF,
    MetadataSource.OPENALEX,
    MetadataSource.ARXIV,
}

# Ported from jobs/src/prompts.py (SYSTEM_INSTRUCTIONS_CACHE +
# EXTRACT_METADATA_PROMPT_TEMPLATE).
INSTRUCTIONS = """\
You are a metadata extraction assistant. Your task is to extract specific \
information from the provided academic paper content. Pay special attention \
to the details and ensure accuracy in the extracted metadata.

Always think step-by-step when making a determination with respect to the \
contents of the paper. If you are unsure about a specific field, provide a \
best guess based on the content available; leave a field empty if the \
content doesn't say.
"""

PROMPT_TEMPLATE = """\
Paper Content:

{content}

Please extract the following fields and structure them according to the \
provided schema: title, authors, abstract, publish date, institutions and \
keywords.
"""


class ExtractedMetadata(BaseModel):
    """The old jobs schemas `TitleAuthorsAbstract` + `InstitutionsKeywords`."""

    title: str = Field(description="Title of the paper **in normal case**")
    authors: list[str] = Field(default=[], description="List of authors")
    abstract: str = Field(default="", description="Abstract of the paper")
    publish_date: Optional[str] = Field(
        default=None, description="Publishing date of the paper in YYYY-MM-DD format"
    )
    institutions: list[str] = Field(
        default=[], description="List of institutions involved in the publication."
    )
    keywords: list[str] = Field(default=[], description="List of keywords")


def llm_fields(extracted: ExtractedMetadata) -> FieldValues:
    values = {
        "title": clean_text(extracted.title),
        "authors": dedupe(extracted.authors),
        "abstract": clean_text(extracted.abstract),
        "publish_date": parse_date(extracted.publish_date),
        "institutions": dedupe(extracted.institutions),
        "keywords": dedupe(extracted.keywords),
    }
    return {
        name: (value, MetadataSource.LLM) for name, value in values.items() if value
    }


def llm_content(inputs: MetadataInputs) -> str:
    pages = []
    for page_no in range(1, LLM_PAGES + 1):
        page = inputs.page(page_no)
        text = page.markdown or page.text_layer
        if text:
            pages.append(text)
    return "\n\n".join(pages)[:LLM_MAX_CHARS]


def _resolved_by_lookup(session: Session, paper_id) -> bool:
    from app.database.models import Paper

    paper = session.get(Paper, paper_id)
    sources = paper.metadata_source if paper is not None else None
    if not isinstance(sources, dict):
        return False
    return sources.get("title") in {s.value for s in LOOKUP_SOURCES}


class MetadataFallback(Stage[MetadataOutput]):
    name = "metadata_fallback"
    needs = ("metadata", "ocr_repair")
    resource = Resource.LLM
    timeout_s = 180.0
    model_slot = "ingest.metadata"

    def check_config(self) -> None:
        # The model is only needed if the OCR-text lookup misses too, so it
        # is resolved (ConfigError if unset) right before the call instead.
        return None

    async def run(self, ctx: StageContext) -> MetadataOutput:
        if await ctx.read(lambda s: _resolved_by_lookup(s, ctx.paper_id)):
            skip("Metadata was already found by lookup")

        inputs = await ctx.read(lambda s: load_inputs(s, ctx.paper_id, LLM_PAGES))
        hints = await load_pdf_hints(ctx, inputs.s3_key)
        md1, md2 = inputs.page(1).markdown, inputs.page(2).markdown
        page1 = f"{inputs.page(1).text_layer}\n{md1}"
        tried_titles = title_candidates(
            ids.title_from_markdown(md1), hints.layout_title, hints.embedded_title
        )

        client = shared_client()
        resolution = await resolve(
            client,
            ctx.deadline,
            identifiers=identifier_candidates(hints, inputs, [md1, md2]),
            titles=tried_titles,
            page1=page1,
            attempt=Attempt(),
        )
        if resolution is not None:
            ctx.log.info("Resolved from the OCR text by %s", resolution.via)
            fields = with_embedded_keywords(resolution.fields(), hints)
            return MetadataOutput(fields, via=resolution.via)

        content = llm_content(inputs)
        if not content.strip():
            skip("No text to read metadata from (empty text layer and OCR)")
        self.resolve_model(ctx)  # ConfigError if the slot has no model
        async with asyncio.timeout(ctx.deadline.timeout()):
            extracted = await oneshot.complete(
                "ingest.metadata",
                PROMPT_TEMPLATE.format(content=content),
                output_type=ExtractedMetadata,
                instructions=INSTRUCTIONS,
            )

        # The model reads titles better than the heuristics: one more search.
        llm_title = ids.plausible_title(extracted.title)
        tried = {tuple(ids.tokens(title)) for title in tried_titles}
        if llm_title and tuple(ids.tokens(llm_title)) not in tried:
            resolution = await resolve(
                client, ctx.deadline, identifiers=[], titles=[llm_title], page1=page1
            )
            if resolution is not None:
                ctx.log.info("Resolved by %s with the model's title", resolution.via)
                fields = with_embedded_keywords(resolution.fields(), hints)
                return MetadataOutput(fields, via=resolution.via)

        return MetadataOutput(llm_fields(extracted), via="LLM extraction (unverified)")

    def save(self, session: Session, ctx: StageContext, output: MetadataOutput) -> None:
        write_fields(session, ctx.paper_id, output.fields)
