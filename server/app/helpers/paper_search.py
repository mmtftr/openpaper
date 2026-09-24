import logging
import time
from enum import Enum
from typing import List, Optional
from urllib.parse import quote

import requests
from pydantic import BaseModel, ConfigDict, model_validator

from app.settings import get_settings

logger = logging.getLogger(__name__)

OPENALEX_MAX_RETRIES = 3
OPENALEX_RETRY_DELAY = 1  # seconds
OPENALEX_API_KEY = get_settings().OPENALEX_API_KEY


def _with_openalex_auth(url: str) -> str:
    """Append the OpenAlex api_key query parameter to a URL if configured."""
    if not OPENALEX_API_KEY:
        return url
    separator = "&" if "?" in url else "?"
    return f"{url}{separator}api_key={quote(OPENALEX_API_KEY)}"


def _request_with_retry(
    url: str,
    method: str = "GET",
    max_retries: int = OPENALEX_MAX_RETRIES,
    retry_delay: float = OPENALEX_RETRY_DELAY,
    timeout: int = 10,
) -> requests.Response:
    """
    Make an HTTP request with automatic retry on failure.

    Args:
        url: The URL to request.
        method: HTTP method (GET, POST, etc.).
        max_retries: Maximum number of retry attempts.
        retry_delay: Delay between retries in seconds.
        timeout: Request timeout in seconds.

    Returns:
        requests.Response: The response object.

    Raises:
        requests.RequestException: If all retries fail.
    """
    last_exception = None

    for attempt in range(max_retries):
        try:
            response = requests.request(method, url, timeout=timeout)
            response.raise_for_status()
            return response
        except requests.RequestException as e:
            last_exception = e
            if attempt < max_retries - 1:
                logger.warning(
                    f"OpenAlex API request failed (attempt {attempt + 1}/{max_retries}): {e}. "
                    f"Retrying in {retry_delay}s..."
                )
                time.sleep(retry_delay)
            else:
                logger.error(
                    f"OpenAlex API request failed after {max_retries} attempts: {e}"
                )

    raise last_exception  # type: ignore


SEMANTIC_SCHOLAR_API_KEY = get_settings().SEMANTIC_SCHOLAR_API_KEY

DISABLE_SEMANTIC_SCHOLAR = (
    True  # Temporary flag to disable Semantic Scholar API calls due to 403 errors
)


class OAStatus(str, Enum):
    """
    Enum for OpenAlex OA status.
    """

    DIAMOND = "diamond"
    GOLDEN = "gold"
    GREEN = "green"
    HYBRID = "hybrid"
    BRONZE = "bronze"
    CLOSED = "closed"


class PaperSort(str, Enum):
    top_cited = "cited_by_count:desc"
    newest = "publication_date:desc"


class BaseOpenAlexModel(BaseModel):
    model_config = ConfigDict(extra="ignore")


class OpenAccess(BaseOpenAlexModel):
    is_oa: bool
    oa_status: Optional[OAStatus] = None
    oa_url: Optional[str] = None


class Keyword(BaseOpenAlexModel):
    id: str
    display_name: str
    score: Optional[float] = None


class PrimaryLocationSource(BaseOpenAlexModel):
    id: Optional[str] = None
    display_name: Optional[str] = None
    type: Optional[str] = None
    issn_l: Optional[str] = None
    issn: Optional[List[str]] = None
    host_organization: Optional[str] = None


class PrimaryLocation(BaseOpenAlexModel):
    is_oa: Optional[bool] = None
    landing_page_url: Optional[str] = None
    pdf_url: Optional[str] = None
    source: Optional[PrimaryLocationSource] = None


class Biblio(BaseOpenAlexModel):
    volume: Optional[str] = None
    issue: Optional[str] = None
    first_page: Optional[str] = None
    last_page: Optional[str] = None


class SubTopic(BaseOpenAlexModel):
    id: str
    display_name: str


class Topic(BaseOpenAlexModel):
    id: str
    display_name: Optional[str] = None
    score: Optional[float] = None
    subfield: Optional[SubTopic] = None
    field: Optional[SubTopic] = None
    domain: Optional[SubTopic] = None


class Author(BaseOpenAlexModel):
    id: Optional[str] = None
    display_name: Optional[str] = None
    orcid: Optional[str] = None


class Institution(BaseOpenAlexModel):
    id: Optional[str] = None
    display_name: Optional[str] = None
    ror: Optional[str] = None
    country_code: Optional[str] = None
    type: Optional[str] = None


class Authorship(BaseOpenAlexModel):
    author_position: Optional[str] = None
    author: Optional[Author] = None
    institutions: Optional[List[Institution]] = None


