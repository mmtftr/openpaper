"""Crossref REST API (api.crossref.org): look up a DOI, search by title.

Polite pool: `crossref_params()` adds `mailto` when `CONTACT_EMAIL` is set,
and the shared client's User-Agent carries it too.
"""

from __future__ import annotations

from typing import Any, Optional
from urllib.parse import quote

import httpx

from app.core.deadline import Deadline
from app.core.http import crossref_params
from app.ingest.models import MetadataSource
from app.ingest.sources.fetch import get_or_none
from app.ingest.sources.work_record import (
    Author,
    WorkRecord,
    clean_abstract,
    clean_text,
    date_from_parts,
    dedupe,
)

BASE_URL = "https://api.crossref.org/works"

# Fields a search result needs (single-DOI lookups can't `select`).
_SEARCH_SELECT = (
    "DOI,title,subtitle,author,container-title,publisher,published,issued,"
    "published-print,published-online,abstract,subject,type"
)


async def get_work(
    client: httpx.AsyncClient, doi: str, deadline: Deadline
) -> Optional[WorkRecord]:
    """The record for `doi`, or None if Crossref doesn't know it (e.g.
    DataCite DOIs: arXiv, Zenodo)."""
    response = await get_or_none(
        client,
        f"{BASE_URL}/{quote(doi, safe='/')}",
        params=crossref_params(),
        deadline=deadline,
        what=f"Crossref {doi}",
    )
    if response is None:
        return None
    message = response.json().get("message")
    return parse_work(message) if isinstance(message, dict) else None


async def search(
    client: httpx.AsyncClient, title: str, deadline: Deadline, rows: int = 5
) -> list[WorkRecord]:
    """Best `rows` matches for `title`, most relevant first."""
    response = await get_or_none(
        client,
        BASE_URL,
        params=crossref_params(
            **{"query.bibliographic": title, "rows": rows, "select": _SEARCH_SELECT}
        ),
        deadline=deadline,
        what="Crossref title search",
    )
    if response is None:
        return []
    items = (response.json().get("message") or {}).get("items") or []
    return [parse_work(item) for item in items if isinstance(item, dict)]


def parse_work(message: dict[str, Any]) -> WorkRecord:
    """A Crossref `message` (one work) -> `WorkRecord`."""
    title = clean_text(_first(message.get("title")))
    subtitle = clean_text(_first(message.get("subtitle")))
    if title and subtitle and subtitle.lower() not in title.lower():
        title = f"{title}: {subtitle}"

    authors: list[Author] = []
    affiliations: list[str] = []
    for person in message.get("author") or []:
        family = clean_text(person.get("family"))
        given = clean_text(person.get("given"))
        name = " ".join(p for p in (given, family) if p) or clean_text(
            person.get("name")  # organisations ("The XYZ Consortium")
        )
        if name:
            authors.append(Author(name=name, family=family))
        affiliations += [a.get("name") or "" for a in person.get("affiliation") or []]

    doi = message.get("DOI")
    return WorkRecord(
        source=MetadataSource.CROSSREF,
        title=title,
        authors=authors,
        abstract=clean_abstract(message.get("abstract")),
        publish_date=_published(message),
        journal=clean_text(_first(message.get("container-title"))),
        publisher=clean_text(message.get("publisher")),
        doi=doi.lower() if isinstance(doi, str) else None,
        keywords=dedupe(list(message.get("subject") or [])),
        institutions=dedupe(affiliations),
        work_type=message.get("type"),
    )


def _first(values: Any) -> Optional[str]:
    if isinstance(values, list) and values:
        return values[0] if isinstance(values[0], str) else None
    return values if isinstance(values, str) else None


def _published(message: dict[str, Any]):
    # `published` = the earliest of print/online; the others are fallbacks
    # for records deposited before Crossref added it.
    for key in ("published", "issued", "published-print", "published-online"):
        parts = (message.get(key) or {}).get("date-parts") or []
        if parts and parts[0] and parts[0][0]:
            return date_from_parts([int(p) for p in parts[0] if p is not None])
    return None
