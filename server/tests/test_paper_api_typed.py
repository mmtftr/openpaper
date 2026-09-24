"""Behaviour that changed when the paper-side routers got typed responses."""

import uuid
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import document_api
from app.api.paper import detail as paper_detail
from app.api.paper import library as paper_library
from app.api.paper import paper_router
from app.auth.dependencies import get_required_user
from app.database.crud.document_crud import RevisionMismatch
from app.database.database import get_db

USER = SimpleNamespace(id=uuid.uuid4())


def _client(router, prefix: str) -> TestClient:
    app = FastAPI()
    app.include_router(router, prefix=prefix)
    app.dependency_overrides[get_required_user] = lambda: USER
    app.dependency_overrides[get_db] = lambda: None
    return TestClient(app)


@pytest.mark.parametrize("route", ["active", "relevant"])
def test_empty_paper_lists_are_200_not_404(monkeypatch, route):
    # A 404 here used to reject the client's Promise.all and blank the
    # projects list fetched alongside it.
    monkeypatch.setattr(paper_library.library, "library_papers", lambda *a, **k: [])
    monkeypatch.setattr(paper_library.library, "relevant_papers", lambda *a, **k: [])
    response = _client(paper_router, "").get(f"/api/paper/{route}")
    assert response.status_code == 200
    assert response.json() == {"papers": []}


def test_missing_paper_is_a_detail_404(monkeypatch):
    monkeypatch.setattr(paper_detail.paper_crud, "get", lambda *a, **k: None)
    client = _client(paper_router, "")
    response = client.get(f"/api/paper?id={uuid.uuid4()}")
    assert response.status_code == 404
    assert response.json() == {"detail": "Document not found"}
    assert client.get("/api/paper?id=not-a-uuid").status_code == 422


def test_revision_conflict_keeps_the_fields_the_editor_reads(monkeypatch):
    doc = SimpleNamespace(id=uuid.uuid4())
    monkeypatch.setattr(document_api.document_crud, "get", lambda *a, **k: doc)

    def _conflict(*a, **k):
        raise RevisionMismatch(7, "server copy")

    monkeypatch.setattr(
        document_api.document_crud, "update_with_revision_check", _conflict
    )
    response = _client(document_api.document_router, "/api/document").put(
        f"/api/document/{doc.id}", json={"content": "mine", "expected_revision": 6}
    )
    assert response.status_code == 409
    body = response.json()
    assert isinstance(body["detail"], str)
    assert body["error"] == "revision_mismatch"
    assert body["current_revision"] == 7
    assert body["current_content"] == "server copy"


class _FakeDb:
    def __init__(self, paper):
        self.paper = paper

    def add(self, obj):
        pass

    def commit(self):
        pass

    def refresh(self, obj):
        pass

    def get(self, model, id):
        return self.paper if id == self.paper.id else None


def test_edited_fields_are_protected_from_metadata_lookups(monkeypatch):
    from app.database.models import Paper, PaperStatus
    from app.ingest.metadata_lookup import write_fields
    from app.ingest.models import MetadataSource

    paper = Paper(
        id=uuid.uuid4(),
        user_id=USER.id,
        file_url="https://files.test/x.pdf",
        status=PaperStatus.todo,
        title="Crossref Title",
        metadata_source={"title": "crossref", "doi": "crossref"},
    )
    before = paper.metadata_source
    fake_db = _FakeDb(paper)
    monkeypatch.setattr(paper_detail.paper_crud, "get", lambda *a, **k: paper)
    monkeypatch.setattr(paper_detail, "track_event", lambda *a, **k: None)
    app = FastAPI()
    app.include_router(paper_router)
    app.dependency_overrides[get_required_user] = lambda: USER
    app.dependency_overrides[get_db] = lambda: fake_db

    response = TestClient(app).patch(
        f"/api/paper?paper_id={paper.id}",
        json={"title": "My Title", "authors": ["Ada Lovelace"]},
    )
    assert response.status_code == 200, response.text
    assert paper.metadata_source is not before  # reassigned: SQLAlchemy sees it
    assert paper.metadata_source == {
        "title": "user",
        "authors": "user",
        "doi": "crossref",
    }

    crossref = MetadataSource.CROSSREF
    written = write_fields(
        fake_db,  # type: ignore[arg-type]
        paper.id,
        {
            "title": ("Crossref Title", crossref),
            "authors": (["A. Lovelace"], crossref),
            "journal": ("Nature", crossref),
        },
    )
    assert written == ["journal"]
    assert (paper.title, paper.authors) == ("My Title", ["Ada Lovelace"])
