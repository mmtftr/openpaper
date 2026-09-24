"""Metadata lookup shared by the `metadata` and `metadata_fallback` stages.

    candidates  = identifiers (best-first) + title guesses   (metadata_ids)
    resolve()   = look each identifier up, then search the titles, until a
                  record VERIFIES against page 1
    write_fields() = copy the merged record onto `papers`, skipping fields
                  the owner edited

A record verifies when its title matches the page-1 text (normalized, fuzzy)
and at least one author's surname appears on page 1 (design §5). That check
is what makes it safe to try uncertain candidates — a DOI from a citation,
a title-search hit for a different paper — and discard the misses.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from difflib import SequenceMatcher
from typing import Any, Awaitable, Optional

import httpx
from sqlalchemy import update
from sqlalchemy.orm import Session

from app.core.deadline import Deadline
from app.core.errors import classify
from app.ingest.metadata_ids import Identifier, tokens
from app.ingest.models import (
    METADATA_FIELDS,
    IngestStage,
    MetadataSource,
    StageStatus,
)
from app.ingest.sources import arxiv, crossref, openalex
from app.ingest.sources.work_record import WorkRecord

logger = logging.getLogger(__name__)

# Share of the title's words that must appear, in order, on page 1.
TITLE_MATCH_THRESHOLD = 0.85
# Bounds on how much a stage tries before giving up (each costs 1-2 calls).
MAX_IDENTIFIERS = 4
MAX_TITLE_GUESSES = 2
SEARCH_ROWS = 5
# {field: (value, where it came from)} — what the stages write.
FieldValues = dict[str, tuple[Any, MetadataSource]]

# Records of these types share a paper's title without being the paper.
_SKIP_TYPES = frozenset({"peer-review", "component", "dataset", "grant", "erratum"})


# -- verification -------------------------------------------------------------


def title_matches(title: Optional[str], page_text: str) -> bool:
    """Does `title` appear (near enough) in `page_text`?

    Exact normalized substring, or, allowing for OCR/markup noise (a Greek
    letter, a LaTeX command, a dropped subtitle word), at least
    `TITLE_MATCH_THRESHOLD` of the title's tokens in order within a window
    about the title's length.
    """
    wanted = tokens(title)
    page = tokens(page_text)
    if not wanted or not page:
        return False
    if f" {' '.join(wanted)} " in f" {' '.join(page)} ":
        return True
    if len(wanted) < 3:
        return False  # too short to judge fuzzily
    window = len(wanted) + max(3, len(wanted) // 2)
    first_words = set(wanted[:3])
    best = 0
    for start, word in enumerate(page):
        if word not in first_words:
            continue
        blocks = SequenceMatcher(
            None, wanted, page[start : start + window], autojunk=False
        ).get_matching_blocks()
        best = max(best, sum(block.size for block in blocks))
    return best / len(wanted) >= TITLE_MATCH_THRESHOLD


def author_on_page(record: WorkRecord, page_text: str) -> bool:
    """Does at least one author's surname appear on the page?

    PDF text glues affiliation marks onto names ("Luoa,b", "Nielsen1"), so
    a page word may also be a surname plus up to two trailing characters.
    """
    page = set(tokens(page_text))
    page |= {word[:-cut] for word in page for cut in (1, 2) if len(word) - cut >= 3}
    for author in record.authors:
        surname = tokens(author.family) or tokens(author.name)[-1:]
        if any(len(part) >= 2 and part in page for part in surname):
            return True
    return False


def verifies(record: Optional[WorkRecord], page1: str) -> bool:
    return (
        record is not None
        and title_matches(record.title, page1)
        and author_on_page(record, page1)
    )


# -- resolution -----------------------------------------------------------------


@dataclass
class Resolution:
    """A verified lookup: the records to merge, best first."""

    records: list[WorkRecord]
    via: str  # "DOI 10.1/x (page 1 header)", "title search" ... (for the log)

    def fields(self) -> FieldValues:
        return merge(self.records)


@dataclass
class Attempt:
    """Everything one `resolve()` run tried (for the log and the retry rule)."""

    tried: list[str] = field(default_factory=list)
    errors: list[BaseException] = field(default_factory=list)

    @property
    def retryable_error(self) -> Optional[BaseException]:
        return next((e for e in self.errors if classify(e).retryable), None)


async def resolve(
    client: httpx.AsyncClient,
    deadline: Deadline,
    *,
    identifiers: list[Identifier],
    titles: list[str],
    page1: str,
    attempt: Optional[Attempt] = None,
) -> Optional[Resolution]:
    """The first candidate that verifies against `page1`, or None.

    Lookup errors are recorded on `attempt` and treated as "not found" so
    one API being down doesn't stop the other candidates.
    """
    attempt = attempt if attempt is not None else Attempt()
    if not tokens(page1):
        attempt.tried.append("no page-1 text to verify against")
        return None

    for ident in identifiers[:MAX_IDENTIFIERS]:
        attempt.tried.append(str(ident))
        found = await lookup(client, deadline, ident, attempt)
        verified = [record for record in found if verifies(record, page1)]
        if verified:
            return Resolution(verified, via=str(ident))

    for title in titles[:MAX_TITLE_GUESSES]:
        attempt.tried.append(f"title search {title!r}")
        hits = await _gather(
            attempt,
            crossref.search(client, title, deadline, rows=SEARCH_ROWS),
            openalex.search(client, title, deadline, rows=SEARCH_ROWS),
        )
        for hit in [h for found in hits for h in found or []]:
            if hit.work_type in _SKIP_TYPES or not verifies(hit, page1):
                continue
            # Re-resolve by id: that gathers every source for the paper.
            ident = _identifier_of(hit)
            if ident is not None:
                found = await lookup(client, deadline, ident, attempt)
                verified = [record for record in found if verifies(record, page1)]
                if verified:
                    return Resolution(verified, via=f"title search → {ident}")
            return Resolution([hit], via=f"title search ({hit.source.value})")
    return None


async def lookup(
    client: httpx.AsyncClient,
    deadline: Deadline,
    ident: Identifier,
    attempt: Attempt,
) -> list[WorkRecord]:
    """Every source's record for one identifier, in merge priority order."""
    if ident.kind == "arxiv":
        results = await _gather(
            attempt,
            arxiv.get_paper(client, ident.value, deadline),
            openalex.get_work(client, arxiv.arxiv_doi(ident.value), deadline),
        )
    else:
        results = await _gather(
            attempt,
            crossref.get_work(client, ident.value, deadline),
            openalex.get_work(client, ident.value, deadline),
        )
    return [record for record in results if record is not None]


