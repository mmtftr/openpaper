"""Archiving papers: the `/archive` route with a stubbed CRUD, and the list
queries against a disposable Postgres (the engine tests' fixtures)."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

import test_ingest_engine as engine_tests
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from app.api.paper import archive as paper_archive
from app.api.paper import library as paper_library
from app.api.paper import paper_router
from app.auth.dependencies import get_required_user
from app.database.crud.paper_crud import paper_crud
from app.database.database import get_db
from app.database.models import Paper, PaperStatus
from app.database.queries import library
from app.schemas.user import CurrentUser

USER = CurrentUser(id=uuid.uuid4(), email="owner@example.com")
NOW = datetime(2026, 9, 27, 12, 0, tzinfo=timezone.utc)


def _client(db: Any = None) -> TestClient:
    app = FastAPI()
    app.include_router(paper_router)
    app.dependency_overrides[get_required_user] = lambda: USER
    app.dependency_overrides[get_db] = lambda: db
    return TestClient(app)


# -- route --------------------------------------------------------------------------


def test_archive_passes_ids_and_flag(monkeypatch):
    ids = [uuid.uuid4(), uuid.uuid4()]
    calls: list[dict[str, Any]] = []

    def set_archived(db, *, paper_ids, archived, user):
        calls.append({"paper_ids": paper_ids, "archived": archived, "user": user})
        return paper_ids, NOW if archived else None

    monkeypatch.setattr(paper_archive.paper_crud, "set_archived", set_archived)
    client = _client()

    response = client.post(
        "/api/paper/archive", json={"paper_ids": [str(i) for i in ids]}
    )
    assert response.status_code == 200, response.text
    assert response.json() == {
        "paper_ids": [str(i) for i in ids],
        "archived_at": NOW.isoformat(),
    }

    response = client.post(
        "/api/paper/archive",
        json={"paper_ids": [str(ids[0])], "archived": False},
    )
    assert response.json() == {"paper_ids": [str(ids[0])], "archived_at": None}
    assert [(c["archived"], c["user"]) for c in calls] == [(True, USER), (False, USER)]


def test_archive_of_no_owned_paper_is_404(monkeypatch):
    monkeypatch.setattr(
        paper_archive.paper_crud, "set_archived", lambda *a, **k: ([], None)
    )
    response = _client().post(
        "/api/paper/archive", json={"paper_ids": [str(uuid.uuid4())]}
    )
    assert response.status_code == 404


def test_archive_needs_at_least_one_id():
    response = _client().post("/api/paper/archive", json={"paper_ids": []})
    assert response.status_code == 422


def test_all_forwards_the_archived_view(monkeypatch):
    seen: list[bool] = []

    def library_papers(db, *, user, archived=False, **kwargs):
        seen.append(archived)
        return []

    monkeypatch.setattr(paper_library.library, "library_papers", library_papers)
    monkeypatch.setattr(paper_library.library, "processing_ids", lambda *a: set())
    client = _client()
    assert client.get("/api/paper/all").json() == {"papers": []}
    assert client.get("/api/paper/all?archived=true").json() == {"papers": []}
    assert seen == [False, True]


# -- against Postgres (skipped without docker) --------------------------------------

pg_url = engine_tests.pg_url
db = engine_tests.db


def _insert_user(session: Session) -> CurrentUser:
    user_id = uuid.uuid4()
    session.execute(
        text(
            "INSERT INTO users (id, email, auth_provider, provider_user_id,"
            " is_email_verified) VALUES (:id, :email, 'test', :pid, true)"
        ),
        {"id": user_id, "email": f"{user_id.hex}@example.com", "pid": user_id.hex},
    )
    return CurrentUser(id=user_id, email=f"{user_id.hex}@example.com")


def _insert_paper(
    session: Session,
    user: CurrentUser,
    *,
    status: PaperStatus = PaperStatus.reading,
) -> uuid.UUID:
    paper_id = uuid.uuid4()
    session.add(
        Paper(
            id=paper_id,
            file_url="x",
            status=status,
            user_id=user.id,
            updated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
    )
    session.flush()
    return paper_id


def _ids(papers: list[Paper]) -> set[uuid.UUID]:
    return {p.id for p in papers}


def test_archived_papers_leave_every_list(db: sessionmaker[Session]):
    with db() as session:
        user = _insert_user(session)
        kept = _insert_paper(session, user)
        todo = _insert_paper(session, user, status=PaperStatus.todo)
        gone = _insert_paper(session, user)
        session.commit()

        matched, archived_at = paper_crud.set_archived(
            session, paper_ids=[gone], archived=True, user=user
        )
        assert matched == [gone] and archived_at is not None

        assert _ids(library.library_papers(session, user=user)) == {kept, todo}
        assert _ids(
            library.library_papers(session, user=user, status=PaperStatus.reading)
        ) == {kept}
        assert _ids(library.relevant_papers(session, user=user)) == {kept, todo}
        assert _ids(library.library_papers(session, user=user, archived=True)) == {gone}
        # Still openable by id.
        assert paper_crud.get(session, id=gone, user=user) is not None


def test_archive_round_trip_keeps_updated_at(db: sessionmaker[Session]):
    with db() as session:
        user = _insert_user(session)
        paper_id = _insert_paper(session, user)
        session.commit()
        before = session.get(Paper, paper_id)
        assert before is not None
        updated_at = before.updated_at

        paper_crud.set_archived(session, paper_ids=[paper_id], archived=True, user=user)
        paper_crud.set_archived(
            session, paper_ids=[paper_id], archived=False, user=user
        )
        session.expire_all()
        paper = session.get(Paper, paper_id)
        assert paper is not None
        assert paper.archived_at is None
        assert paper.updated_at == updated_at
        assert _ids(library.library_papers(session, user=user)) == {paper_id}
        assert library.library_papers(session, user=user, archived=True) == []


def test_archive_skips_other_owners_papers(db: sessionmaker[Session]):
    with db() as session:
        owner = _insert_user(session)
        other = _insert_user(session)
        mine = _insert_paper(session, owner)
        theirs = _insert_paper(session, other)
        session.commit()

        matched, _ = paper_crud.set_archived(
            session, paper_ids=[mine, theirs], archived=True, user=owner
        )
        assert matched == [mine]
        assert _ids(library.library_papers(session, user=other)) == {theirs}


def test_archive_api_against_postgres(db: sessionmaker[Session]):
    with db() as session:
        user = _insert_user(session)
        paper_id = _insert_paper(session, user)
        session.commit()

        app = FastAPI()
        app.include_router(paper_router)
        app.dependency_overrides[get_required_user] = lambda: user
        app.dependency_overrides[get_db] = lambda: session
        client = TestClient(app)

        response = client.post(
            "/api/paper/archive", json={"paper_ids": [str(paper_id)]}
        )
        assert response.status_code == 200, response.text
        assert client.get("/api/paper/all").json() == {"papers": []}
        assert client.get("/api/paper/active").json() == {"papers": []}
        assert client.get("/api/paper/relevant").json() == {"papers": []}
        archived = client.get("/api/paper/all?archived=true").json()["papers"]
        assert [p["id"] for p in archived] == [str(paper_id)]
        assert archived[0]["archived_at"] is not None
