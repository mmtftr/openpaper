"""Reference resolution: entry text helpers, the resolver (MockTransport, no
network) and the batch service's cache / library behaviour (in-memory store).
"""

from __future__ import annotations

import asyncio
import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Collection

import httpx
import pytest

from app.core.deadline import Deadline
from app.references import service
from app.references.resolver import Resolution, resolve_reference
from app.references.schemas import ReferenceEntry, ResolvedReference
from app.references.service import LibraryPaper, resolve_entries
from app.references.text import (
    cache_key,
    title_candidates,
    url_candidates,
    without_urls,
    year_in,
)
from tests.ingest_metadata_fixtures import (
    ARXIV_ROUTES,
    PNAS_ROUTES,
    client_for,
    fixture_json,
)

TRANSLUCE = (
    "Choi, D., Huang, V., Schwettmann, S., and Steinhardt, J. Scalably extracting "
    "latent representations of users, 2025. URL https://transluce.org/ user-modeling."
)
PNAS_ENTRY = (
    "Luo, J., Wang, H., Moyer, D., and Nielsen, J. Reconstruction of human "
    "metabolic models with large language models. PNAS, 123(15), 2026. "
    "doi:10.1073/pnas.2516511123."
)
PNAS_NO_ID = (
    "Luo, J., Wang, H., Moyer, D., and Nielsen, J. Reconstruction of human "
    "metabolic models with large language models. PNAS, 2026."
)
ARXIV_ENTRY = (
    "Stefan Heimersheim and Neel Nanda. How to use and interpret activation "
    "patching, 2024. URL https://arxiv.org/abs/2404.15255."
)

TRANSLUCE_OG = """<!doctype html><html><head>
<title>Scalably Extracting Latent Representations of Users | Transluce AI</title>
<meta property="og:title" content="Scalably Extracting Latent Representations of Users">
<meta property="og:description" content="Constructing datasets and training decoders
 to extract user models from language models">
<meta property="og:site_name" content="Transluce">
<meta property="og:image" content="/images/user-modeling.png">
<meta name="author" content="Dami Choi">
<meta property="article:published_time" content="2025-11-25T00:00:00Z">
<link rel="canonical" href="https://transluce.org/user-modeling">
</head><body><p>...</p></body></html>"""

# What transluce.org actually serves: <title>, a description, JSON-LD.
TRANSLUCE_LD = """<!DOCTYPE html><html lang="en"><head>
<title data-next-head="">Scalably Extracting Latent Representations of Users | Transluce AI</title>
<meta name="description" content="Constructing datasets and training decoders to extract user models from language models"/>
<script type="application/ld+json">{"@context":"https://schema.org","@type":"BlogPosting",
"headline":"Scalably Extracting Latent Representations of Users",
"publisher":{"@type":"Organization","name":"Transluce"},"datePublished":"2025-11-25",
"author":[{"@type":"Person","name":"Dami Choi"},{"@type":"Person","name":"Vincent Huang"}]}</script>
</head><body></body></html>"""

CITATION_PAGE = """<html><head>
<meta name="citation_title" content="Sparse Feature Circuits: Discovering and Editing Interpretable Causal Graphs">
<meta name="citation_author" content="Marks, Samuel">
<meta name="citation_author" content="Rager, Can">
<meta name="citation_publication_date" content="2025/01/22">
<meta name="citation_conference_title" content="The Thirteenth International Conference on Learning Representations">
<meta name="citation_pdf_url" content="/pdf?id=I4e82CIDxv">
<meta name="citation_abstract" content="We introduce methods for discovering sparse feature circuits.">
<title>Sparse Feature Circuits | OpenReview</title>
</head></html>"""
CITATION_ENTRY = (
    "Samuel Marks, Can Rager, et al. Sparse feature circuits: Discovering and "
    "editing interpretable causal graphs. In ICLR, 2025. URL "
    "https://openreview.net/forum?id=I4e82CIDxv."
)


def html(body: str, status: int = 200) -> httpx.Response:
    return httpx.Response(
        status, text=body, headers={"content-type": "text/html; charset=utf-8"}
    )


def mock_client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def resolve(text: str, client: httpx.AsyncClient) -> Resolution:
    return asyncio.run(resolve_reference(text, client=client, deadline=Deadline(10)))


