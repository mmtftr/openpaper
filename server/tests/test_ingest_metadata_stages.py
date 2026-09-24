"""The `metadata` and `metadata_fallback` stages with a fake context: recorded
API responses (MockTransport), fake DB session, fake S3, fake model."""

import uuid
from datetime import date, datetime
from types import SimpleNamespace
from typing import Any

import httpx
import pymupdf
import pytest
from sqlalchemy.dialects import postgresql

from app.core.deadline import Deadline
from app.ingest import metadata_lookup as lookup
from app.ingest.models import MetadataSource
from app.ingest.stages import metadata as metadata_stage
from app.ingest.stages import metadata_fallback as fallback_stage
from app.ingest.stages.base import StageContext, StageSkipped
from app.ingest.stages.metadata import MetadataInputs, MetadataOutput, PageText
from tests.ingest_metadata_fixtures import (
    PNAS_PAGE_1,
    PNAS_ROUTES,
    client_for,
    fixture_json,
)


class FakeSession:
    """Just enough Session for `ctx.read` and the stages' `save()`."""

    def __init__(self, paper: Any = None) -> None:
        self.paper = paper
        self.statements: list[Any] = []

    def execute(self, statement: Any) -> None:
        self.statements.append(statement)

    def get(self, model: Any, ident: Any) -> Any:
        return self.paper

    def rollback(self) -> None:
        pass

    def close(self) -> None:
        pass


class FakeS3:
    def __init__(self, data: bytes) -> None:
        self.data = data

    def get_object_bytes(self, key: str) -> bytes:
        return self.data


def make_ctx(stage: str, attempt: int = 1, s3: Any = None) -> StageContext:
    return StageContext(
        paper_id=uuid.uuid4(),
        stage=stage,
        attempt=attempt,
        is_supplementary=False,
        deadline=Deadline(60),
        session_factory=lambda: FakeSession(),
        s3=s3,
    )


def paper_row(**fields: Any) -> SimpleNamespace:
    base = {
        name: None
        for name in (
            "title",
            "authors",
            "abstract",
            "publish_date",
            "journal",
            "publisher",
            "doi",
            "arxiv_id",
            "openalex_id",
            "keywords",
            "institutions",
            "attempted_metadata_at",
        )
    }
    base["metadata_source"] = {}
    return SimpleNamespace(**{**base, **fields})


@pytest.fixture
def use_routes(monkeypatch):
    """Point both stages' HTTP client at recorded responses."""

    def install(routes: dict[str, object], calls: list[str] | None = None):
        client = client_for(routes, calls)
        monkeypatch.setattr(metadata_stage, "shared_client", lambda: client)
        monkeypatch.setattr(fallback_stage, "shared_client", lambda: client)
        return client

    return install


def given_inputs(monkeypatch, inputs: MetadataInputs) -> None:
    monkeypatch.setattr(metadata_stage, "load_inputs", lambda *a, **k: inputs)
    monkeypatch.setattr(fallback_stage, "load_inputs", lambda *a, **k: inputs)


def pnas_pdf_with_embedded_doi() -> bytes:
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 100), "Some text", fontsize=12)
    doc.set_metadata({"subject": "PNAS, doi:10.1073/pnas.2516511123"})
    return doc.tobytes()


# -- metadata -------------------------------------------------------------------


@pytest.mark.asyncio
async def test_metadata_resolves_embedded_doi_and_skips_fallback(
    monkeypatch, use_routes
):
    calls: list[str] = []
    use_routes(PNAS_ROUTES, calls)
    given_inputs(
        monkeypatch,
        MetadataInputs(pages={1: PageText(text_layer=PNAS_PAGE_1)}, s3_key="k.pdf"),
    )
    ctx = make_ctx("metadata", s3=FakeS3(pnas_pdf_with_embedded_doi()))

    output = await metadata_stage.Metadata().run(ctx)

    assert output.via == "DOI 10.1073/pnas.2516511123 (embedded)"
    assert output.fields["title"][1] is MetadataSource.CROSSREF
    assert len(calls) == 2  # Crossref + OpenAlex, nothing else

    paper = paper_row(title="My own title", metadata_source={"title": "user"})
    session = FakeSession(paper)
    metadata_stage.Metadata().save(session, ctx, output)

    assert paper.title == "My own title"  # edited by the owner: kept
    assert paper.doi == "10.1073/pnas.2516511123"
    assert paper.publish_date == datetime(2026, 4, 8)
    assert paper.metadata_source["title"] == "user"
    assert paper.metadata_source["doi"] == "crossref"
    assert paper.metadata_source["openalex_id"] == "openalex"
    [statement] = session.statements
    sql = str(
        statement.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )
    assert "UPDATE ingest_stages SET status='skipped'" in sql
    assert "ingest_stages.name = 'metadata_fallback'" in sql
    assert "Found by lookup: DOI 10.1073/pnas.2516511123 (embedded)" in sql


