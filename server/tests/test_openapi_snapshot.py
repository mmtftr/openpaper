"""The client's generated API types must match the server.

If this fails, run `cd client && yarn gen:api` and commit the result.
"""

from pathlib import Path

from app.scripts.export_openapi import openapi_json

SNAPSHOT = Path(__file__).resolve().parents[2] / "client/src/lib/api/openapi.json"


def test_client_openapi_snapshot_is_current():
    assert SNAPSHOT.exists(), "missing client/src/lib/api/openapi.json — run `yarn gen:api`"
    assert SNAPSHOT.read_text() == openapi_json(), (
        "client/src/lib/api/openapi.json is stale — run `cd client && yarn gen:api`"
    )
