"""Upload routes (`app.api.paper_upload_api`) against a disposable Postgres
(the engine tests' fixture) and an in-memory S3."""

from __future__ import annotations

import asyncio
import uuid
from types import SimpleNamespace
from typing import Any, Iterator

import httpx
import pymupdf
import pytest
import test_ingest_engine as engine_tests
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

import app.helpers.s3 as s3_module
from app.api import paper_upload_api as upload_api
from app.auth.dependencies import get_required_user
from app.database.database import get_db
from app.database.models import Paper, ProjectPaper
from app.ingest import graph
from app.ingest.models import IngestStage

pg_url = engine_tests.pg_url
db = engine_tests.db

USER_ID = uuid.uuid4()


class FakeS3Client:
    def __init__(self, objects: dict[str, bytes]) -> None:
        self.objects = objects

    def put_object(self, *, Bucket: str, Key: str, Body: bytes, ContentType: str):
        self.objects[Key] = Body

    def get_paginator(self, name: str) -> Any:
        objects = self.objects

        class Paginator:
            def paginate(self, *, Bucket: str, Prefix: str):
                yield {
                    "Contents": [{"Key": k} for k in objects if k.startswith(Prefix)]
                }

        return Paginator()

    def delete_object(self, *, Bucket: str, Key: str) -> None:
        self.objects.pop(Key, None)

    def delete_objects(self, *, Bucket: str, Delete: dict[str, Any]) -> None:
        for obj in Delete["Objects"]:
            self.objects.pop(obj["Key"], None)


class FakeS3:
    bucket_name = "bucket"

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.s3_client = FakeS3Client(self.objects)

    def _public_url(self, key: str) -> str:
        return f"https://s3.test/bucket/{key}"


def make_pdf(pages: int = 2) -> bytes:
    doc = pymupdf.open()
    for i in range(pages):
        doc.new_page().insert_text((72, 100), f"Page {i + 1}", fontsize=12)
    return doc.tobytes()


@pytest.fixture
def s3(monkeypatch) -> FakeS3:
    fake = FakeS3()
    monkeypatch.setattr(s3_module, "s3_service", fake)
    monkeypatch.setattr(upload_api, "s3_service", fake)
    return fake


@pytest.fixture
def client(db: sessionmaker[Session], s3: FakeS3) -> Iterator[TestClient]:
    with db() as session:
        session.execute(text("DELETE FROM project"))
        session.execute(text("DELETE FROM users"))
        session.execute(
            text(
                "INSERT INTO users (id, email, auth_provider, provider_user_id,"
                " is_email_verified) VALUES (:id, 'me@test', 'email', 'me', true)"
            ),
            {"id": USER_ID},
        )
        session.commit()

    def get_session() -> Iterator[Session]:
        session = db()
        try:
            yield session
        finally:
            session.close()

    app = FastAPI()
    app.include_router(upload_api.paper_upload_router, prefix="/api/paper/upload")
    app.dependency_overrides[get_required_user] = lambda: SimpleNamespace(id=USER_ID)
    app.dependency_overrides[get_db] = get_session
    yield TestClient(app)


def upload(client: TestClient, pdf: bytes, name: str = "2404.15255v2.pdf", **query):
    return client.post(
        "/api/paper/upload",
        params=query,
        files={"file": (name, pdf, "application/pdf")},
    )


def stage_statuses(db: sessionmaker[Session], paper_id: uuid.UUID) -> dict[str, str]:
    with db() as session:
        rows = session.query(IngestStage).filter_by(paper_id=paper_id).all()
        return {r.name: r.status.value for r in rows}


def test_upload_stores_the_pdf_and_queues_ingest(client, db, s3):
    response = upload(client, make_pdf(3))

    assert response.status_code == 201
    paper_id = uuid.UUID(response.json()["paper_id"])
    key = f"papers/{paper_id}/2404.15255v2.pdf"
    assert set(s3.objects) == {key}
    with db() as session:
        paper = session.get(Paper, paper_id)
        assert paper is not None
        assert paper.s3_object_key == key
        assert paper.file_url == f"https://s3.test/bucket/{key}"
        assert paper.page_count == 3
        assert paper.user_id == USER_ID
        assert paper.source_filename == "2404.15255v2.pdf"
        assert paper.source_url is None
    statuses = stage_statuses(db, paper_id)
    assert set(statuses) == set(graph.STAGES)
    assert statuses["source"] == "succeeded"
    assert {n for n, s in statuses.items() if s == "queued"} == {
        "text_layer",
        "preview",
        "ocr",
    }


