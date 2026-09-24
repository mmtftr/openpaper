"""Model slot overrides: resolution, caching and the /api/settings API.

DB-free: the persistence seam (`model_slot_crud` functions as imported by
the router, and `model_slots.load_overrides`) is replaced by an in-memory
dict.
"""

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import settings_api
from app.auth.dependencies import get_required_user
from app.database.database import get_db
from app.llm import model_slots
from app.llm.model_registry import (
    LLMProvider,
    ModelRegistry,
    ModelSpec,
    _ProviderConfig,
)
from app.llm.model_slots import SlotOverride, resolve_slot
from app.schemas.user import CurrentUser


def _registry():
    configs = {
        LLMProvider.CODEX_PROXY: _ProviderConfig(
            "k", "http://proxy/v1", "gpt-6-astra", "gpt-5.4-mini"
        ),
        LLMProvider.OPENAI: _ProviderConfig("k", None, "gpt-5.5", "gpt-5.4-mini-azure"),
    }
    specs = [
        ModelSpec(
            "gpt-6-astra",
            LLMProvider.CODEX_PROXY,
            "Astra",
            api="chat",
            supports_reasoning_effort=True,
        ),
        ModelSpec(
            "gpt-5.5", LLMProvider.OPENAI, "GPT-5.5", supports_reasoning_effort=True
        ),
        ModelSpec(
            "DeepSeek-V4-Flash-0731",
            LLMProvider.OPENAI,
            "DeepSeek",
            supports_vision=False,
        ),
    ]
    return ModelRegistry(specs, configs, LLMProvider.CODEX_PROXY)


# -- resolve_slot with overrides ------------------------------------------


def test_override_picks_provider_and_model():
    resolved = resolve_slot(
        "chat.title",
        _registry(),
        {"chat.title": SlotOverride("codex_proxy", "gpt-6-astra", "low")},
    )
    assert (resolved.spec.provider, resolved.spec.id) == (
        LLMProvider.CODEX_PROXY,
        "gpt-6-astra",
    )
    assert resolved.reasoning_effort == "low"


def test_provider_only_override_uses_the_slot_role():
    # chat.title is a FAST slot: provider-only picks that provider's fast model.
    resolved = resolve_slot(
        "chat.title", _registry(), {"chat.title": SlotOverride("codex_proxy")}
    )
    assert resolved.spec.id == "gpt-5.4-mini"


def test_unlisted_provider_role_model_is_accepted():
    resolved = resolve_slot(
        "chat.default",
        _registry(),
        {"chat.default": SlotOverride("openai", "gpt-5.4-mini-azure")},
    )
    assert resolved.spec.id == "gpt-5.4-mini-azure"


def test_effort_only_override_keeps_the_default_model():
    resolved = resolve_slot(
        "chat.default",
        _registry(),
        {"chat.default": SlotOverride(reasoning_effort="high")},
    )
    assert resolved.spec.id == "gpt-6-astra"
    assert resolved.reasoning_effort == "high"


@pytest.mark.parametrize(
    "override",
    [
        SlotOverride("gemini", "gemini-3.7-flash"),  # provider not configured
        SlotOverride("openai", "gone-model"),  # model not in the registry
        SlotOverride("nonsense", None),  # unknown provider
    ],
)
def test_unusable_override_falls_back_to_default(override, caplog):
    resolved = resolve_slot("discover", _registry(), {"discover": override})
    assert (resolved.spec.provider, resolved.spec.id) == (
        LLMProvider.OPENAI,
        "gpt-5.4-mini-azure",
    )
    assert "ignoring override" in caplog.text


def test_overrides_are_cached_for_the_ttl(monkeypatch):
    calls = []

    def load():
        calls.append(1)
        return {"chat.title": SlotOverride("codex_proxy", "gpt-6-astra")}

    clock = [100.0]
    monkeypatch.setattr(model_slots, "load_overrides", load)
    monkeypatch.setattr(model_slots.time, "monotonic", lambda: clock[0])
    registry = _registry()

    assert resolve_slot("chat.title", registry).spec.id == "gpt-6-astra"
    assert resolve_slot("chat.title", registry).spec.id == "gpt-6-astra"
    assert len(calls) == 1
    clock[0] += model_slots.OVERRIDE_TTL_SECONDS + 1
    resolve_slot("chat.title", registry)
    assert len(calls) == 2
    model_slots.invalidate_overrides()
    resolve_slot("chat.title", registry)
    assert len(calls) == 3


def test_failed_override_read_uses_defaults(monkeypatch):
    def boom():
        raise RuntimeError("db down")

    monkeypatch.setattr(model_slots, "load_overrides", boom)
    resolved = resolve_slot("chat.title", _registry())
    assert resolved.spec.id == "gpt-5.4-mini-azure"


# -- API ------------------------------------------------------------------


