"""`highlights` stage save + regeneration against a real (disposable) Postgres.

Skipped unless `INGEST_TEST_DATABASE_URL` points at a migrated throwaway
database, e.g.:

    docker run --rm -d --name ingest-test -e POSTGRES_PASSWORD=postgres \\
        -p 127.0.0.1:55432:5432 postgres:17
    DATABASE_URL=postgresql://postgres:postgres@127.0.0.1:55432/postgres \\
        uv run alembic upgrade head
    INGEST_TEST_DATABASE_URL=postgresql://postgres:postgres@127.0.0.1:55432/postgres \\
        uv run pytest tests/test_ingest_highlights_save.py

Each test runs in a transaction that is rolled back.
"""

import os
import uuid

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session

from app.core.deadline import Deadline
from app.database.models import Annotation, Highlight
from app.ingest.stages.base import StageContext
from app.ingest.stages.highlights import GeneratedHighlight, Highlights

DB_URL = os.getenv("INGEST_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DB_URL, reason="INGEST_TEST_DATABASE_URL not set")

POSITION = {
    "boundingRect": {
        "x1": 72.0,
        "y1": 90.0,
        "x2": 300.0,
        "y2": 103.0,
        "width": 595.0,
        "height": 842.0,
        "pageNumber": 2,
    },
    "rects": [
        {
            "x1": 72.0,
            "y1": 90.0,
            "x2": 300.0,
            "y2": 103.0,
            "width": 595.0,
            "height": 842.0,
            "pageNumber": 2,
        }
    ],
}


@pytest.fixture
def session():
    assert DB_URL
    engine = create_engine(DB_URL)
    with engine.connect() as conn:
        outer = conn.begin()
        with Session(bind=conn, join_transaction_mode="create_savepoint") as s:
            yield s
        outer.rollback()
    engine.dispose()


@pytest.fixture
def paper(session):
    user_id, paper_id = uuid.uuid4(), uuid.uuid4()
    session.execute(
        text(
            "INSERT INTO users (id, email, auth_provider, provider_user_id,"
            " is_email_verified) VALUES (:id, :email, 'test', :id, true)"
        ),
        {"id": user_id, "email": f"{user_id}@example.com"},
    )
    session.execute(
        text(
            "INSERT INTO papers (id, file_url, status, user_id)"
            " VALUES (:id, 'x', 'reading', :user)"
        ),
        {"id": paper_id, "user": user_id},
    )
    return paper_id, user_id


def ctx_for(paper_id):
    return StageContext(
        paper_id=paper_id,
        stage="highlights",
        attempt=1,
        is_supplementary=False,
        deadline=Deadline(60),
    )


def generated(text_, note="Why.", position=None, page=None):
    return GeneratedHighlight(
        text=text_,
        annotation=note,
        type="result",
        page_number=page,
        position=position,
        start_offset=0 if page else None,
        end_offset=len(text_) if page else None,
    )


def add_highlight(session, paper_id, user_id, raw_text, *, origin, notes=()):
    highlight = Highlight(
        paper_id=paper_id,
        user_id=user_id,
        raw_text=raw_text,
        role="assistant" if origin == "ai" else "user",
        origin=origin,
    )
    session.add(highlight)
    session.flush()
    for role, content in notes:
        session.add(
            Annotation(
                highlight_id=highlight.id,
                paper_id=paper_id,
                user_id=user_id,
                content=content,
                role=role,
            )
        )
    session.flush()
    return highlight.id


def rows(session, paper_id):
    highlights = session.scalars(
        select(Highlight).where(Highlight.paper_id == paper_id)
    ).all()
    return {
        h.raw_text: (h, sorted((a.role, a.content) for a in h.annotations))
        for h in highlights
    }


def test_save_creates_ai_highlights_with_assistant_notes(session, paper):
    paper_id, user_id = paper
    Highlights().save(
        session,
        ctx_for(paper_id),
        [
            generated("Anchored quote.", position=POSITION, page=2),
            generated("Unanchored quote.", note="Still useful."),
        ],
    )
    session.expire_all()
    stored = rows(session, paper_id)
    anchored, notes = stored["Anchored quote."]
    assert (anchored.role, anchored.origin, anchored.type) == (
        "assistant",
        "ai",
        "result",
    )
    assert anchored.user_id == user_id
    assert anchored.page_number == 2 and anchored.position == POSITION
    assert (anchored.start_offset, anchored.end_offset) == (0, 15)
    assert notes == [("assistant", "Why.")]
    unanchored, notes = stored["Unanchored quote."]
    assert unanchored.position is None and unanchored.page_number is None
    assert notes == [("assistant", "Still useful.")]


def test_regeneration_keeps_ai_highlights_the_owner_annotated(session, paper):
    paper_id, user_id = paper
    add_highlight(session, paper_id, user_id, "Mine.", origin="user")
    add_highlight(
        session, paper_id, user_id, "Old AI.", origin="ai", notes=[("assistant", "n")]
    )
    kept = add_highlight(
        session,
        paper_id,
        user_id,
        "Annotated AI.",
        origin="ai",
        notes=[("assistant", "n"), ("user", "my thought")],
    )

    Highlights().save(
        session,
        ctx_for(paper_id),
        [generated("Annotated AI."), generated("Fresh AI.")],
    )
    session.expire_all()
    stored = rows(session, paper_id)

    assert set(stored) == {"Mine.", "Annotated AI.", "Fresh AI."}
    annotated, notes = stored["Annotated AI."]
    assert annotated.id == kept  # kept as is, not duplicated
    assert notes == [("assistant", "n"), ("user", "my thought")]
    # The dropped highlight's assistant note went with it.
    assert (
        session.scalar(
            select(Annotation.id).where(
                Annotation.content == "n", Annotation.highlight_id != kept
            )
        )
        is None
    )
    fresh, notes = stored["Fresh AI."]
    assert fresh.origin == "ai" and notes == [("assistant", "Why.")]
