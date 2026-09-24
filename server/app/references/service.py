"""Batch resolution: the owner's library first, then the cache, then lookups.

The library match is never cached (papers come and go); it runs on every
request against the owner's papers, by DOI / arXiv id and by title. The
external resolution (`resolver`) is what `reference_resolutions` caches.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Awaitable, Callable, Collection, Optional, Protocol
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.core.deadline import Deadline
from app.database.models import Paper
from app.ingest.metadata_ids import identifiers_in, tokens
from app.ingest.metadata_lookup import author_on_page, title_matches
from app.ingest.models import MetadataSource
from app.ingest.sources.work_record import Author, WorkRecord
from app.references.models import ReferenceResolution
from app.references.resolver import ENTRY_BUDGET_S, Resolution, resolve_reference
from app.references.schemas import ReferenceEntry, ResolvedReference
from app.references.text import cache_key

logger = logging.getLogger(__name__)

UNRESOLVED_TTL = timedelta(days=7)
BATCH_CONCURRENCY = 6
# The whole request; entries still resolving then come back as `pending`.
BATCH_BUDGET_S = 25.0

Resolver = Callable[[str, Deadline], Awaitable[Resolution]]


async def _default_resolver(text: str, deadline: Deadline) -> Resolution:
    return await resolve_reference(text, deadline=deadline)


# -- cache ----------------------------------------------------------------------


class ReferenceStore(Protocol):
    def get_many(
        self, keys: Collection[str], now: datetime
    ) -> dict[str, ResolvedReference]: ...

    def put_many(
        self, rows: list[tuple[str, str, ResolvedReference]], now: datetime
    ) -> None: ...


def expires_at(reference: ResolvedReference, now: datetime) -> Optional[datetime]:
    """Found: never. Unresolved: in a week (it may be indexed by then)."""
    return now + UNRESOLVED_TTL if reference.kind == "unresolved" else None


class DbReferenceStore:
    def __init__(self, db: Session) -> None:
        self.db = db

    def get_many(
        self, keys: Collection[str], now: datetime
    ) -> dict[str, ResolvedReference]:
        if not keys:
            return {}
        rows = self.db.execute(
            select(ReferenceResolution.key, ReferenceResolution.result).where(
                ReferenceResolution.key.in_(list(keys)),
                (ReferenceResolution.expires_at.is_(None))
                | (ReferenceResolution.expires_at > now),
            )
        ).all()
        found: dict[str, ResolvedReference] = {}
        for key, result in rows:
            try:
                found[key] = ResolvedReference.model_validate(result)
            except ValidationError:
                continue  # an old shape: resolve again
        return found

    def put_many(
        self, rows: list[tuple[str, str, ResolvedReference]], now: datetime
    ) -> None:
        if not rows:
            return
        values = [
            {
                "key": key,
                "entry_text": text,
                "kind": ref.kind,
                "result": ref.model_dump(mode="json"),
                "resolved_at": now,
                "expires_at": expires_at(ref, now),
            }
            for key, text, ref in {row[0]: row for row in rows}.values()
        ]
        stmt = insert(ReferenceResolution).values(values)
        self.db.execute(
            stmt.on_conflict_do_update(
                index_elements=[ReferenceResolution.key],
                set_={
                    "entry_text": stmt.excluded.entry_text,
                    "kind": stmt.excluded.kind,
                    "result": stmt.excluded.result,
                    "resolved_at": stmt.excluded.resolved_at,
                    "expires_at": stmt.excluded.expires_at,
                    "updated_at": func.now(),
                },
            )
        )
        self.db.commit()


# -- library ----------------------------------------------------------------------


@dataclass(frozen=True)
class LibraryPaper:
    id: UUID
    title: str
    authors: tuple[str, ...] = ()
    abstract: Optional[str] = None
    doi: Optional[str] = None
    arxiv_id: Optional[str] = None
    year: Optional[int] = None
    venue: Optional[str] = None
    preview_url: Optional[str] = None


def load_library(db: Session, user_id: UUID) -> list[LibraryPaper]:
    rows = db.execute(
        select(
            Paper.id,
            Paper.title,
            Paper.authors,
            Paper.abstract,
            Paper.doi,
            Paper.arxiv_id,
            Paper.publish_date,
            Paper.journal,
            Paper.preview_url,
        ).where(
            Paper.user_id == user_id,
            Paper.supplementary_of_paper_id.is_(None),
            Paper.title.isnot(None),
        )
    ).all()
    return [
        LibraryPaper(
            id=row.id,
            title=row.title,
            authors=tuple(row.authors or ()),
            abstract=row.abstract,
            doi=(row.doi or "").lower() or None,
            arxiv_id=(row.arxiv_id or "").lower() or None,
            year=row.publish_date.year if row.publish_date else None,
            venue=row.journal,
            preview_url=row.preview_url,
        )
        for row in rows
    ]


def match_library(
    entry_text: str,
    reference: Optional[ResolvedReference],
    library: list[LibraryPaper],
) -> Optional[LibraryPaper]:
    """The owner's paper this entry cites: same DOI / arXiv id (from the
    entry or its resolution), or its title in the entry plus an author's
    surname (the same check ingest verifies metadata with)."""
    if not library:
        return None
    ids = {(i.kind, i.value.lower()) for i in identifiers_in(entry_text, "entry")}
    if reference is not None:
        if reference.doi:
            ids.add(("doi", reference.doi.lower()))
        if reference.arxiv_id:
            ids.add(("arxiv", reference.arxiv_id.lower()))
    for paper in library:
        if (paper.doi and ("doi", paper.doi) in ids) or (
            paper.arxiv_id and ("arxiv", paper.arxiv_id) in ids
        ):
            return paper

    entry_words = set(tokens(entry_text))
    resolved_title = tokens(reference.title) if reference is not None else None
    for paper in library:
        title = tokens(paper.title)
        if len(title) < 3:
            continue
        if (
            reference is not None
            and reference.kind == "paper"
            and title == resolved_title
        ):
            return paper  # e.g. the entry misspells the title, the record doesn't
        # Cheap filter before the fuzzy in-order match.
        if sum(word in entry_words for word in title) < 0.8 * len(title):
            continue
        if not title_matches(paper.title, entry_text):
            continue
        if paper.authors:
            record = WorkRecord(
                source=MetadataSource.USER,
                authors=[Author(name=name) for name in paper.authors],
            )
            if author_on_page(record, entry_text):
                return paper
        elif len(title) >= 5:
            return paper
    return None


def library_reference(
    paper: LibraryPaper, reference: Optional[ResolvedReference]
) -> ResolvedReference:
    """The library paper, gaps filled from the external resolution."""
    ref = reference or ResolvedReference(kind="unresolved")
    return ResolvedReference(
        kind="library",
        source="library",
        title=paper.title,
        authors=list(paper.authors) or ref.authors,
        year=paper.year or ref.year,
        venue=paper.venue or ref.venue,
        abstract=paper.abstract or ref.abstract,
        doi=paper.doi or ref.doi,
        arxiv_id=paper.arxiv_id or ref.arxiv_id,
        url=ref.url,
        library_paper_id=paper.id,
        preview_url=paper.preview_url,
    )


# -- the batch ------------------------------------------------------------------------


@dataclass
class BatchResult:
    results: dict[str, ResolvedReference]
    # Keys not resolved in time, or whose lookup failed transiently.
    pending: list[str]


async def resolve_entries(
    entries: list[ReferenceEntry],
    *,
    store: ReferenceStore,
    library: list[LibraryPaper],
    refresh: bool = False,
    resolver: Resolver = _default_resolver,
    concurrency: int = BATCH_CONCURRENCY,
    budget_s: float = BATCH_BUDGET_S,
    now: Optional[datetime] = None,
) -> BatchResult:
    now = now or datetime.now(timezone.utc)
    hashes = {entry.key: cache_key(entry.text) for entry in entries}
    texts: dict[str, str] = {}
    for entry in entries:
        texts.setdefault(hashes[entry.key], entry.text)

    cached = {} if refresh else store.get_many(list(texts), now)
    in_library = {h: match_library(t, cached.get(h), library) for h, t in texts.items()}
    misses = [h for h in texts if h not in cached and in_library[h] is None]
    resolved = await _resolve_all(
        {h: texts[h] for h in misses}, resolver, concurrency, budget_s
    )
    store.put_many(
        [(h, texts[h], r.reference) for h, r in resolved.items() if not r.transient],
        now,
    )

    external = {**cached, **{h: r.reference for h, r in resolved.items()}}
    results: dict[str, ResolvedReference] = {}
    pending: list[str] = []
    for entry in entries:
        h = hashes[entry.key]
        ref = external.get(h)
        paper = in_library[h] or match_library(entry.text, ref, library)
        if paper is not None:
            results[entry.key] = library_reference(paper, ref)
            continue
        attempt = resolved.get(h)
        if ref is None or (attempt and attempt.transient and ref.kind == "unresolved"):
            pending.append(entry.key)  # "don't know yet", not "no match"
        else:
            results[entry.key] = ref
    return BatchResult(results, pending)


async def _resolve_all(
    texts: dict[str, str], resolver: Resolver, concurrency: int, budget_s: float
) -> dict[str, Resolution]:
    if not texts:
        return {}
    batch = Deadline(budget_s)
    gate = asyncio.Semaphore(concurrency)

    async def one(text: str) -> Resolution:
        async with gate:
            budget = min(ENTRY_BUDGET_S, batch.remaining())
            return await resolver(text, Deadline(budget))

    tasks = {h: asyncio.create_task(one(text)) for h, text in texts.items()}
    _, pending = await asyncio.wait(tasks.values(), timeout=budget_s)
    for task in pending:
        task.cancel()
    await asyncio.gather(*pending, return_exceptions=True)

    out: dict[str, Resolution] = {}
    for h, task in tasks.items():
        if task.cancelled() or not task.done():
            continue
        error = task.exception()
        if error is not None:
            logger.warning("Reference resolution failed: %r", error)
            continue
        out[h] = task.result()
    return out
