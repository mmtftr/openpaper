"""OpenAlex API (api.openalex.org): look up a DOI, search by title.

`openalex_params()` adds `OPENALEX_API_KEY` / `mailto` when configured.

OpenAlex's `keywords`/`topics` are machine-assigned and often off
("Action (physics)" for a metabolism paper), so they are not used as the
paper's keywords.
"""

from __future__ import annotations

import re
from typing import Any, Optional
from urllib.parse import quote

import httpx

from app.core.deadline import Deadline
from app.core.http import openalex_params
from app.ingest.models import MetadataSource
from app.ingest.sources.fetch import get_or_none
from app.ingest.sources.work_record import (
    Author,
    WorkRecord,
    clean_text,
    dedupe,
    display_author,
    parse_date,
)

BASE_URL = "https://api.openalex.org/works"

_SELECT = (
    "id,doi,title,display_name,publication_date,authorships,primary_location,"
    "abstract_inverted_index,ids,type,best_oa_location"
)


async def get_work(
    client: httpx.AsyncClient, doi: str, deadline: Deadline
) -> Optional[WorkRecord]:
    """The work with this DOI, or None."""
    response = await get_or_none(
        client,
        f"{BASE_URL}/doi:{quote(doi, safe='/')}",
        params=openalex_params(select=_SELECT),
        deadline=deadline,
        what=f"OpenAlex {doi}",
    )
    return parse_work(response.json()) if response is not None else None


async def search(
    client: httpx.AsyncClient, title: str, deadline: Deadline, rows: int = 5
) -> list[WorkRecord]:
    """Best `rows` matches for `title`, most relevant first."""
    response = await get_or_none(
        client,
        BASE_URL,
        params=openalex_params(search=title, per_page=rows, select=_SELECT),
        deadline=deadline,
        what="OpenAlex title search",
    )
    if response is None:
        return []
    results = response.json().get("results") or []
    return [parse_work(work) for work in results if isinstance(work, dict)]


async def title_search(
    client: httpx.AsyncClient, title: str, deadline: Deadline, rows: int = 5
) -> list[WorkRecord]:
    """Works whose *title* matches `title` (`filter=title.search`).

    Much more precise for a known title than `search`, which ranks by
    full-text relevance and citations (a short ML title comes back buried
    under famous papers), and cheaper against the API budget.
    """
    # `,` separates filters and `|` means OR in a filter value.
    query = " ".join(re.sub(r"[,|:]", " ", title).split())
    response = await get_or_none(
        client,
        BASE_URL,
        params=openalex_params(
            filter=f"title.search:{query}", per_page=rows, select=_SELECT
        ),
        deadline=deadline,
        what="OpenAlex title filter",
    )
    if response is None:
        return []
    results = response.json().get("results") or []
    return [parse_work(work) for work in results if isinstance(work, dict)]


def parse_work(work: dict[str, Any]) -> WorkRecord:
    """An OpenAlex work object -> `WorkRecord`."""
    authors: list[Author] = []
    institutions: list[str] = []
    for authorship in work.get("authorships") or []:
        name = clean_text((authorship.get("author") or {}).get("display_name"))
        name = name or clean_text(authorship.get("raw_author_name"))
        if name:
            authors.append(display_author(name))
        institutions += [
            inst.get("display_name") or ""
            for inst in authorship.get("institutions") or []
        ]

    primary = work.get("primary_location") or {}
    source = primary.get("source") or {}
    doi = strip_doi_url(work.get("doi"))
    arxiv_id = None
    if doi and doi.startswith("10.48550/arxiv."):
        arxiv_id = doi.removeprefix("10.48550/arxiv.")
    openalex_id = (work.get("id") or "").rsplit("/", 1)[-1] or None

    return WorkRecord(
        source=MetadataSource.OPENALEX,
        title=clean_text(work.get("title") or work.get("display_name")),
        authors=authors,
        abstract=abstract_from_index(work.get("abstract_inverted_index")),
        publish_date=parse_date(work.get("publication_date")),
        journal=clean_text(source.get("display_name")),
        publisher=clean_text(source.get("host_organization_name")),
        doi=doi,
        arxiv_id=arxiv_id,
        openalex_id=openalex_id,
        institutions=dedupe(institutions),
        work_type=work.get("type"),
        pdf_url=(work.get("best_oa_location") or {}).get("pdf_url")
        or primary.get("pdf_url"),
    )


def strip_doi_url(value: Any) -> Optional[str]:
    """'https://doi.org/10.1/ABC' -> '10.1/abc'."""
    if not isinstance(value, str) or not value:
        return None
    doi = value.split("doi.org/", 1)[-1].strip().lower()
    return doi or None


def abstract_from_index(index: Any) -> Optional[str]:
    """Rebuild the abstract from OpenAlex's inverted index {word: [pos...]}."""
    if not isinstance(index, dict) or not index:
        return None
    positions: dict[int, str] = {}
    for word, spots in index.items():
        for spot in spots or []:
            positions[int(spot)] = word
    return clean_text(" ".join(positions[i] for i in sorted(positions)))
