"""Crossref / OpenAlex / arXiv parsing and the verified lookup, on recorded
responses (httpx.MockTransport; no network)."""

from datetime import date

import httpx
import pytest

from app.core.deadline import Deadline
from app.ingest import metadata_lookup as lookup
from app.ingest.metadata_ids import Identifier
from app.ingest.models import MetadataSource
from app.ingest.sources import arxiv, crossref, openalex
from app.ingest.sources.work_record import Author, WorkRecord
from tests.ingest_metadata_fixtures import (
    ARXIV_PAGE_1,
    ARXIV_ROUTES,
    PNAS_PAGE_1,
    PNAS_ROUTES,
    client_for,
    fixture,
    fixture_json,
)

# -- parsing ------------------------------------------------------------------


def test_crossref_parse_work():
    record = crossref.parse_work(fixture_json("crossref_pnas.json")["message"])
    assert record.source is MetadataSource.CROSSREF
    assert record.title == (
        "Reconstruction of human metabolic models with large language models"
    )
    assert record.authors[0] == Author(name="Jiahao Luo", family="Luo")
    assert record.doi == "10.1073/pnas.2516511123"
    assert record.journal == "Proceedings of the National Academy of Sciences"
    assert record.publisher == "National Academy of Sciences"
    assert record.publish_date == date(2026, 4, 8)
    assert record.institutions  # from the author affiliations
    assert record.work_type == "journal-article"


def test_crossref_jats_abstract_and_subtitle():
    record = crossref.parse_work(
        {
            "title": ["Deep things"],
            "subtitle": ["a primer"],
            "abstract": "<jats:title>Abstract</jats:title><jats:p>We &amp; they"
            " <jats:italic>study</jats:italic>.</jats:p>",
            "author": [{"name": "The Things Consortium"}],
            "issued": {"date-parts": [[2021, 3]]},
        }
    )
    assert record.title == "Deep things: a primer"
    assert record.abstract == "We & they study ."
    assert record.authors == [Author(name="The Things Consortium")]
    assert record.publish_date == date(2021, 3, 1)


def test_openalex_parse_work():
    record = openalex.parse_work(fixture_json("openalex_pnas.json"))
    assert record.source is MetadataSource.OPENALEX
    assert record.openalex_id == "W7151904352"
    assert record.doi == "10.1073/pnas.2516511123"
    assert record.journal == "Proceedings of the National Academy of Sciences"
    assert record.publisher == "National Academy of Sciences"
    assert record.abstract and record.abstract.split()[0:2] == [
        "Genome-scale",
        "metabolic",
    ]
    assert "Chalmers University of Technology" in record.institutions
    assert record.keywords == []  # machine-assigned keywords are not used


def test_openalex_arxiv_work_carries_the_arxiv_id():
    record = openalex.parse_work(fixture_json("openalex_arxiv.json"))
    assert record.doi == "10.48550/arxiv.2404.15255"
    assert record.arxiv_id == "2404.15255"


def test_openalex_family_first_names_are_turned_around():
    work = {
        "authorships": [
            {"author": {"display_name": "Vaswani, Ashish"}},
            {"author": {"display_name": "Noam Shazeer"}},
            {"author": {"display_name": "Martin Luther King, Jr."}},
            {"author": {}, "raw_author_name": "Parmar, N."},
        ]
    }
    assert openalex.parse_work(work).authors == [
        Author(name="Ashish Vaswani", family="Vaswani"),
        Author(name="Noam Shazeer"),
        Author(name="Martin Luther King, Jr."),
        Author(name="N. Parmar", family="Parmar"),
    ]


def test_openalex_abstract_from_index():
    index = {"world": [1, 3], "hello": [0], "again": [2]}
    assert openalex.abstract_from_index(index) == "hello world again world"
    assert openalex.abstract_from_index(None) is None


def test_arxiv_parse_feed():
    [record] = arxiv.parse_feed(fixture("arxiv_2404.15255.xml"))
    assert record.source is MetadataSource.ARXIV
    assert record.arxiv_id == "2404.15255"
    assert record.title == "How to use and interpret activation patching"
    assert [a.name for a in record.authors] == ["Stefan Heimersheim", "Neel Nanda"]
    assert record.doi == "10.48550/arxiv.2404.15255"  # arXiv's own DOI
    assert record.publish_date == date(2024, 4, 23)
    assert record.journal is None