# -- text ------------------------------------------------------------------------


def test_split_url_is_repaired_and_original_kept_as_fallback():
    assert url_candidates(TRANSLUCE) == [
        ["https://transluce.org/user-modeling", "https://transluce.org/"]
    ]


def test_url_not_joined_across_sentence_end_or_year():
    assert url_candidates("See https://example.com/foo/. Accessed 2020.") == [
        ["https://example.com/foo/"]
    ]
    assert url_candidates("Blog. https://example.com/ 2024.") == [
        ["https://example.com/"]
    ]
    assert url_candidates("x https://a.org/path-to/thing, 2025. Accessed: 2025") == [
        ["https://a.org/path-to/thing"]
    ]


def test_title_and_year_ignore_the_url():
    assert title_candidates(TRANSLUCE)[0] == (
        "Scalably extracting latent representations of users"
    )
    assert "transluce" not in without_urls(TRANSLUCE)
    assert year_in(TRANSLUCE) == 2025
    assert year_in("A. B. Foo. https://x.org/2019/post, 2023.") == 2023


def test_cache_key_ignores_numbering_case_punctuation_and_spacing():
    a = "[12] Vaswani, A. Attention is all you need.  NeurIPS, 2017."
    b = "vaswani a attention is all you need neurips 2017"
    assert cache_key(a) == cache_key(b)
    assert cache_key(a) != cache_key("Vaswani, A. Attention. NeurIPS, 2018.")


# -- resolver -----------------------------------------------------------------------


def test_doi_entry_resolves_through_crossref_and_openalex():
    result = resolve(PNAS_ENTRY, client_for(PNAS_ROUTES))
    ref = result.reference
    assert ref.kind == "paper" and ref.source == "crossref"
    assert (
        ref.title
        == "Reconstruction of human metabolic models with large language models"
    )
    assert ref.doi == "10.1073/pnas.2516511123"
    assert ref.year == 2026 and ref.abstract and ref.authors
    assert ref.url == "https://doi.org/10.1073/pnas.2516511123"
    assert not result.transient


def test_arxiv_entry_resolves_through_arxiv():
    calls: list[str] = []
    ref = resolve(ARXIV_ENTRY, client_for(ARXIV_ROUTES, calls)).reference
    assert ref.kind == "paper" and ref.source == "arxiv"
    assert ref.arxiv_id == "2404.15255" and ref.doi is None
    assert ref.title == "How to use and interpret activation patching"
    assert ref.pdf_url == "https://arxiv.org/pdf/2404.15255"
    assert ref.abstract and ref.abstract.startswith("Activation patching")
    assert not any("arxiv.org/abs" in url for url in calls)  # no page fetch


def test_transluce_split_url_resolves_to_web_page_from_og_tags():
    fetched: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        fetched.append(str(request.url))
        if str(request.url) == "https://transluce.org/user-modeling":
            return html(TRANSLUCE_OG)
        return httpx.Response(404)

    result = resolve(TRANSLUCE, mock_client(handler))
    ref = result.reference
    assert fetched[0] == "https://transluce.org/user-modeling"
    assert ref.kind == "web" and ref.source == "og"
    assert ref.title == "Scalably Extracting Latent Representations of Users"
    assert ref.site_name == "Transluce"
    assert ref.abstract and ref.abstract.startswith("Constructing datasets")
    assert ref.image_url == "https://transluce.org/images/user-modeling.png"
    assert ref.authors == ["Dami Choi"] and ref.year == 2025
    assert ref.url == "https://transluce.org/user-modeling"
    assert not result.transient


def test_json_ld_page_without_og_tags():
    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == "https://transluce.org/user-modeling":
            return html(TRANSLUCE_LD)
        return httpx.Response(404)

    ref = resolve(TRANSLUCE, mock_client(handler)).reference
    assert ref.kind == "web"
    assert ref.title == "Scalably Extracting Latent Representations of Users"
    assert ref.site_name == "Transluce"
    assert ref.authors == ["Dami Choi", "Vincent Huang"] and ref.year == 2025


def test_split_url_falls_back_to_the_unjoined_url():
    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == "https://transluce.org/":
            return html(TRANSLUCE_OG)
        return httpx.Response(404)

    assert resolve(TRANSLUCE, mock_client(handler)).reference.kind == "web"


