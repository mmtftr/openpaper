"""Domain errors from the data layer map to HTTP responses in one place."""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.errors import install_error_handlers
from app.database.errors import NotFound


def _client() -> TestClient:
    app = FastAPI()
    install_error_handlers(app)

    @app.get("/missing")
    def missing() -> dict:
        raise NotFound("Document not found")

    @app.get("/boom")
    def boom() -> dict:
        raise RuntimeError("db went away")

    return TestClient(app, raise_server_exceptions=False)


def test_not_found_is_a_404_with_its_message():
    response = _client().get("/missing")
    assert response.status_code == 404
    assert response.json() == {"detail": "Document not found"}


def test_other_errors_are_a_generic_500():
    response = _client().get("/boom")
    assert response.status_code == 500
    assert response.json() == {"detail": "Internal server error"}
