"""`WorkRecord`: one bibliographic record, normalized from any source.

Crossref, OpenAlex and arXiv each parse their own format into this shape so
the metadata stages can verify and merge records without caring where they
came from.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass, field
from datetime import date
from typing import Optional

from app.ingest.models import MetadataSource


@dataclass(frozen=True)
class Author:
    name: str  # "Given Family", as displayed
    family: Optional[str] = None  # surname when the source says which it is


@dataclass
class WorkRecord:
    source: MetadataSource
    title: Optional[str] = None
    authors: list[Author] = field(default_factory=list)
    abstract: Optional[str] = None
    publish_date: Optional[date] = None
    journal: Optional[str] = None
    publisher: Optional[str] = None
    doi: Optional[str] = None  # bare, lowercase: "10.1073/pnas.2516511123"
    arxiv_id: Optional[str] = None  # no version: "2404.15255"
    openalex_id: Optional[str] = None  # "W4395443869"
    keywords: list[str] = field(default_factory=list)
    institutions: list[str] = field(default_factory=list)
    # The source's own record type ("journal-article", "preprint", ...).
    work_type: Optional[str] = None
    # An open-access PDF, when the source knows one (OpenAlex only).
    pdf_url: Optional[str] = None


_TAG = re.compile(r"<[^>]+>")
_SPACE = re.compile(r"\s+")


def clean_text(value: Optional[str]) -> Optional[str]:
    """Strip markup (Crossref JATS / MathML), unescape entities, collapse
    whitespace. Empty -> None."""
    if not value:
        return None
    text = _SPACE.sub(" ", html.unescape(_TAG.sub(" ", value))).strip()
    return text or None


def clean_abstract(value: Optional[str]) -> Optional[str]:
    """`clean_text` minus a leading "Abstract" heading (JATS <jats:title>)."""
    text = clean_text(value)
    if text and text.lower().startswith("abstract "):
        text = text[len("abstract ") :].lstrip(" .:-")
    return text or None


def parse_date(value: Optional[str]) -> Optional[date]:
    """'2024-04-23', '2024-04', '2024' or an ISO timestamp -> date."""
    if not value:
        return None
    match = re.match(r"\s*(\d{4})(?:-(\d{1,2}))?(?:-(\d{1,2}))?", value)
    if not match:
        return None
    return date_from_parts([int(part) for part in match.groups() if part is not None])


def date_from_parts(parts: list[int]) -> Optional[date]:
    """[year, month?, day?] (Crossref `date-parts`) -> date; missing = 1."""
    if not parts or not parts[0]:
        return None
    year, month, day = (list(parts) + [1, 1])[:3]
    try:
        return date(year, month or 1, day or 1)
    except ValueError:
        try:
            return date(year, 1, 1)
        except ValueError:
            return None


def dedupe(values: list[str]) -> list[str]:
    """Drop empties and case-insensitive repeats, keeping first-seen order."""
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        text = clean_text(value)
        if text and text.lower() not in seen:
            seen.add(text.lower())
            out.append(text)
    return out