def test_citation_meta_page_becomes_a_paper():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "openreview.net":
            return html(CITATION_PAGE)
        return httpx.Response(404)

    ref = resolve(CITATION_ENTRY, mock_client(handler)).reference
    assert ref.kind == "paper" and ref.source == "citation_meta"
    assert ref.title and ref.title.startswith("Sparse Feature Circuits")
    assert ref.authors == ["Samuel Marks", "Can Rager"]
    assert ref.year == 2025
    assert ref.venue and "Learning Representations" in ref.venue
    assert ref.pdf_url == "https://openreview.net/pdf?id=I4e82CIDxv"
    assert (
        ref.abstract == "We introduce methods for discovering sparse feature circuits."
    )


def test_citation_doi_on_the_page_goes_to_crossref():
    page = CITATION_PAGE.replace(
        "<title>",
        '<meta name="citation_doi" content="10.1073/pnas.2516511123"><title>',
    )

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if request.url.host == "openreview.net":
            return html(page)
        for key, value in PNAS_ROUTES.items():
            if key in url:
                return httpx.Response(200, json=value)
        return httpx.Response(404)

    ref = resolve(CITATION_ENTRY, mock_client(handler)).reference
    assert ref.source == "crossref" and ref.doi == "10.1073/pnas.2516511123"
    assert ref.url == "https://openreview.net/forum?id=I4e82CIDxv"
    assert ref.pdf_url == "https://openreview.net/pdf?id=I4e82CIDxv"


def test_title_search_accepts_matching_title_and_author():
    calls: list[str] = []
    routes = {
        # Crossref's hits are all other papers; OpenAlex has it.
        "api.crossref.org/works?": fixture_json("crossref_search_pnas.json"),
        "api.openalex.org/works?": fixture_json("openalex_search_pnas.json"),
        **PNAS_ROUTES,
    }
    ref = resolve(PNAS_NO_ID, client_for(routes, calls)).reference
    assert ref.kind == "paper"
    assert ref.doi == "10.1073/pnas.2516511123"
    # Re-resolved by DOI: the Crossref record leads (it's first in the merge).
    assert ref.source == "crossref" and ref.abstract
    assert any("api.crossref.org/works/10.1073" in url for url in calls)


def test_title_search_stops_at_crossref_when_it_matches():
    calls: list[str] = []
    crossref_hit = {
        "message": {"items": [fixture_json("crossref_pnas.json")["message"]]}
    }
    routes = {"api.crossref.org/works?": crossref_hit, **PNAS_ROUTES}
    ref = resolve(PNAS_NO_ID, client_for(routes, calls)).reference
    assert ref.kind == "paper" and ref.doi == "10.1073/pnas.2516511123"
    # The metered OpenAlex *search* never ran (the DOI lookup is cheap).
    assert not any("api.openalex.org/works?" in url for url in calls)


def test_title_search_rejects_wrong_authors():
    entry = PNAS_NO_ID.replace(
        "Luo, J., Wang, H., Moyer, D., and Nielsen, J.", "Smith, A."
    )
    calls: list[str] = []
    routes = {
        "api.crossref.org/works?": fixture_json("crossref_search_pnas.json"),
        "api.openalex.org/works?": fixture_json("openalex_search_pnas.json"),
    }
    result = resolve(entry, client_for(routes, calls))
    assert result.reference.kind == "unresolved"
    assert any("api.openalex.org/works?" in url for url in calls)  # tried both


def test_title_search_rejects_a_different_title():
    entry = (
        "Luo, J. and Nielsen, J. Metabolic modelling of yeast with graph neural "
        "networks. Nature, 2025."
    )
    routes = {
        "api.crossref.org/works?": fixture_json("crossref_search_pnas.json"),
        "api.openalex.org/works?": fixture_json("openalex_search_pnas.json"),
    }
    assert resolve(entry, client_for(routes)).reference.kind == "unresolved"


def test_transient_failures_are_flagged():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    result = resolve(PNAS_NO_ID, mock_client(handler))
    assert result.reference.kind == "unresolved" and result.transient


def test_missing_page_is_not_transient_and_keeps_the_url():
    result = resolve(TRANSLUCE, mock_client(lambda request: httpx.Response(404)))
    assert result.reference.kind == "unresolved" and not result.transient
    assert result.reference.url == "https://transluce.org/user-modeling"


