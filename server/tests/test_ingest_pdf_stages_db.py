"""The PDF stages' `save()`s against a real Postgres.

Skipped unless `INGEST_TEST_DATABASE_URL` points at a disposable database
migrated to head, e.g.

    docker run --rm -d --name ingest-test -e POSTGRES_PASSWORD=postgres \
        -p 127.0.0.1:55432:5432 postgres:17
    DATABASE_URL=postgresql://postgres:postgres@127.0.0.1:55432/postgres \
        uv run alembic upgrade head
    INGEST_TEST_DATABASE_URL=postgresql://postgres:postgres@127.0.0.1:55432/postgres \
        uv run pytest tests/test_ingest_pdf_stages_db.py
"""

from __future__ import annotations

import asyncio
import os
import uuid

import pytest
from sqlalchemy import create_engine, delete, func, select
from sqlalchemy.orm import Session, sessionmaker

from app.core.deadline import Deadline
from app.database.models import Paper
from app.ingest.models import PaperFigure, PaperPage
from app.ingest.pdf.text import EmbeddedMetadata, PageText, TextLayerResult
from app.ingest.stages.base import StageContext
from app.ingest.stages.figures import Figures, StoredFigure, load_boxes
from app.ingest.stages.preview import Preview
from app.ingest.stages.source import Source
from app.ingest.stages.text_layer import TextLayer

DB_URL = os.getenv("INGEST_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DB_URL, reason="INGEST_TEST_DATABASE_URL not set")


@pytest.fixture
def session():
    engine = create_engine(DB_URL)  # pyright: ignore[reportArgumentType]
    with Session(engine) as session:
        yield session
        session.rollback()
    engine.dispose()


@pytest.fixture
def paper(session: Session) -> Paper:
    paper = Paper(
        id=uuid.uuid4(),
        file_url="https://files.test/x.pdf",
        s3_object_key="papers/x/x.pdf",
        title="Title I Typed",
        metadata_source={"title": "user"},
    )
    session.add(paper)
    session.flush()
    return paper


def ctx_for(paper: Paper) -> StageContext:
    return StageContext(
        paper_id=paper.id,  # pyright: ignore[reportArgumentType]
        stage="test",
        attempt=1,
        is_supplementary=False,
        deadline=Deadline(30),
    )


def test_text_layer_save_upserts_pages_and_embedded_metadata(session, paper):
    # The OCR stage got to page 1 first: its column must survive.
    session.add(PaperPage(paper_id=paper.id, page_no=1, ocr_markdown="# OCR"))
    session.flush()
    output = TextLayerResult(
        pages=[PageText(1, 612, 792, "one"), PageText(2, 792, 612, "two")],
        embedded=EmbeddedMetadata(
            title="Embedded Title", authors=["A B"], doi="10.1/x", arxiv_id=None
        ),
    )
    TextLayer().save(session, ctx_for(paper), output)
    TextLayer().save(session, ctx_for(paper), output)  # re-run is idempotent
    session.flush()
    session.expire_all()

    pages = session.scalars(
        select(PaperPage)
        .where(PaperPage.paper_id == paper.id)
        .order_by(PaperPage.page_no)
    ).all()
    assert [(p.page_no, p.text_layer, p.width_pt, p.height_pt) for p in pages] == [
        (1, "one", 612, 792),
        (2, "two", 792, 612),
    ]
    assert pages[0].ocr_markdown == "# OCR"

    fresh = session.get(Paper, paper.id)
    assert fresh.title == "Title I Typed"
    assert fresh.authors == ["A B"]
    assert fresh.doi == "10.1/x"
    assert fresh.metadata_source == {
        "title": "user",
        "authors": "embedded",
        "doi": "embedded",
    }


def test_source_and_preview_save(session, paper):
    Source().save(session, ctx_for(paper), 12)
    Preview().save(session, ctx_for(paper), "https://files.test/preview.png")
    session.flush()
    session.expire_all()
    fresh = session.get(Paper, paper.id)
    assert fresh.page_count == 12
    assert fresh.preview_url == "https://files.test/preview.png"


def test_figures_load_and_save(session, paper):
    fig = PaperFigure(
        paper_id=paper.id,
        page_no=1,
        ocr_image_id="img-0.jpeg",
        bbox={"x0": 1, "y0": 2, "x1": 3, "y1": 4},
    )
    session.add(fig)
    session.flush()

    (box,) = load_boxes(session, paper.id)
    assert box.figure_id == str(fig.id) and box.page_no == 1
    assert dict(box.bbox) == {"x0": 1, "y0": 2, "x1": 3, "y1": 4}

    Figures().save(
        session, ctx_for(paper), [StoredFigure(str(fig.id), "papers/k.png", 40, 30)]
    )
    session.flush()
    session.expire_all()
    fresh = session.get(PaperFigure, fig.id)
    assert (fresh.s3_key, fresh.width, fresh.height) == ("papers/k.png", 40, 30)


def test_text_layer_end_to_end():
    """run() reads the key through a read-only session; save() writes."""
    from tests.test_ingest_pdf_stages import FakeS3, make_pdf

    engine = create_engine(DB_URL)  # pyright: ignore[reportArgumentType]
    factory = sessionmaker(bind=engine)
    s3 = FakeS3()
    paper_id = uuid.uuid4()
    key = f"papers/{paper_id}/p.pdf"
    s3.objects[key] = (make_pdf(metadata={"subject": "doi:10.1234/e2e"}), "")
    with factory() as session:
        session.add(Paper(id=paper_id, file_url="u", s3_object_key=key))
        session.commit()
    try:
        ctx = StageContext(
            paper_id=paper_id,
            stage="text_layer",
            attempt=1,
            is_supplementary=False,
            deadline=Deadline(30),
            session_factory=factory,
            s3=s3,  # pyright: ignore[reportArgumentType]
        )
        stage = TextLayer()
        output = asyncio.run(stage.run(ctx))
        with factory() as session:
            stage.save(session, ctx, output)
            session.commit()
            paper = session.get(Paper, paper_id)
            assert paper.doi == "10.1234/e2e"
            count = session.scalar(
                select(func.count()).where(PaperPage.paper_id == paper_id)
            )
            assert count == 2
    finally:
        with factory() as session:
            session.execute(delete(Paper).where(Paper.id == paper_id))
            session.commit()
        engine.dispose()
