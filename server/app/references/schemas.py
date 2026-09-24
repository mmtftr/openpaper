"""API / cache shapes for resolved references."""

from __future__ import annotations

from typing import Literal, Optional
from uuid import UUID

from pydantic import BaseModel, Field

# library: the owner already has it. paper: a scholarly record (Crossref /
# OpenAlex / arXiv, or a landing page's `citation_*` tags). web: a page
# described by its OpenGraph / JSON-LD / <title>. unresolved: nothing found.
ReferenceKind = Literal["library", "paper", "web", "unresolved"]
ReferenceSource = Literal[
    "library", "crossref", "openalex", "arxiv", "citation_meta", "og"
]


class ResolvedReference(BaseModel):
    kind: ReferenceKind
    source: Optional[ReferenceSource] = None
    title: Optional[str] = None
    authors: list[str] = Field(default_factory=list)
    year: Optional[int] = None
    venue: Optional[str] = None
    # The paper's abstract, or a web page's description.
    abstract: Optional[str] = None
    doi: Optional[str] = None
    arxiv_id: Optional[str] = None
    # Where the reference points: landing page / DOI link / the web page.
    # For `unresolved`, the (repaired) URL found in the entry, if any.
    url: Optional[str] = None
    # Open-access PDF, when known ("Add to library" imports it).
    pdf_url: Optional[str] = None
    site_name: Optional[str] = None
    image_url: Optional[str] = None
    library_paper_id: Optional[UUID] = None
    # Rendered first page, for library papers.
    preview_url: Optional[str] = None


class ReferenceEntry(BaseModel):
    key: str = Field(max_length=200, description="Caller's id for this entry")
    text: str = Field(min_length=1, max_length=5000)


class ResolveReferencesRequest(BaseModel):
    entries: list[ReferenceEntry] = Field(max_length=500)


class ResolveReferencesResponse(BaseModel):
    # By the caller's `key`.
    results: dict[str, ResolvedReference]
    # Keys not resolved within the request's time budget, or whose lookup hit
    # a temporary failure (nothing was cached for them): ask again later.
    pending: list[str]