def test_bot_challenge_page_is_not_a_result():
    challenge = "<html><head><title>Just a moment...</title></head></html>"
    result = resolve(TRANSLUCE, mock_client(lambda request: html(challenge)))
    assert result.reference.kind == "unresolved"


# -- service: cache + library ---------------------------------------------------------


class MemoryStore:
    """`DbReferenceStore` semantics (expiry via `service.expires_at`) in memory."""

    def __init__(self) -> None:
        self.rows: dict[str, tuple[ResolvedReference, datetime | None]] = {}

    def get_many(self, keys: Collection[str], now: datetime):
        return {
            k: ref
            for k in keys
            if k in self.rows
            for ref, expires in [self.rows[k]]
            if expires is None or expires > now
        }

    def put_many(self, rows, now: datetime) -> None:
        for key, _text, ref in rows:
            self.rows[key] = (ref, service.expires_at(ref, now))


class FakeResolver:
    def __init__(self, reference: ResolvedReference, transient: bool = False):
        self.reference, self.transient, self.calls = reference, transient, []

    async def __call__(self, text: str, deadline: Deadline) -> Resolution:
        self.calls.append(text)
        return Resolution(self.reference, self.transient)


T0 = datetime(2026, 9, 1, tzinfo=timezone.utc)
PAPER = ResolvedReference(
    kind="paper", source="openalex", title="A paper", doi="10.1/x"
)
UNRESOLVED = ResolvedReference(kind="unresolved")


def run_batch(entries, store, resolver, *, library=(), now=T0, refresh=False, **kw):
    return asyncio.run(
        resolve_entries(
            [ReferenceEntry(key=k, text=t) for k, t in entries],
            store=store,
            library=list(library),
            resolver=resolver,
            now=now,
            refresh=refresh,
            **kw,
        )
    )


def test_cache_hit_skips_the_resolver():
    store, resolver = MemoryStore(), FakeResolver(PAPER)
    first = run_batch([("a", PNAS_NO_ID)], store, resolver)
    # Same entry, different numbering/spacing, different caller key.
    second = run_batch([("b", "[3]  " + PNAS_NO_ID)], store, resolver)
    assert first.results["a"] == second.results["b"] == PAPER
    assert len(resolver.calls) == 1


def test_resolved_results_never_expire_unresolved_after_a_week():
    store = MemoryStore()
    run_batch([("a", PNAS_NO_ID)], store, FakeResolver(PAPER))
    run_batch([("b", TRANSLUCE)], store, FakeResolver(UNRESOLVED))

    later = FakeResolver(PAPER)
    run_batch([("a", PNAS_NO_ID)], store, later, now=T0 + timedelta(days=365))
    assert later.calls == []
    run_batch([("b", TRANSLUCE)], store, later, now=T0 + timedelta(days=6))
    assert later.calls == []
    result = run_batch([("b", TRANSLUCE)], store, later, now=T0 + timedelta(days=8))
    assert later.calls == [TRANSLUCE] and result.results["b"] == PAPER


def test_transient_failures_are_not_cached():
    store = MemoryStore()
    failed = run_batch(
        [("a", TRANSLUCE)], store, FakeResolver(UNRESOLVED, transient=True)
    )
    assert failed.pending == ["a"] and "a" not in failed.results
    assert store.rows == {}
    # A result found despite a flaky source is returned, but not stored.
    partial = run_batch([("a", TRANSLUCE)], store, FakeResolver(PAPER, transient=True))
    assert partial.results["a"] == PAPER and store.rows == {}


def test_refresh_bypasses_the_cache():
    store = MemoryStore()
    run_batch([("a", TRANSLUCE)], store, FakeResolver(UNRESOLVED))
    again = FakeResolver(PAPER)
    result = run_batch([("a", TRANSLUCE)], store, again, refresh=True)
    assert again.calls == [TRANSLUCE] and result.results["a"] == PAPER
    assert store.rows[cache_key(TRANSLUCE)][0] == PAPER