@pytest.mark.asyncio
async def test_metadata_unresolved_writes_nothing(monkeypatch, use_routes):
    use_routes({"api.crossref.org/works?": {"message": {"items": []}}})
    given_inputs(
        monkeypatch,
        MetadataInputs(pages={1: PageText(text_layer="A Study of Things\nJ. Doe\n")}),
    )
    ctx = make_ctx("metadata")

    output = await metadata_stage.Metadata().run(ctx)

    assert output == MetadataOutput()
    session = FakeSession(paper_row())
    metadata_stage.Metadata().save(session, ctx, output)
    assert session.statements == []  # metadata_fallback stays pending


@pytest.mark.asyncio
async def test_metadata_api_down_retries_then_hands_over(monkeypatch, use_routes):
    use_routes({"api.": httpx.ConnectError("down")})
    given_inputs(
        monkeypatch,
        MetadataInputs(pages={1: PageText(text_layer=PNAS_PAGE_1)}),
    )
    with pytest.raises(httpx.ConnectError):
        await metadata_stage.Metadata().run(make_ctx("metadata", attempt=1))
    last = metadata_stage.LOOKUP_ERROR_ATTEMPTS
    output = await metadata_stage.Metadata().run(make_ctx("metadata", attempt=last))
    assert output == MetadataOutput()


@pytest.mark.asyncio
async def test_metadata_finds_the_doi_in_the_page_header(monkeypatch, use_routes):
    use_routes(PNAS_ROUTES)
    given_inputs(
        monkeypatch,
        MetadataInputs(pages={1: PageText(text_layer=PNAS_PAGE_1)}),
    )
    output = await metadata_stage.Metadata().run(make_ctx("metadata"))
    assert output.via == "DOI 10.1073/pnas.2516511123 (page 1 header)"


def test_embedded_keywords_fill_in_only_when_missing():
    hints = metadata_stage.PdfHints(embedded_keywords=["things"])
    fields = metadata_stage.with_embedded_keywords({}, hints)
    assert fields["keywords"] == (["things"], MetadataSource.EMBEDDED)
    kept = {"keywords": (["crossref subject"], MetadataSource.CROSSREF)}
    assert metadata_stage.with_embedded_keywords(kept, hints) == kept


# -- metadata_fallback -----------------------------------------------------------


@pytest.fixture
def fake_llm(monkeypatch):
    """Record LLM calls; return `result` (set by the test)."""
    state: dict[str, Any] = {"calls": [], "result": None}

    async def complete(slot, prompt, *, output_type, instructions=None):
        state["calls"].append((slot, prompt))
        return state["result"]

    def resolve_model(self, ctx=None):
        if ctx is not None:
            ctx.model_used = "openai/test-model"

    monkeypatch.setattr(fallback_stage.oneshot, "complete", complete)
    monkeypatch.setattr(fallback_stage.MetadataFallback, "resolve_model", resolve_model)
    monkeypatch.setattr(fallback_stage, "_resolved_by_lookup", lambda s, pid: False)
    return state


SCAN_PAGE_1_MD = """\
# Reconstruction of human metabolic models with large language models

Jiahao Luo, Hao Wang, Devlin Moyer, Jens Nielsen

PNAS 2026 | https://doi.org/10.1073/pnas.2516511123
"""


@pytest.mark.asyncio
async def test_fallback_resolves_from_ocr_text_without_the_llm(
    monkeypatch, use_routes, fake_llm
):
    use_routes(PNAS_ROUTES)
    given_inputs(
        monkeypatch,
        MetadataInputs(pages={1: PageText(text_layer="", markdown=SCAN_PAGE_1_MD)}),
    )
    ctx = make_ctx("metadata_fallback")
    output = await fallback_stage.MetadataFallback().run(ctx)
    assert output.fields["title"][1] is MetadataSource.CROSSREF
    assert fake_llm["calls"] == []
    assert ctx.model_used is None