def test_arxiv_parse_feed_published_version_and_errors():
    feed = """<?xml version='1.0' encoding='UTF-8'?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:arxiv="http://arxiv.org/schemas/atom">
  <entry>
    <id>http://arxiv.org/abs/hep-th/9901001v2</id>
    <title>Old
      style   title</title>
    <published>1999-01-04T00:00:00Z</published>
    <arxiv:doi>10.1016/S0550-3213(99)00001-X</arxiv:doi>
    <arxiv:journal_ref>Nucl.Phys. B550 (1999) 1</arxiv:journal_ref>
    <author><name>A. Physicist</name></author>
  </entry>
  <entry>
    <id>http://arxiv.org/api/errors#incorrect_id_format_for_1234</id>
    <title>Error</title>
  </entry>
</feed>"""
    [record] = arxiv.parse_feed(feed)
    assert record.arxiv_id == "hep-th/9901001"
    assert record.title == "Old style title"
    assert record.doi == "10.1016/s0550-3213(99)00001-x"
    assert record.journal == "Nucl.Phys. B550 (1999) 1"


@pytest.mark.asyncio
async def test_get_work_404_is_none():
    async with client_for({}) as client:
        assert await crossref.get_work(client, "10.1/none", Deadline(10)) is None
        assert await openalex.get_work(client, "10.1/none", Deadline(10)) is None


@pytest.mark.asyncio
async def test_lookup_sends_query_params_and_polite_ids(monkeypatch):
    monkeypatch.setenv("CONTACT_EMAIL", "me@example.org")
    monkeypatch.setenv("OPENALEX_API_KEY", "k123")
    calls: list[str] = []
    async with client_for(ARXIV_ROUTES, calls) as client:
        await arxiv.get_paper(client, "2404.15255", Deadline(10))
        await crossref.search(client, "A title", Deadline(10))
        await openalex.search(client, "A title", Deadline(10))
    assert "id_list=2404.15255" in calls[0]
    assert "query.bibliographic=A+title" in calls[1]
    assert "mailto=me%40example.org" in calls[1]
    assert "search=A+title" in calls[2] and "api_key=k123" in calls[2]


# -- verification --------------------------------------------------------------


def test_title_match_exact_across_line_breaks():
    title = "Reconstruction of human metabolic models with large language models"
    assert lookup.title_matches(title, PNAS_PAGE_1)


def test_title_match_tolerates_small_differences():
    page = "Scaling α-Divergence Minimization for Very Large Models\nA. Author"
    assert lookup.title_matches(
        "Scaling $\\alpha$-Divergence Minimization for Very Large Models", page
    )


def test_title_match_rejects_a_different_paper():
    assert not lookup.title_matches(
        "A community-driven global reconstruction of human metabolism", PNAS_PAGE_1
    )
    # A citation's title words scattered over the page don't add up either.
    assert not lookup.title_matches(
        "Large language models as models of human cognition", PNAS_PAGE_1
    )


def test_short_titles_need_an_exact_match():
    assert lookup.title_matches("Mamba", "Mamba\nA. Gu, T. Dao")
    assert not lookup.title_matches("Mamba Two", "Mamba\nA. Gu, T. Dao")


def test_author_on_page():
    record = WorkRecord(
        source=MetadataSource.CROSSREF,
        authors=[Author("Nobody Here"), Author("Devlin Moyer", family="Moyer")],
    )
    assert lookup.author_on_page(record, PNAS_PAGE_1)
    record.authors = [Author("Jane Roe", family="Roe")]
    assert not lookup.author_on_page(record, PNAS_PAGE_1)


def test_verifies_needs_title_and_author():
    title = "Reconstruction of human metabolic models with large language models"
    good = WorkRecord(
        MetadataSource.OPENALEX, title=title, authors=[Author("Hao Wang")]
    )
    assert lookup.verifies(good, PNAS_PAGE_1)
    no_author = WorkRecord(MetadataSource.OPENALEX, title=title)
    assert not lookup.verifies(no_author, PNAS_PAGE_1)
    wrong_author = WorkRecord(
        MetadataSource.OPENALEX, title=title, authors=[Author("Jane Roe")]
    )
    assert not lookup.verifies(wrong_author, PNAS_PAGE_1)


# -- resolve() ---------------------------------------------------------------------


def doi(value: str, where: str = "page 1") -> Identifier:
    return Identifier("doi", value, where)


@pytest.mark.asyncio
async def test_resolve_doi_merges_crossref_and_openalex():
    async with client_for(PNAS_ROUTES) as client:
        result = await lookup.resolve(
            client,
            Deadline(30),
            identifiers=[doi("10.1073/pnas.2516511123")],
            titles=[],
            page1=PNAS_PAGE_1,
        )
    assert result is not None
    fields = result.fields()
    assert fields["title"][1] is MetadataSource.CROSSREF
    assert fields["authors"][0][:2] == ["Jiahao Luo", "Hao Wang"]
    assert fields["doi"] == ("10.1073/pnas.2516511123", MetadataSource.CROSSREF)
    assert fields["openalex_id"] == ("W7151904352", MetadataSource.OPENALEX)
    assert fields["abstract"][0].startswith("Genome-scale metabolic models (GEMs)")
    assert fields["abstract"][1] is MetadataSource.CROSSREF
    assert fields["institutions"][1] is MetadataSource.OPENALEX
    assert fields["publish_date"] == (date(2026, 4, 8), MetadataSource.CROSSREF)
    assert fields["journal"][0] == "Proceedings of the National Academy of Sciences"
    assert "keywords" not in fields