def test_library_title_match_wins_and_skips_lookup():
    paper = LibraryPaper(
        id=uuid.uuid4(),
        title="Reconstruction of human metabolic models with large language models",
        authors=("Jiahao Luo", "Jens Nielsen"),
        abstract="GEMs ...",
        preview_url="https://files.test/preview.png",
    )
    resolver = FakeResolver(PAPER)
    result = run_batch([("a", PNAS_NO_ID)], MemoryStore(), resolver, library=[paper])
    ref = result.results["a"]
    assert ref.kind == "library" and ref.library_paper_id == paper.id
    assert ref.preview_url == paper.preview_url and resolver.calls == []


def test_library_match_by_resolved_doi():
    paper = LibraryPaper(
        id=uuid.uuid4(), title="Totally different stored title", doi="10.1/x"
    )
    result = run_batch(
        [("a", TRANSLUCE)], MemoryStore(), FakeResolver(PAPER), library=[paper]
    )
    assert result.results["a"].kind == "library"
    assert result.results["a"].library_paper_id == paper.id


def test_library_rejects_title_without_author():
    paper = LibraryPaper(
        id=uuid.uuid4(),
        title="Reconstruction of human metabolic models with large language models",
        authors=("Someone Else",),
    )
    result = run_batch(
        [("a", PNAS_NO_ID)], MemoryStore(), FakeResolver(PAPER), library=[paper]
    )
    assert result.results["a"] == PAPER


def test_batch_budget_leaves_slow_entries_pending():
    class Slow(FakeResolver):
        async def __call__(self, text: str, deadline: Deadline) -> Resolution:
            if text == TRANSLUCE:
                await asyncio.sleep(5)
            return Resolution(self.reference)

    store = MemoryStore()
    result = run_batch(
        [("fast", PNAS_NO_ID), ("slow", TRANSLUCE)], store, Slow(PAPER), budget_s=0.2
    )
    assert result.results["fast"] == PAPER and result.pending == ["slow"]
    assert list(store.rows) == [cache_key(PNAS_NO_ID)]


@pytest.mark.parametrize("refresh,count,status", [(True, 2, 400), (True, 1, 200)])
def test_refresh_takes_one_entry(monkeypatch, refresh, count, status):
    from fastapi.testclient import TestClient

    from app.auth.dependencies import get_required_user
    from app.database.database import get_db
    from app.main import app
    from app.references import api
    from app.schemas.user import CurrentUser

    async def fake_resolve_entries(entries, **kwargs):
        return service.BatchResult({e.key: PAPER for e in entries}, [])

    monkeypatch.setattr(api, "resolve_entries", fake_resolve_entries)
    monkeypatch.setattr(api, "load_library", lambda db, user_id: [])
    app.dependency_overrides[get_db] = lambda: None
    app.dependency_overrides[get_required_user] = lambda: CurrentUser(
        id=uuid.uuid4(), email="me@example.com"
    )
    try:
        response = TestClient(app).post(
            f"/api/references/resolve?refresh={str(refresh).lower()}",
            json={
                "entries": [{"key": str(i), "text": TRANSLUCE} for i in range(count)]
            },
        )
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == status
    if status == 200:
        assert response.json()["results"]["0"]["kind"] == "paper"


# -- the Postgres store (needs a disposable DB migrated to head) ------------------------

DB_URL = os.getenv("INGEST_TEST_DATABASE_URL")


@pytest.mark.skipif(not DB_URL, reason="INGEST_TEST_DATABASE_URL not set")
def test_db_store_upserts_and_expires():
    from sqlalchemy import create_engine, delete
    from sqlalchemy.orm import Session

    from app.references.models import ReferenceResolution
    from app.references.service import DbReferenceStore

    engine = create_engine(DB_URL)
    key = f"test-{uuid.uuid4().hex}"
    try:
        with Session(engine) as db:
            store = DbReferenceStore(db)
            store.put_many([(key, "entry", UNRESOLVED)], T0)
            assert store.get_many([key], T0 + timedelta(days=6)) == {key: UNRESOLVED}
            assert store.get_many([key], T0 + timedelta(days=8)) == {}
            store.put_many([(key, "entry", PAPER)], T0)  # upsert: never expires
            assert store.get_many([key], T0 + timedelta(days=999)) == {key: PAPER}
    finally:
        with Session(engine) as db:
            db.execute(
                delete(ReferenceResolution).where(ReferenceResolution.key == key)
            )
            db.commit()
        engine.dispose()
