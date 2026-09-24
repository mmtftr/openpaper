"""Behaviour that changed when the paper-side routers got typed responses."""

import uuid
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import document_api, paper_api
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
    monkeypatch.setattr(
        paper_api.paper_crud, "get_multi_uploads_completed", lambda *a, **k: []
    )
    monkeypatch.setattr(paper_api.paper_crud, "get_top_relevant_papers", lambda *a, **k: [])
    response = _client(paper_api.paper_router, "/api/paper").get(f"/api/paper/{route}")
    assert response.status_code == 200
    assert response.json() == {"papers": []}


def test_missing_paper_is_a_detail_404(monkeypatch):
    monkeypatch.setattr(paper_api.paper_crud, "get", lambda *a, **k: None)
    client = _client(paper_api.paper_router, "/api/paper")
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
