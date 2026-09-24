from types import SimpleNamespace
from unittest.mock import MagicMock

from app.api import repo_api


def test_mark_checked_rolls_back_and_retries(monkeypatch):
    calls = []

    def mark(session, row, **fields):
        calls.append(fields)
        if len(calls) == 1:
            raise RuntimeError("transient")

    monkeypatch.setattr(repo_api.paper_repo_crud, "mark", mark)
    session = MagicMock()
    repo_api._mark_checked(session, SimpleNamespace(id="r1"), status="failed")

    assert len(calls) == 2
    session.rollback.assert_called_once()
