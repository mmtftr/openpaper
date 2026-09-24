"""Resolve one bibliography entry to a paper, a web page, or nothing.

Order (first hit wins):

1. a DOI / arXiv id in the entry -> Crossref + OpenAlex / arXiv + OpenAlex;
2. a URL in the entry (line-break splits repaired) -> fetch the page:
   `citation_doi` / an arXiv id -> step 1; `citation_*` tags -> a paper;
   OpenGraph / Twitter / JSON-LD / <title> -> a web page;
3. title search on Crossref, then OpenAlex, with the title guessed from the entry,
   accepted only when the hit's title appears in the entry (fuzzy, like
   ingest's page-1 check) and one of its authors' surnames does too.

The owner's library is layered on top by `service` (it needs the DB).

`Resolution.transient` is set when any lookup failed in a way a retry could
fix (timeout, 5xx, 429): the answer may be worse than it should be, so the
cache doesn't keep it.
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Awaitable, Optional

import httpx

from app.core.deadline import Deadline
from app.core.errors import classify
from app.core.http import shared_client
from app.ingest.metadata_ids import Identifier, identifiers_in, tokens, unique
from app.ingest.metadata_lookup import Attempt, author_on_page, lookup, title_matches
from app.ingest.sources import crossref, openalex
from app.ingest.sources.work_record import WorkRecord, parse_date
from app.references import html_meta
from app.references.html_meta import PageMeta
from app.references.schemas import ResolvedReference
from app.references.text import title_candidates, url_candidates, year_in

logger = logging.getLogger(__name__)

ENTRY_BUDGET_S = 15.0
PAGE_TIMEOUT_S = 6.0
PAGE_MAX_BYTES = 768 * 1024
SEARCH_ROWS = 5
MAX_IDENTIFIERS = 2
MAX_URLS = 2
# Records of these types share a paper's title without being the paper.
_SKIP_TYPES = frozenset({"peer-review", "component", "dataset", "grant", "erratum"})
# Sites serve bots a 403 or an interstitial; look like a browser.
PAGE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0 Safari/537.36 OpenPaper/0.1"
    ),
    "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.8",
}


@dataclass
class Resolution:
    reference: ResolvedReference
    transient: bool = False  # a retryable failure happened: don't cache


class NotHtml(Exception):
    """The URL answered with something other than a web page (a PDF, ...)."""


async def resolve_reference(
    entry_text: str,
    *,
    client: Optional[httpx.AsyncClient] = None,
    deadline: Optional[Deadline] = None,
) -> Resolution:
    run = _Run(
        entry_text, client or shared_client(), deadline or Deadline(ENTRY_BUDGET_S)
    )
    reference = await run.resolve()
    transient = any(classify(error).retryable for error in run.attempt.errors)
    return Resolution(reference, transient)


@dataclass
class _Run:
    text: str
    client: httpx.AsyncClient
    deadline: Deadline
    attempt: Attempt = field(default_factory=Attempt)
    tried: set[tuple[str, str]] = field(default_factory=set)
    reachable_url: Optional[str] = None

    async def resolve(self) -> ResolvedReference:
        idents = unique(identifiers_in(self.text, "reference"))[:MAX_IDENTIFIERS]
        for ident in idents:
            found = await self.by_identifier(ident)
            if found:
                return found

        urls = url_candidates(self.text)
        doi = next((i.value for i in idents if i.kind == "doi"), None)
        if not urls and doi:
            urls = [[f"https://doi.org/{doi}"]]  # the DOI's landing page
        for variants in urls[:MAX_URLS]:
            found = await self.from_url(variants)
            if found:
                return found

        found = await self.title_search()
        if found:
            return found
        url = self.reachable_url or (urls[0][0] if urls else None)
        return ResolvedReference(kind="unresolved", url=url, year=year_in(self.text))

    # -- identifiers ------------------------------------------------------------

    async def by_identifier(
        self, ident: Identifier, url: Optional[str] = None
    ) -> Optional[ResolvedReference]:
        key = (ident.kind, ident.value.lower())
        if key in self.tried:
            return None
        self.tried.add(key)
        records = await lookup(self.client, self.deadline, ident, self.attempt)
        return paper_from_records(records, url=url) if records else None

    # -- web pages ------------------------------------------------------------------

    async def from_url(self, variants: list[str]) -> Optional[ResolvedReference]:
        for url in variants:
            try:
                page = await fetch_page(self.client, url, self.deadline)
            except NotHtml:
                self.reachable_url = self.reachable_url or url
                return None  # a PDF etc.: the title search may still find it
            except Exception as exc:
                self._record(exc, url)
                continue
            # The URL as cited (a bot-challenge redirect is not the page).
            self.reachable_url = self.reachable_url or url
            found = await self.from_page(page)
            if found:
                return found
        return None

    async def from_page(self, page: PageMeta) -> Optional[ResolvedReference]:
        # A scholarly landing page names its DOI / arXiv id: use the APIs.
        ids_text = " ".join(
            filter(
                None,
                [
                    page.first("citation_doi", "dc.identifier", "prism.doi"),
                    _arxiv_marker(page.first("citation_arxiv_id")),
                    page.url,
                ],
            )
        )
        doi_value = page.first("citation_doi", "prism.doi")
        idents = identifiers_in(ids_text, "page")
        if doi_value and doi_value.lower().startswith("10."):
            idents.insert(0, Identifier("doi", doi_value.lower(), "page"))
        for ident in unique(idents)[:MAX_IDENTIFIERS]:
            landing = page.absolute(page.canonical) or page.url
            found = await self.by_identifier(ident, url=landing)
            if found:
                if not found.pdf_url:
                    found.pdf_url = page.absolute(page.first("citation_pdf_url"))
                return found
        return paper_from_citation_meta(page) or web_from_page(page, self.text)

    # -- title search ---------------------------------------------------------------

    async def title_search(self) -> Optional[ResolvedReference]:
        """Crossref first (free), then OpenAlex (metered per request) only when
        Crossref has no acceptable hit. OpenAlex is asked by title filter, not
        full-text `search`, which buries short ML titles under famous papers."""
        for title in title_candidates(self.text):
            for search in (crossref.search, openalex.title_search):
                (hits,) = await self._gather(
                    search(self.client, title, self.deadline, rows=SEARCH_ROWS)
                )
                for hit in hits or []:
                    if hit.work_type in _SKIP_TYPES or not self.accepts(hit, title):
                        continue
                    return await self.enrich(hit)
        return None

    async def enrich(self, hit: WorkRecord) -> ResolvedReference:
        """Re-resolve a search hit by its id: gathers every source (the arXiv
        abstract, OpenAlex's open-access PDF, ...)."""
        ident = _identifier_of(hit)
        if ident is not None and (ident.kind, ident.value.lower()) not in self.tried:
            self.tried.add((ident.kind, ident.value.lower()))
            records = await lookup(self.client, self.deadline, ident, self.attempt)
            records = [r for r in records if title_matches(r.title, self.text)]
            if records:
                return paper_from_records(records)
        return paper_from_records([hit])

    def accepts(self, hit: WorkRecord, query: str) -> bool:
        """The hit's title is in the entry, covers most of the guessed title,
        and one of its authors' surnames is in the entry too."""
        title = tokens(hit.title)
        if len(title) < 3 or len(title) < 0.6 * len(tokens(query)):
            return False
        return title_matches(hit.title, self.text) and author_on_page(hit, self.text)

    # -- plumbing -----------------------------------------------------------------------

    async def _gather(self, *calls: Awaitable[Any]) -> list[Any]:
        out: list[Any] = []
        for result in await asyncio.gather(*calls, return_exceptions=True):
            if isinstance(result, BaseException):
                if not isinstance(result, Exception):
                    raise result
                self._record(result, "search")
                result = None
            out.append(result)
        return out

    def _record(self, exc: Exception, what: str) -> None:
        classified = classify(exc)
        logger.info("Reference lookup %s failed: %s", what, classified.message)
        if classified.retryable:
            self.attempt.errors.append(exc)


async def fetch_page(
    client: httpx.AsyncClient, url: str, deadline: Deadline
) -> PageMeta:
    """GET `url` (redirects followed, body capped) and parse its head.

    Raises for HTTP errors (`classify` sorts them) and `NotHtml` for PDFs etc.
    """
    async with client.stream(
        "GET",
        url,
        headers=PAGE_HEADERS,
        timeout=deadline.timeout(PAGE_TIMEOUT_S),
        follow_redirects=True,
    ) as response:
        response.raise_for_status()
        content_type = response.headers.get("content-type", "").lower()
        if content_type and "html" not in content_type:
            raise NotHtml(content_type)
        body = bytearray()
        async for chunk in response.aiter_bytes():
            body += chunk
            if len(body) >= PAGE_MAX_BYTES:
                break
        html = bytes(body).decode(response.charset_encoding or "utf-8", "replace")
        return html_meta.parse_head(html, str(response.url))


# -- building results ---------------------------------------------------------------


def paper_from_records(
    records: list[WorkRecord], *, url: Optional[str] = None
) -> ResolvedReference:
    """Merge records (priority order: first non-empty value per field)."""

    def first(name: str) -> Any:
        return next((v for r in records if (v := getattr(r, name, None))), None)

    authors = next((r.authors for r in records if r.authors), [])
    arxiv_id = first("arxiv_id")
    doi = first("doi")
    if doi and arxiv_id and doi.startswith("10.48550/"):
        # arXiv's own DataCite DOI: prefer a published version's, if any.
        doi = next(
            (r.doi for r in records if r.doi and not r.doi.startswith("10.48550/")),
            None,
        )
    published = first("publish_date")
    venue = first("journal") or ("arXiv" if arxiv_id else None)
    if venue and venue.startswith("arXiv"):
        venue = "arXiv"  # OpenAlex: "arXiv (Cornell University)"
    pdf_url = first("pdf_url") or (
        f"https://arxiv.org/pdf/{arxiv_id}" if arxiv_id else None
    )
    landing = url or (
        f"https://doi.org/{doi}"
        if doi
        else f"https://arxiv.org/abs/{arxiv_id}"
        if arxiv_id
        else None
    )
    return ResolvedReference(
        kind="paper",
        source=records[0].source.value,  # type: ignore[arg-type]
        title=first("title"),
        authors=[a.name for a in authors],
        year=published.year if published else None,
        venue=venue,
        abstract=first("abstract"),
        doi=doi,
        arxiv_id=arxiv_id,
        url=landing,
        pdf_url=pdf_url,
    )


def paper_from_citation_meta(page: PageMeta) -> Optional[ResolvedReference]:
    """A landing page's Highwire `citation_*` tags (OpenReview, proceedings)."""
    title = page.first("citation_title")
    if html_meta.is_junk_title(title):
        return None
    authors = [html_meta.person_name(a) for a in page.all("citation_author")]
    date = page.first(
        "citation_publication_date", "citation_date", "citation_online_date",
        "citation_year", "dc.date",
    )  # fmt: skip
    published = parse_date(date)
    return ResolvedReference(
        kind="paper",
        source="citation_meta",
        title=title,
        authors=authors,
        year=published.year if published else None,
        venue=page.first(
            "citation_journal_title",
            "citation_conference_title",
            "citation_book_title",
            "citation_publisher",
            "og:site_name",
        ),  # fmt: skip
        abstract=page.first(
            "citation_abstract", "dc.description", "og:description", "description"
        ),
        url=page.absolute(page.canonical) or page.url,
        pdf_url=page.absolute(page.first("citation_pdf_url")),
        site_name=page.first("og:site_name") or html_meta.host_name(page.url),
    )


def web_from_page(page: PageMeta, entry_text: str) -> Optional[ResolvedReference]:
    """A web page (blog post, docs, repo) from its OpenGraph / JSON-LD / <title>."""
    ld = html_meta.article_ld(page)
    site = page.first("og:site_name", "application-name")
    if not site and ld:
        site = next(iter(html_meta.ld_names(ld.get("publisher"))), None)
    title = page.first("og:title", "twitter:title") or html_meta.ld_text(
        ld, "headline", "name"
    )
    if not title and page.title:
        title, site_from_title = html_meta.split_title(page.title)
        site = site or site_from_title
    if html_meta.is_junk_title(title):
        return None

    authors = html_meta.ld_names((ld or {}).get("author"))
    if not authors:
        authors = [
            a
            for name in ("author", "article:author", "parsely-author", "dc.creator")
            for a in page.all(name)
            if not a.startswith("http")
        ][:10]
    date = page.first(
        "article:published_time", "citation_publication_date", "parsely-pub-date",
        "date", "dc.date",
    ) or html_meta.ld_text(ld, "datePublished", "dateCreated")  # fmt: skip
    published = parse_date(date)
    image = page.first(
        "og:image", "og:image:url", "og:image:secure_url", "twitter:image",
        "twitter:image:src",
    ) or html_meta.ld_text(ld, "image", "thumbnailUrl")  # fmt: skip
    return ResolvedReference(
        kind="web",
        source="og",
        title=title,
        authors=authors,
        year=published.year if published else year_in(entry_text),
        abstract=page.first("og:description", "twitter:description", "description")
        or html_meta.ld_text(ld, "description"),
        url=page.absolute(page.canonical) or page.url,
        site_name=site or html_meta.host_name(page.url),
        image_url=page.absolute(image),
    )


def _arxiv_marker(value: Optional[str]) -> Optional[str]:
    return f"arXiv:{value}" if value and re.match(r"\d{4}\.\d{4,5}", value) else None


def _identifier_of(record: WorkRecord) -> Optional[Identifier]:
    if record.arxiv_id:
        return Identifier("arxiv", record.arxiv_id, "title search")
    if record.doi:
        return Identifier("doi", record.doi, "title search")
    return None