@pytest.mark.asyncio
async def test_fallback_llm_extraction_is_unverified(monkeypatch, use_routes, fake_llm):
    use_routes({})  # every lookup 404s
    given_inputs(
        monkeypatch,
        MetadataInputs(
            pages={
                1: PageText(markdown="# Notes on Things\n\nJane Doe\n"),
                2: PageText(markdown="More about things."),
            }
        ),
    )
    fake_llm["result"] = fallback_stage.ExtractedMetadata(
        title="Notes on Things",
        authors=["Jane Doe", "jane doe"],
        abstract="",
        publish_date="2023-05",
        institutions=["Univ. of Things"],
        keywords=["things"],
    )
    ctx = make_ctx("metadata_fallback")

    output = await fallback_stage.MetadataFallback().run(ctx)

    [(slot, prompt)] = fake_llm["calls"]
    assert slot == "ingest.metadata"
    assert "# Notes on Things" in prompt and "More about things." in prompt
    assert ctx.model_used == "openai/test-model"
    assert output.fields == {
        "title": ("Notes on Things", MetadataSource.LLM),
        "authors": (["Jane Doe"], MetadataSource.LLM),
        "publish_date": (date(2023, 5, 1), MetadataSource.LLM),
        "institutions": (["Univ. of Things"], MetadataSource.LLM),
        "keywords": (["things"], MetadataSource.LLM),
    }

    paper = paper_row(metadata_source={"keywords": "user"}, keywords=["mine"])
    fallback_stage.MetadataFallback().save(
        FakeSession(paper),
        ctx,
        output,
    )
    assert paper.title == "Notes on Things"
    assert paper.keywords == ["mine"]
    assert paper.metadata_source == {
        "keywords": "user",
        "title": "llm",
        "authors": "llm",
        "publish_date": "llm",
        "institutions": "llm",
    }


@pytest.mark.asyncio
async def test_fallback_searches_the_llm_title(monkeypatch, use_routes, fake_llm):
    # The OCR didn't mark the title as a heading and there is no DOI, so
    # the heuristics have nothing to search; the model reads the title and
    # that search verifies.
    title = "Reconstruction of human metabolic models with large language models"
    use_routes(
        {
            "search=" + title.replace(" ", "+"): fixture_json(
                "openalex_search_pnas.json"
            ),
            "api.crossref.org/works?": {"message": {"items": []}},
            **PNAS_ROUTES,
        }
    )
    given_inputs(
        monkeypatch,
        MetadataInputs(
            pages={1: PageText(markdown=title + "\n\nJiahao Luo, Hao Wang\n")}
        ),
    )
    fake_llm["result"] = fallback_stage.ExtractedMetadata(title=title)

    output = await fallback_stage.MetadataFallback().run(make_ctx("metadata_fallback"))

    assert len(fake_llm["calls"]) == 1
    assert output.fields["title"][1] is MetadataSource.CROSSREF
    assert output.via and output.via.startswith("title search")


@pytest.mark.asyncio
async def test_fallback_skips_when_the_lookup_already_resolved(monkeypatch):
    monkeypatch.setattr(fallback_stage, "_resolved_by_lookup", lambda s, pid: True)
    with pytest.raises(StageSkipped):
        await fallback_stage.MetadataFallback().run(make_ctx("metadata_fallback"))


def test_resolved_by_lookup_reads_the_title_source():
    session = FakeSession(paper_row(metadata_source={"title": "arxiv"}))
    assert fallback_stage._resolved_by_lookup(session, uuid.uuid4())
    session = FakeSession(paper_row(metadata_source={"title": "llm"}))
    assert not fallback_stage._resolved_by_lookup(session, uuid.uuid4())


def test_fallback_check_config_does_not_need_a_model(monkeypatch):
    def unresolvable(self, ctx=None):
        raise AssertionError("resolved the model up front")

    monkeypatch.setattr(fallback_stage.MetadataFallback, "resolve_model", unresolvable)
    fallback_stage.MetadataFallback().check_config()


@pytest.mark.asyncio
async def test_fallback_without_any_text_skips(monkeypatch, use_routes, fake_llm):
    use_routes({})
    given_inputs(monkeypatch, MetadataInputs(pages={}))
    with pytest.raises(StageSkipped):
        await fallback_stage.MetadataFallback().run(make_ctx("metadata_fallback"))
    assert fake_llm["calls"] == []


# -- write_fields ---------------------------------------------------------------------


def test_write_fields_skips_empty_values_and_keeps_existing():
    paper = paper_row(abstract="old abstract", metadata_source={"abstract": "llm"})
    written = lookup.write_fields(
        FakeSession(paper),
        uuid.uuid4(),
        {
            "title": ("T", MetadataSource.ARXIV),
            "abstract": (None, MetadataSource.ARXIV),
        },
    )
    assert written == ["title"]
    assert paper.abstract == "old abstract"
    assert paper.metadata_source == {"abstract": "llm", "title": "arxiv"}
    assert paper.attempted_metadata_at is not None