def _identifier_of(record: WorkRecord) -> Optional[Identifier]:
    if record.arxiv_id:
        return Identifier("arxiv", record.arxiv_id, "title search")
    if record.doi:
        return Identifier("doi", record.doi, "title search")
    return None


async def _gather(attempt: Attempt, *calls: Awaitable[Any]) -> list[Any]:
    """Run lookups concurrently; a failed one is recorded and gives None."""
    out: list[Any] = []
    for result in await asyncio.gather(*calls, return_exceptions=True):
        if isinstance(result, BaseException):
            if not isinstance(result, Exception):
                raise result  # cancellation
            logger.warning("Metadata lookup failed: %s", classify(result).message)
            attempt.errors.append(result)
            result = None
        out.append(result)
    return out


# -- merging and writing ------------------------------------------------------------


# Fields where one source is better than the usual priority order: OpenAlex
# institutions are normalized ("Chalmers University of Technology"), Crossref
# affiliations are raw strings ("Department of ..., Chalmers ..., Sweden").
_PREFERRED_SOURCE = {"institutions": MetadataSource.OPENALEX}


def merge(records: list[WorkRecord]) -> FieldValues:
    """Per field, the first record (priority order) that has a value."""
    fields: FieldValues = {}
    for name in METADATA_FIELDS:
        preferred = _PREFERRED_SOURCE.get(name)
        ordered = sorted(records, key=lambda r: r.source is not preferred)
        for record in ordered:
            value = getattr(record, name, None)
            if name == "authors":
                value = [author.name for author in record.authors]
            if value:
                fields[name] = (value, record.source)
                break
    return fields


def write_fields(
    session: Session,
    paper_id: Any,
    fields: FieldValues,
) -> list[str]:
    """Copy `fields` onto the paper; never touches a field whose
    `metadata_source` is "user". Returns the fields written."""
    from app.database.models import Paper

    paper = session.get(Paper, paper_id)
    if paper is None:
        return []
    sources: dict[str, str] = dict(paper.metadata_source or {})
    written: list[str] = []
    for name, (value, source) in fields.items():
        if name not in METADATA_FIELDS or not value:
            continue
        if sources.get(name) == MetadataSource.USER.value:
            continue
        if isinstance(value, date) and not isinstance(value, datetime):
            value = datetime(value.year, value.month, value.day)
        setattr(paper, name, value)
        sources[name] = MetadataSource(source).value
        written.append(name)
    # Reassign (not mutate) so SQLAlchemy sees the JSONB change.
    paper.metadata_source = sources
    paper.attempted_metadata_at = datetime.now(timezone.utc)
    return written


def skip_fallback(session: Session, paper_id: Any, reason: str) -> None:
    """Mark `metadata_fallback` skipped (the lookup already resolved)."""
    session.execute(
        update(IngestStage)
        .where(
            IngestStage.paper_id == paper_id,
            IngestStage.name == "metadata_fallback",
            IngestStage.status.in_(
                [StageStatus.PENDING, StageStatus.BLOCKED, StageStatus.QUEUED]
            ),
        )
        .values(
            status=StageStatus.SKIPPED,
            error_message=reason,
            error_kind=None,
            next_attempt_at=None,
            finished_at=datetime.now(timezone.utc),
        )
    )