def test_invalid_pdf_is_400_and_stores_nothing(client, db, s3):
    response = upload(client, b"%PDF-1.7 not really a pdf" * 100, name="bad.pdf")

    assert response.status_code == 400
    assert "readable PDF" in response.json()["detail"]
    assert s3.objects == {}
    with db() as session:
        assert session.query(Paper).count() == 0


def test_supplementary_upload_gets_the_supplementary_stages(client, db):
    parent = uuid.UUID(upload(client, make_pdf()).json()["paper_id"])

    response = upload(client, make_pdf(), name="si.pdf", supplementary_of=str(parent))

    assert response.status_code == 201
    child = uuid.UUID(response.json()["paper_id"])
    assert set(stage_statuses(db, child)) == set(graph.SUPPLEMENTARY_STAGES)
    with db() as session:
        paper = session.get(Paper, child)
        assert paper is not None and paper.supplementary_of_paper_id == parent


def test_unknown_parent_or_project_is_404_before_storing(client, s3):
    missing = str(uuid.uuid4())
    assert upload(client, make_pdf(), supplementary_of=missing).status_code == 404
    assert upload(client, make_pdf(), project_id=missing).status_code == 404
    assert s3.objects == {}


def test_project_upload_links_the_paper(client, db):
    project_id = uuid.uuid4()
    with db() as session:
        session.execute(
            text("INSERT INTO project (id, title, owner_id) VALUES (:id, 'P', :u)"),
            {"id": project_id, "u": USER_ID},
        )
        session.commit()

    response = upload(client, make_pdf(), project_id=str(project_id))

    assert response.status_code == 201
    paper_id = uuid.UUID(response.json()["paper_id"])
    with db() as session:
        link = session.query(ProjectPaper).filter_by(paper_id=paper_id).one()
        assert link.project_id == project_id


