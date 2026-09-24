"""Schemas for the Discover feature."""

from typing import Dict, List, Literal, Optional
from uuid import UUID

from pydantic import BaseModel
from typing_extensions import NotRequired, TypedDict

from app.schemas.json_datetime import IsoDatetime

# Available source filters for discover search
# "openalex" routes to OpenAlex backend, others filter Exa by domain
DISCOVER_SOURCES = {
    "openalex": {
        "label": "Academic Databases",
        "description": "OpenAlex scholarly index",
        "domains": None,  # Uses OpenAlex backend instead of Exa
    },
    "arxiv": {
        "label": "arXiv",
        "description": "Preprints in physics, math, CS, and more",
        "domains": ["arxiv.org"],
    },
    "pubmed": {
        "label": "PubMed",
        "description": "Biomedical and life sciences",
        "domains": ["pubmed.ncbi.nlm.nih.gov", "ncbi.nlm.nih.gov"],
    },
    "nature": {
        "label": "Nature",
        "description": "Nature family of journals",
        "domains": ["nature.com"],
    },
    "science": {
        "label": "Science",
        "description": "Science family of journals",
        "domains": ["science.org"],
    },
    "plos": {
        "label": "PLOS",
        "description": "Open access journals",
        "domains": ["plos.org"],
    },
    "biorxiv": {
        "label": "bioRxiv / medRxiv",
        "description": "Biology and medicine preprints",
        "domains": ["biorxiv.org", "medrxiv.org"],
    },
    "ssrn": {
        "label": "SSRN",
        "description": "Social sciences research",
        "domains": ["ssrn.com"],
    },
    "ieee": {
        "label": "IEEE",
        "description": "Engineering and technology",
        "domains": ["ieee.org", "ieeexplore.ieee.org"],
    },
    "acm": {
        "label": "ACM",
        "description": "Computing and information technology",
        "domains": ["acm.org", "dl.acm.org"],
    },
}


class DiscoverSearchRequest(BaseModel):
    question: str
    sources: Optional[list[str]] = None  # List of source keys from DISCOVER_SOURCES
    # OpenAlex sort; None keeps relevance order.
    sort: Optional[Literal["cited_by_count:desc", "publication_date:desc"]] = None
    only_open_access: bool = False  # Filter for open access papers (OpenAlex only)
    # None means all time.
    year_filter: Optional[Literal["last_year", "last_5_years"]] = None


class DiscoverResult(TypedDict):
    """One search hit, as stored in `discover_searches.results`.

    Exa hits carry `summary`; OpenAlex hits carry `cited_by_count`, `source`
    and `institutions`. A TypedDict (not a model) so each hit serializes with
    only the keys it was stored with.
    """

    title: Optional[str]
    url: Optional[str]
    authors: NotRequired[Optional[List[str]]]
    published_date: NotRequired[Optional[str]]
    text: NotRequired[Optional[str]]
    highlights: NotRequired[Optional[List[str]]]
    highlight_scores: NotRequired[Optional[List[float]]]
    favicon: NotRequired[Optional[str]]
    summary: NotRequired[Optional[str]]
    cited_by_count: NotRequired[Optional[int]]
    source: NotRequired[Optional[str]]
    institutions: NotRequired[Optional[List[str]]]


class DiscoverSearchRecord(BaseModel):
    id: UUID
    question: str
    subqueries: Optional[List[str]] = None
    # subquery -> its hits
    results: Optional[Dict[str, List[DiscoverResult]]] = None
    created_at: Optional[IsoDatetime] = None


class DiscoverSource(BaseModel):
    key: str
    label: str
    description: str