@pytest.mark.asyncio
async def test_resolve_rejects_a_cited_doi_and_takes_the_next():
    cited = fixture_json("crossref_pnas.json")
    cited["message"]["title"] = [
        "A community-driven global reconstruction of human metabolism"
    ]
    cited["message"]["DOI"] = "10.1038/nbt.2488"
    routes = {"api.crossref.org/works/10.1038/nbt.2488": cited, **PNAS_ROUTES}
    attempt = lookup.Attempt()
    async with client_for(routes) as client:
        result = await lookup.resolve(
            client,
            Deadline(30),
            identifiers=[doi("10.1038/nbt.2488"), doi("10.1073/pnas.2516511123")],
            titles=[],
            page1=PNAS_PAGE_1,
            attempt=attempt,
        )
    assert result is not None and "10.1073/pnas.2516511123" in result.via
    assert len(attempt.tried) == 2


@pytest.mark.asyncio
async def test_resolve_arxiv():
    async with client_for(ARXIV_ROUTES) as client:
        result = await lookup.resolve(
            client,
            Deadline(30),
            identifiers=[Identifier("arxiv", "2404.15255", "page 1")],
            titles=[],
            page1=ARXIV_PAGE_1,
        )
    assert result is not None
    fields = result.fields()
    assert fields["title"][1] is MetadataSource.ARXIV
    assert fields["arxiv_id"] == ("2404.15255", MetadataSource.ARXIV)
    assert fields["doi"] == ("10.48550/arxiv.2404.15255", MetadataSource.ARXIV)
    assert fields["openalex_id"] == ("W4395443869", MetadataSource.OPENALEX)
    assert fields["journal"] == ("arXiv (Cornell University)", MetadataSource.OPENALEX)


@pytest.mark.asyncio
async def test_resolve_by_title_search():
    # Crossref's search misses this paper (recorded); OpenAlex's first hit
    # verifies and is then looked up by its DOI on both sources.
    routes = {
        "api.crossref.org/works?": fixture_json("crossref_search_pnas.json"),
        "api.openalex.org/works?": fixture_json("openalex_search_pnas.json"),
        **PNAS_ROUTES,
    }
    title = "Reconstruction of human metabolic models with large language models"
    async with client_for(routes) as client:
        result = await lookup.resolve(
            client, Deadline(30), identifiers=[], titles=[title], page1=PNAS_PAGE_1
        )
    assert result is not None
    assert result.via == "title search → DOI 10.1073/pnas.2516511123 (title search)"
    assert result.fields()["title"][1] is MetadataSource.CROSSREF


@pytest.mark.asyncio
async def test_resolve_nothing_verifies():
    routes = {
        "api.crossref.org/works?": fixture_json("crossref_search_pnas.json"),
        "api.openalex.org/works?": {"results": []},
    }
    async with client_for(routes) as client:
        result = await lookup.resolve(
            client,
            Deadline(30),
            identifiers=[],
            titles=["Some other title entirely"],
            page1=PNAS_PAGE_1,
        )
    assert result is None


@pytest.mark.asyncio
async def test_resolve_survives_one_source_down():
    routes = {
        "api.crossref.org": 503,
        "api.openalex.org/works/doi:10.1073/pnas.2516511123": fixture_json(
            "openalex_pnas.json"
        ),
    }
    attempt = lookup.Attempt()
    async with client_for(routes) as client:
        result = await lookup.resolve(
            client,
            Deadline(30),
            identifiers=[doi("10.1073/pnas.2516511123")],
            titles=[],
            page1=PNAS_PAGE_1,
            attempt=attempt,
        )
    assert result is not None
    assert result.fields()["title"][1] is MetadataSource.OPENALEX
    assert attempt.retryable_error is not None  # recorded, not raised


@pytest.mark.asyncio
async def test_resolve_all_down_records_errors():
    attempt = lookup.Attempt()
    routes = {"api.": httpx.ConnectError("down")}
    async with client_for(routes) as client:
        result = await lookup.resolve(
            client,
            Deadline(30),
            identifiers=[doi("10.1073/pnas.2516511123")],
            titles=["Reconstruction of human metabolic models"],
            page1=PNAS_PAGE_1,
            attempt=attempt,
        )
    assert result is None
    assert isinstance(attempt.retryable_error, httpx.ConnectError)


@pytest.mark.asyncio
async def test_resolve_without_page_text_does_nothing():
    calls: list[str] = []
    async with client_for(PNAS_ROUTES, calls) as client:
        result = await lookup.resolve(
            client,
            Deadline(30),
            identifiers=[doi("10.1073/pnas.2516511123")],
            titles=[],
            page1="   ",
        )
    assert result is None and calls == []