def test_failed_commit_deletes_the_stored_pdf(client, db, s3, monkeypatch):
    def broken(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("database went away")

    monkeypatch.setattr(upload_api.service, "enqueue_paper", broken)

    response = upload(client, make_pdf())

    assert response.status_code == 500
    assert s3.objects == {}
    with db() as session:
        assert session.query(Paper).count() == 0


def test_too_large_is_400(client, s3, monkeypatch):
    monkeypatch.setattr(upload_api, "MAX_UPLOAD_BYTES", 100)
    response = upload(client, make_pdf())
    assert response.status_code == 400
    assert "too large" in response.json()["detail"]
    assert s3.objects == {}


def test_from_url_records_the_source_url(client, db, monkeypatch):
    pdf = make_pdf()
    fetched: list[str] = []

    async def fetch(url: str) -> bytes:
        fetched.append(url)
        return pdf

    monkeypatch.setattr(upload_api, "fetch_pdf", fetch)
    url = "https://arxiv.org/pdf/2404.15255v2"

    response = client.post("/api/paper/upload/from-url", json={"url": url})

    assert response.status_code == 201
    assert fetched == [url]
    with db() as session:
        paper = session.get(Paper, uuid.UUID(response.json()["paper_id"]))
        assert paper is not None
        assert paper.source_url == url
        assert paper.source_filename == "2404.15255v2"
        assert str(paper.s3_object_key).endswith("/2404.15255v2.pdf")


@pytest.mark.parametrize(
    "url, name",
    [
        ("https://arxiv.org/pdf/2404.15255v2", "2404.15255v2"),
        ("https://x.org/files/My%20Paper.pdf?dl=1", "My Paper.pdf"),
        ("https://x.org/", None),
    ],
)
def test_url_file_name(url, name):
    assert upload_api._url_file_name(url) == name


# -- import (pasted links) ---------------------------------------------------------


@pytest.fixture
def fetched(monkeypatch) -> list[str]:
    """`fetch_pdf` replaced by a recorder serving a small PDF (no network)."""
    urls: list[str] = []
    pdf = make_pdf()

    async def fetch(url: str) -> bytes:
        urls.append(url)
        return pdf

    monkeypatch.setattr(upload_api, "fetch_pdf", fetch)
    return urls


def import_link(client: TestClient, url: str, **query):
    return client.post("/api/paper/upload/import", params=query, json={"url": url})


def test_import_arxiv_abs_downloads_the_pdf(client, db, fetched):
    response = import_link(client, "https://arxiv.org/abs/2504.11844")

    assert response.status_code == 201
    body = response.json()
    assert body["existing"] is False and body["arxiv_id"] == "2504.11844"
    assert fetched == ["https://arxiv.org/pdf/2504.11844"]
    paper_id = uuid.UUID(body["paper_id"])
    with db() as session:
        paper = session.get(Paper, paper_id)
        assert paper is not None
        assert paper.source_url == "https://arxiv.org/pdf/2504.11844"
        assert str(paper.s3_object_key).endswith("/2504.11844.pdf")
    assert stage_statuses(db, paper_id)["source"] == "succeeded"


def test_import_same_arxiv_paper_returns_the_existing_one(client, db, fetched):
    first = import_link(client, "https://arxiv.org/abs/2504.11844").json()

    again = import_link(client, "https://www.alphaxiv.org/abs/2504.11844v2")

    assert again.status_code == 201
    assert again.json()["existing"] is True
    assert again.json()["paper_id"] == first["paper_id"]
    assert len(fetched) == 1
    with db() as session:
        assert session.query(Paper).count() == 1


def test_import_matches_the_arxiv_id_ingest_recorded(client, db, fetched):
    # Uploaded as a file; the metadata stage later found its arXiv id.
    paper_id = upload(client, make_pdf(), name="paper.pdf").json()["paper_id"]
    with db() as session:
        session.execute(
            text(
                "UPDATE papers SET arxiv_id = '2504.11844', title = 'T',"
                " archived_at = now() WHERE id = :p"
            ),
            {"p": paper_id},
        )
        session.commit()

    response = import_link(client, "arXiv:2504.11844")

    body = response.json()
    assert body["existing"] is True and body["paper_id"] == paper_id
    assert body["title"] == "T"
    assert fetched == []
    with db() as session:
        paper = session.get(Paper, uuid.UUID(paper_id))
        assert paper is not None and paper.archived_at is None  # unarchived


def test_import_other_arxiv_ids_are_not_duplicates(client, fetched):
    import_link(client, "https://arxiv.org/abs/2504.11844")

    other = import_link(client, "https://arxiv.org/abs/2504.1184")  # not an id
    assert (
        import_link(client, "https://arxiv.org/abs/2504.11845").json()["existing"]
        is False
    )
    assert other.json()["existing"] is False  # a generic URL, different source
    assert fetched[-1] == "https://arxiv.org/pdf/2504.11845"


def test_import_existing_paper_is_linked_to_the_project(client, db, fetched):
    project_id = uuid.uuid4()
    with db() as session:
        session.execute(
            text("INSERT INTO project (id, title, owner_id) VALUES (:id, 'P', :u)"),
            {"id": project_id, "u": USER_ID},
        )
        session.commit()
    paper_id = import_link(client, "https://arxiv.org/abs/2504.11844").json()[
        "paper_id"
    ]

    for _ in range(2):  # linking twice is a no-op
        response = import_link(
            client, "https://arxiv.org/pdf/2504.11844", project_id=str(project_id)
        )
        assert response.json()["existing"] is True

    with db() as session:
        links = session.query(ProjectPaper).filter_by(paper_id=paper_id).all()
        assert [link.project_id for link in links] == [project_id]


def test_import_direct_pdf_url_dedupes_on_the_url(client, fetched):
    url = "https://example.org/files/paper.pdf"
    first = import_link(client, url).json()
    again = import_link(client, url).json()

    assert first["existing"] is False and first["arxiv_id"] is None
    assert again["existing"] is True and again["paper_id"] == first["paper_id"]
    assert fetched == [url]


@pytest.mark.parametrize("text", ["hello world", "ftp://x.org/a.pdf", "javascript:1"])
def test_import_rejects_what_isnt_a_link(client, fetched, text):
    assert import_link(client, text).status_code == 400
    assert fetched == []


def test_from_url_resolves_arxiv_pages(client, fetched):
    response = client.post(
        "/api/paper/upload/from-url", json={"url": "https://arxiv.org/abs/2504.11844v2"}
    )
    assert response.status_code == 201
    assert fetched == ["https://arxiv.org/pdf/2504.11844v2"]


# -- fetch_pdf (mocked transport) ----------------------------------------------------


def serve(monkeypatch, handler) -> None:
    from app.core.http import make_client as real

    def make_client(**kwargs: Any):
        return real(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(upload_api, "make_client", make_client)


def test_fetch_pdf_returns_the_pdf(monkeypatch):
    pdf = make_pdf()
    serve(
        monkeypatch,
        lambda request: httpx.Response(
            200, content=pdf, headers={"content-type": "application/pdf"}
        ),
    )
    assert asyncio.run(upload_api.fetch_pdf("https://arxiv.org/pdf/1")) == pdf


def test_fetch_pdf_rejects_a_web_page(monkeypatch):
    serve(
        monkeypatch,
        lambda request: httpx.Response(
            200,
            content=b"<!doctype html><html>" + b" " * 5000,
            headers={"content-type": "text/html; charset=utf-8"},
        ),
    )
    with pytest.raises(HTTPException) as err:
        asyncio.run(upload_api.fetch_pdf("https://arxiv.org/abs/1"))
    assert err.value.status_code == 400
    assert "didn't return a PDF (got text/html)" in err.value.detail


def test_fetch_pdf_rejects_a_short_non_pdf(monkeypatch):
    serve(monkeypatch, lambda request: httpx.Response(200, content=b"nope"))
    with pytest.raises(HTTPException) as err:
        asyncio.run(upload_api.fetch_pdf("https://x.org/a.pdf"))
    assert "didn't return a PDF" in err.value.detail


def test_fetch_pdf_http_error_and_size_cap(monkeypatch):
    serve(monkeypatch, lambda request: httpx.Response(404))
    with pytest.raises(HTTPException) as err:
        asyncio.run(upload_api.fetch_pdf("https://x.org/a.pdf"))
    assert "HTTP 404" in err.value.detail

    monkeypatch.setattr(upload_api, "MAX_UPLOAD_BYTES", 2000)
    serve(monkeypatch, lambda request: httpx.Response(200, content=b"%PDF-" * 1000))
    with pytest.raises(HTTPException) as err:
        asyncio.run(upload_api.fetch_pdf("https://x.org/a.pdf"))
    assert "too large" in err.value.detail


def test_fetch_pdf_times_out(monkeypatch):
    async def slow(request):
        await asyncio.sleep(1)
        return httpx.Response(200, content=make_pdf())

    serve(monkeypatch, slow)
    monkeypatch.setattr(upload_api, "URL_FETCH_TOTAL_S", 0.05)
    with pytest.raises(HTTPException) as err:
        asyncio.run(upload_api.fetch_pdf("https://x.org/a.pdf"))
    assert "timed out" in err.value.detail


# -- delete ------------------------------------------------------------------------


def test_delete_removes_the_prefix_and_legacy_objects(client, db, s3, monkeypatch):
    from app.api.paper import delete as paper_delete
    from app.api.paper import paper_router

    monkeypatch.setattr(paper_delete, "s3_service", s3)
    client.app.include_router(paper_router)  # type: ignore[attr-defined]

    # A paper the old pipeline stored: PDF under uploads/, preview elsewhere,
    # figure images under figures/{id}/ — plus a supplementary on the new layout.
    legacy = uuid.uuid4()
    with db() as session:
        session.execute(
            text(
                "INSERT INTO papers (id, user_id, file_url, status, s3_object_key,"
                " preview_url) VALUES (:id, :u, 'x', 'todo', :key, :preview)"
            ),
            {
                "id": legacy,
                "u": USER_ID,
                "key": f"uploads/{legacy}.pdf",
                "preview": "https://s3.test/bucket/uploads/preview-abc.png",
            },
        )
        session.execute(
            text(
                "INSERT INTO paper_figures (id, paper_id, page_no, ocr_image_id,"
                " bbox, s3_key) VALUES (:id, :p, 1, 'img-0.jpeg', '{}', :key)"
            ),
            {"id": uuid.uuid4(), "p": legacy, "key": f"figures/{legacy}/fig-1.png"},
        )
        session.commit()
    s3.objects.update(
        {
            f"uploads/{legacy}.pdf": b"pdf",
            "uploads/preview-abc.png": b"png",
            f"figures/{legacy}/fig-1.png": b"png",
            f"figures/{legacy}/unreferenced.png": b"png",
            "uploads/someone-else.pdf": b"keep",
        }
    )
    child = upload(client, make_pdf(), supplementary_of=str(legacy)).json()["paper_id"]
    assert any(k.startswith(f"papers/{child}/") for k in s3.objects)

    response = client.delete("/api/paper", params={"id": str(legacy)})

    assert response.status_code == 200
    assert s3.objects == {"uploads/someone-else.pdf": b"keep"}
    with db() as session:
        assert session.query(Paper).count() == 0


def test_library_marks_papers_still_ingesting(client, db):
    from app.api.paper import paper_router

    client.app.include_router(paper_router)  # type: ignore[attr-defined]
    paper_id = upload(client, make_pdf()).json()["paper_id"]

    [item] = client.get("/api/paper/all").json()["papers"]
    assert item["id"] == paper_id and item["processing"] is True

    with db() as session:
        session.execute(
            text("UPDATE ingest_stages SET status = 'succeeded' WHERE paper_id = :p"),
            {"p": paper_id},
        )
        session.commit()
    [item] = client.get("/api/paper/all").json()["papers"]
    assert item["processing"] is False