@pytest.fixture
def store(monkeypatch):
    rows: dict[str, SimpleNamespace] = {}

    def set_model_slot(db, slot, *, provider, model, reasoning_effort):
        if provider is None and model is None and reasoning_effort is None:
            rows.pop(slot, None)
            return None
        rows[slot] = SimpleNamespace(
            slot=slot,
            provider=provider,
            model=model,
            reasoning_effort=reasoning_effort,
            updated_at=datetime(2026, 9, 25, tzinfo=timezone.utc),
        )
        return rows[slot]

    monkeypatch.setattr(
        settings_api, "list_model_slots", lambda db: list(rows.values())
    )
    monkeypatch.setattr(settings_api, "get_model_slot", lambda db, slot: rows.get(slot))
    monkeypatch.setattr(settings_api, "set_model_slot", set_model_slot)
    monkeypatch.setattr(settings_api, "get_registry", _registry)
    return rows


@pytest.fixture
def client(store):
    app = FastAPI()
    app.include_router(settings_api.settings_router, prefix="/api/settings")
    app.dependency_overrides[get_db] = lambda: None
    app.dependency_overrides[get_required_user] = lambda: CurrentUser(
        id=uuid.uuid4(), email="me@example.com"
    )
    return TestClient(app)


def test_get_lists_every_slot_with_defaults(client):
    body = client.get("/api/settings/models").json()
    slots = {s["slot"]: s for s in body["slots"]}
    assert list(slots) == list(model_slots.SLOT_DEFAULTS)
    title = slots["chat.title"]
    assert title["role"] == "fast"
    assert title["override"] is None
    assert title["default"] == {
        "provider": "openai",
        "model": "gpt-5.4-mini-azure",
        "model_name": "gpt-5.4-mini-azure",
        "reasoning_effort": None,
    }
    assert title["effective"] == title["default"]

    providers = {p["id"]: p for p in body["providers"]}
    assert providers["codex_proxy"]["is_default"] is True
    assert providers["openai"]["fast_model"] == "gpt-5.4-mini-azure"
    models = {(m["provider"], m["id"]): m for m in body["models"]}
    # Picker models plus unlisted provider role models.
    assert ("openai", "gpt-5.4-mini-azure") in models
    assert ("codex_proxy", "gpt-5.4-mini") in models
    assert models[("openai", "DeepSeek-V4-Flash-0731")]["supports_vision"] is False


def test_put_sets_then_resets_an_override(client, store):
    resp = client.put(
        "/api/settings/models/discover",
        json={
            "provider": "codex_proxy",
            "model": "gpt-6-astra",
            "reasoning_effort": "high",
        },
    )
    assert resp.status_code == 200
    slot = resp.json()
    assert slot["override"]["model"] == "gpt-6-astra"
    assert slot["effective"]["model"] == "gpt-6-astra"
    assert slot["effective"]["reasoning_effort"] == "high"
    assert slot["default"]["model"] == "gpt-5.4-mini-azure"
    assert "discover" in store

    resp = client.put(
        "/api/settings/models/discover",
        json={"provider": None, "model": None, "reasoning_effort": None},
    )
    assert resp.status_code == 200
    assert resp.json()["override"] is None
    assert resp.json()["effective"]["model"] == "gpt-5.4-mini-azure"
    assert "discover" not in store


def test_put_invalidates_the_cache(client, monkeypatch):
    invalidated = []
    monkeypatch.setattr(
        settings_api, "invalidate_overrides", lambda: invalidated.append(1)
    )
    client.put("/api/settings/models/chat.title", json={"provider": "codex_proxy"})
    assert invalidated == [1]


@pytest.mark.parametrize(
    "slot,payload,status",
    [
        ("nope", {"provider": "openai"}, 404),
        ("discover", {"provider": "gemini"}, 400),  # not configured
        ("discover", {"provider": "openai", "model": "gone"}, 400),
        ("discover", {"model": "gpt-5.5"}, 400),  # model without provider
        # DeepSeek has no reasoning effort.
        (
            "discover",
            {
                "provider": "openai",
                "model": "DeepSeek-V4-Flash-0731",
                "reasoning_effort": "low",
            },
            400,
        ),
        ("discover", {"reasoning_effort": "extreme"}, 422),
    ],
)
def test_put_rejects_invalid_choices(client, store, slot, payload, status):
    resp = client.put(f"/api/settings/models/{slot}", json=payload)
    assert resp.status_code == status
    if status != 422:
        assert isinstance(resp.json()["detail"], str)
    assert store == {}


def test_get_flags_a_stale_override(client, store):
    store["chat.title"] = SimpleNamespace(
        slot="chat.title",
        provider="openai",
        model="removed-model",
        reasoning_effort=None,
        updated_at=None,
    )
    slot = next(
        s
        for s in client.get("/api/settings/models").json()["slots"]
        if s["slot"] == "chat.title"
    )
    assert slot["override_error"]
    assert slot["effective"]["model"] == "gpt-5.4-mini-azure"
