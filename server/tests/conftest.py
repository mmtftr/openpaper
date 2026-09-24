"""Pytest bootstrap: load the dev .env before app modules import."""

import os
import sys
from pathlib import Path

from dotenv import load_dotenv

SERVER_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(SERVER_ROOT / ".env")
if str(SERVER_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVER_ROOT))

# Keep registry tests deterministic regardless of the developer's env.
os.environ.pop("MODEL_OVERRIDES", None)


import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _fresh_settings():
    """`get_settings()` is cached per process; tests that `setenv` need the
    next read to see it."""
    from app.settings import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture(autouse=True)
def _no_stored_model_slot_overrides(monkeypatch):
    """Model slots read overrides from the DB; tests run without one."""
    from app.llm import model_slots

    monkeypatch.setattr(model_slots, "load_overrides", lambda: {})
    model_slots.invalidate_overrides()
    yield
    model_slots.invalidate_overrides()
