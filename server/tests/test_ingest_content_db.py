"""`paper_pages` readers and the search-vector triggers against a real Postgres.

Skipped unless `INGEST_TEST_DATABASE_URL` points at a disposable database
migrated to head (see tests/test_ingest_pdf_stages_db.py for the recipe).
"""

from __future__ import annotations

import os
import uuid

import pytest
from sqlalchemy import create_engine, text, update
from sqlalchemy.orm import Session

from app.database.models import Paper, User
from app.database.queries.search import search_knowledge_base
from app.ingest import content
from app.ingest.models import PaperFigure, PaperPage
from app.schemas.user import CurrentUser

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
def user(session: Session) -> User:
    user = User(
        id=uuid.uuid4(),
        email=f"{uuid.uuid4().hex}@example.com",
        auth_provider="test",
        provider_user_id=uuid.uuid4().hex,
    )
    session.add(user)
    session.flush()
    return user


@pytest.fixture
def paper(session: Session, user: User) -> Paper:
    paper = Paper(
        id=uuid.uuid4(),
        file_url="https://files.test/x.pdf",
        title="Sparse Autoencoders",
        user_id=user.id,
        status="todo",
    )
    session.add(paper)
    session.flush()
    return paper


def fire_deferred(session: Session) -> None:
    """Run the deferred page trigger now instead of at commit."""
    session.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
    session.execute(text("SET CONSTRAINTS ALL DEFERRED"))


def search_vector(session: Session, paper: Paper) -> str:
    return session.execute(
        text("SELECT ts_vector::text FROM papers WHERE id = :id"), {"id": paper.id}
    ).scalar_one()


def expected_vector(session: Session, title: str, body: str) -> str:
    return session.execute(
        text(
            "SELECT (setweight(to_tsvector('pg_catalog.english', :t), 'A') ||"
            " setweight(to_tsvector('pg_catalog.english', :b), 'D'))::text"
        ),
        {"t": title, "b": body},
    ).scalar_one()


def test_readers_return_pages_figures_and_text_layer(session, paper):
    session.add_all(
        [
            PaperPage(paper_id=paper.id, page_no=2, markdown="two", text_layer="t2"),
            PaperPage(paper_id=paper.id, page_no=1, markdown=None, text_layer="t1"),
            PaperFigure(
                paper_id=paper.id,
                page_no=2,
                ocr_image_id="img-10.jpeg",
                bbox={"x0": 0, "y0": 0, "x1": 1, "y1": 1},
            ),
            PaperFigure(
                paper_id=paper.id,
                page_no=2,
                ocr_image_id="img-9.jpeg",
                label="Figure 2",
                s3_key="papers/x/figures/a.png",
                bbox={"x0": 0, "y0": 0, "x1": 1, "y1": 1},
            ),
        ]
    )
    session.flush()

    assert content.pages(session, paper.id) == [
        content.Page(1, ""),
        content.Page(2, "two"),
    ]
    assert content.text_layer(session, str(paper.id), 1) == "t1"
    assert content.text_layer(session, paper.id, 3) is None
    figs = content.figures(session, paper.id)
    assert [f.ocr_image_id for f in figs] == ["img-9.jpeg", "img-10.jpeg"]
    assert [f.available for f in figs] == [True, False]


def test_search_vector_follows_page_markdown_and_title(session, paper):
    # A fresh paper: title only.
    assert search_vector(session, paper) == expected_vector(
        session, "Sparse Autoencoders", ""
    )

    # OCR rows arrive without final markdown: nothing to index yet.
    session.add_all(
        [
            PaperPage(paper_id=paper.id, page_no=1, ocr_markdown="x"),
            PaperPage(paper_id=paper.id, page_no=2, ocr_markdown="y"),
        ]
    )
    session.flush()
    fire_deferred(session)
    assert search_vector(session, paper) == expected_vector(
        session, "Sparse Autoencoders", ""
    )

    # ocr_repair writes each page's markdown in its own statement.
    for page_no, md in [(2, "dictionary features"), (1, "# Intro\nneurons")]:
        session.execute(
            update(PaperPage)
            .where(PaperPage.paper_id == paper.id, PaperPage.page_no == page_no)
            .values(markdown=md)
        )
    fire_deferred(session)
    assert search_vector(session, paper) == expected_vector(
        session, "Sparse Autoencoders", "# Intro\nneurons\n\ndictionary features"
    )

    # A title edit re-indexes with the pages.
    session.execute(update(Paper).where(Paper.id == paper.id).values(title="SAEs"))
    assert search_vector(session, paper) == expected_vector(
        session, "SAEs", "# Intro\nneurons\n\ndictionary features"
    )

    # Deleting the pages (OCR reprocess) drops their text from the index.
    session.execute(PaperPage.__table__.delete().where(PaperPage.paper_id == paper.id))
    fire_deferred(session)
    assert search_vector(session, paper) == expected_vector(session, "SAEs", "")


def test_knowledge_base_search_matches_page_text_across_a_page_break(
    session, paper, user
):
    session.add_all(
        [
            PaperPage(paper_id=paper.id, page_no=1, markdown="ends with alpha"),
            PaperPage(paper_id=paper.id, page_no=2, markdown="beta starts"),
        ]
    )
    session.flush()
    current = CurrentUser(id=user.id, email=user.email)

    def hits(query: str) -> list[str]:
        return [p.id for p in search_knowledge_base(session, current, query).papers]

    assert hits("STARTS") == [str(paper.id)]
    assert hits("alpha\n\nbeta") == [str(paper.id)]
    assert hits("gamma") == []
