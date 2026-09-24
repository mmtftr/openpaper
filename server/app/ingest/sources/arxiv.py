"""arXiv API (export.arxiv.org/api/query): look up one arXiv id (Atom feed)."""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from typing import Optional

import httpx

from app.core.deadline import Deadline
from app.ingest.models import MetadataSource
from app.ingest.sources.fetch import get_or_none
from app.ingest.sources.work_record import (
    Author,
    WorkRecord,
    clean_text,
    parse_date,
)

API_URL = "https://export.arxiv.org/api/query"

_NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "arxiv": "http://arxiv.org/schemas/atom",
}
_VERSION = re.compile(r"v\d+$")


def arxiv_doi(arxiv_id: str) -> str:
    """arXiv's own DataCite DOI for a paper (what OpenAlex indexes it by)."""
    return f"10.48550/arxiv.{arxiv_id.lower()}"


async def get_paper(
    client: httpx.AsyncClient, arxiv_id: str, deadline: Deadline
) -> Optional[WorkRecord]:
    """The paper with this id (no version), or None if arXiv has no such id."""
    response = await get_or_none(
        client,
        API_URL,
        params={"id_list": arxiv_id, "max_results": 1},
        deadline=deadline,
        what=f"arXiv {arxiv_id}",
    )
    if response is None:
        return None
    records = parse_feed(response.text)
    return records[0] if records else None


def parse_feed(text: str) -> list[WorkRecord]:
    """An Atom feed from the arXiv API -> one `WorkRecord` per entry.

    An unknown id comes back as an empty feed (or, for malformed ids, one
    entry titled "Error"); both give no records.
    """
    root = ET.fromstring(text)
    records = []
    for entry in root.findall("atom:entry", _NS):
        entry_id = _text(entry, "atom:id") or ""
        if "/api/errors" in entry_id:
            continue
        arxiv_id = _VERSION.sub("", entry_id.split("/abs/", 1)[-1]) or None
        title = clean_text(_text(entry, "atom:title"))
        if not arxiv_id or not title:
            continue
        doi = clean_text(_text(entry, "arxiv:doi"))
        records.append(
            WorkRecord(
                source=MetadataSource.ARXIV,
                title=title,
                authors=[
                    Author(name=name)
                    for a in entry.findall("atom:author", _NS)
                    if (name := clean_text(_text(a, "atom:name")))
                ],
                abstract=clean_text(_text(entry, "atom:summary")),
                publish_date=parse_date(_text(entry, "atom:published")),
                # The published version, when the authors linked one.
                journal=clean_text(_text(entry, "arxiv:journal_ref")),
                doi=doi.lower() if doi else arxiv_doi(arxiv_id),
                arxiv_id=arxiv_id,
                work_type="preprint",
            )
        )
    return records


def _text(node: ET.Element, path: str) -> Optional[str]:
    found = node.find(path, _NS)
    return found.text if found is not None else None