class OpenAlexWork(BaseOpenAlexModel):
    id: str
    title: str
    doi: Optional[str] = None
    display_name: Optional[str] = None
    publication_year: Optional[int] = None
    publication_date: Optional[str] = None
    type: Optional[str] = None
    open_access: Optional[OpenAccess] = None
    keywords: Optional[List[Keyword]] = None
    primary_location: Optional[PrimaryLocation] = None
    biblio: Optional[Biblio] = None
    topics: Optional[List[Topic]] = None
    authorships: Optional[List[Authorship]] = None
    cited_by_count: Optional[int] = None
    abstract_inverted_index: Optional[dict] = None
    abstract: Optional[str] = None

    @model_validator(mode="before")
    @classmethod
    def validate_work(cls, data):
        if "abstract_inverted_index" in data and data["abstract_inverted_index"]:
            data["abstract"] = build_abstract_from_inverted_index(
                data["abstract_inverted_index"]
            )
        return data


class OpenAlexResponse(BaseModel):
    meta: dict
    results: List[OpenAlexWork]

    @model_validator(mode="before")
    @classmethod
    def validate_results(cls, data):
        if "results" in data:
            valid_results = []
            for item in data["results"]:
                try:
                    valid_results.append(OpenAlexWork(**item))
                except Exception as e:
                    logger.debug(f"Skipping invalid OpenAlex work entry: {e}")

            data["results"] = valid_results
        return data


class OpenAlexFilter(BaseModel):
    authors: Optional[List[str]] = None
    institutions: Optional[List[str]] = None
    only_oa: bool = False
    from_publication_date: Optional[str] = None  # ISO date format: YYYY-MM-DD
    min_cited_by_count: Optional[int] = None


def construct_open_alex_filter_url(filter: OpenAlexFilter) -> str:
    """
    Construct a filter URL for OpenAlex API based on provided filters.

    Args:
        filter (OpenAlexFilter): The filter object containing authors and institutions.

    Returns:
        str: The constructed filter URL.
    """
    filters = []
    if filter.authors:
        filters.append(f"authorships.author.id:{'|'.join(filter.authors)}")
    if filter.institutions:
        filters.append(f"institutions.id:{'|'.join(filter.institutions)}")
    if filter.only_oa:
        filters.append("open_access.is_oa:true")
    if filter.from_publication_date:
        filters.append(f"from_publication_date:{filter.from_publication_date}")
    if filter.min_cited_by_count is not None:
        filters.append(f"cited_by_count:>{filter.min_cited_by_count - 1}")

    return ",".join(filters) if filters else ""


# Utility functions for searching the OpenAlex API
# For documentation, see https://docs.openalex.org/api-entities/works/search-works
def search_open_alex(
    search_term: Optional[str],
    filter: Optional[OpenAlexFilter] = None,
    page: int = 1,
    sort: Optional[str] = None,  # e.g. "cited_by_count:desc" or "publication_year:desc"
) -> OpenAlexResponse:
    """
    Search the OpenAlex API for papers based on a search term and optional filter.

    Args:
        search_term (str): The term to search for.
        filter (Optional[OpenAlexFilter]): Optional filter for the search.

    Returns:
        dict: The response from the OpenAlex API.
    """
    # Construct the search URL
    base_url = "https://api.openalex.org/works"

    params = {"search": quote(search_term) if search_term else "", "page": page}
    if filter:
        params["filter"] = quote(construct_open_alex_filter_url(filter))
    if sort:
        params["sort"] = quote(sort)

    constructed_url = f"{base_url}?"
    for key, value in params.items():
        constructed_url += f"{key}={value}&"

    constructed_url = base_url + "?" + "&".join(f"{k}={v}" for k, v in params.items())

    logger.debug(f"Constructed URL: {constructed_url}")

    response = _request_with_retry(_with_openalex_auth(constructed_url))

    logger.info(f"Response Status: {response.status_code}")
    logger.debug(f"Response JSON: {response.json()}")

    return OpenAlexResponse(**response.json())


def build_abstract_from_inverted_index(inverted_index: dict) -> str:
    """
    Build an abstract from the inverted index of a paper.

    Args:
        inverted_index (dict): The inverted index of the paper. Keys are terms, and values are the list of word indexes at which they appear.

    Returns:
        str: The constructed abstract.
    """
    min_index = min(min(value) for value in inverted_index.values() if value)
    max_index = max(max(value) for value in inverted_index.values() if value)
    abstract = [""] * (max_index - min_index + 1)
    for key, value in inverted_index.items():
        for index in value:
            if min_index <= index <= max_index:
                abstract[index - min_index] = key
    return " ".join(abstract).strip() if abstract else ""
