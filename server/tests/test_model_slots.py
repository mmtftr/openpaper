"""Model slots (per-call-site model choice) and the one-shot call path."""

import asyncio
from dataclasses import replace

import pytest
from pydantic import BaseModel
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from app.llm import oneshot
from app.llm._pai_compat import attach_transport_closer
from app.llm.model_registry import (
    LLMProvider,
    ModelRegistry,
    ModelRole,
    ModelSpec,
    _ProviderConfig,
)
from app.llm.model_slots import SLOT_DEFAULTS, resolve_slot


def _registry(default=LLMProvider.CODEX_PROXY, *, with_openai=True):
    configs = {
        LLMProvider.CODEX_PROXY: _ProviderConfig(
            "k", "http://proxy/v1", "gpt-6-astra", "gpt-5.4-mini"
        ),
    }
    if with_openai:
        configs[LLMProvider.OPENAI] = _ProviderConfig(
            "k", None, "gpt-5.5", "gpt-5.4-mini-azure"
        )
    return ModelRegistry([], configs, default)


# Today's per-call-site choices: (provider, model) with the codex proxy as
# the default provider and Azure/OpenAI configured alongside it.
@pytest.mark.parametrize(
    "slot,provider,model_id",
    [
        ("chat.default", LLMProvider.CODEX_PROXY, "gpt-6-astra"),
        ("quick_question", LLMProvider.CODEX_PROXY, "gpt-6-astra"),
        ("chat.reconcile", LLMProvider.OPENAI, "gpt-5.4-mini-azure"),
        ("chat.title", LLMProvider.OPENAI, "gpt-5.4-mini-azure"),
        ("discover", LLMProvider.OPENAI, "gpt-5.4-mini-azure"),
        ("ingest.outline", LLMProvider.OPENAI, "gpt-5.4-mini-azure"),
    ],
)
def test_slot_defaults_reproduce_call_site_choices(slot, provider, model_id):
    resolved = resolve_slot(slot, _registry())
    assert (resolved.spec.provider, resolved.spec.id) == (provider, model_id)
    assert resolved.reasoning_effort is None


def test_every_slot_is_listed():
    assert set(SLOT_DEFAULTS) == {
        "chat.default",
        "chat.reconcile",
        "chat.title",
        "quick_question",
        "discover",
        "ingest.outline",
    }


def test_chat_default_matches_the_registry_default():
    registry = _registry()
    assert resolve_slot("chat.default", registry).spec == registry.resolve()


def test_pinned_provider_falls_back_to_default_when_unconfigured():
    resolved = resolve_slot("ingest.outline", _registry(with_openai=False))
    assert resolved.spec.provider == LLMProvider.CODEX_PROXY
    assert resolved.spec.id == "gpt-5.4-mini"


def test_unknown_slot_raises():
    with pytest.raises(KeyError):
        resolve_slot("nope", _registry())


# -- oneshot ---------------------------------------------------------------


class Pick(BaseModel):
    items: list[str]


class FakeRegistry:
    def __init__(self, fn, spec):
        self.fn = fn
        self.spec = spec
        self.built = []
        self.closed = 0

    def resolve(self, provider=None, model_id=None, role=ModelRole.DEFAULT):
        return self.spec

    def build_model(self, spec):
        self.built.append(spec)

        async def close():
            self.closed += 1

        return attach_transport_closer(FunctionModel(self.fn), close)

    def build_settings(self, spec, reasoning_effort=None, *, cache_key=None):
        return None


def _spec(**kw):
    return replace(
        ModelSpec(id="gpt-5.4-mini", provider=LLMProvider.OPENAI, display_name="m"),
        **kw,
    )


def test_structured_output_goes_through_the_output_tool(monkeypatch):
    seen = {}

    def fn(messages, info: AgentInfo):
        seen["output_tools"] = [t.name for t in info.output_tools]
        seen["allow_text"] = info.allow_text_output
        seen["instructions"] = messages[0].instructions
        return ModelResponse(
            parts=[ToolCallPart(info.output_tools[0].name, {"items": ["a", "b"]})]
        )

    fake = FakeRegistry(fn, _spec())
    monkeypatch.setattr(oneshot, "get_registry", lambda: fake)
    out = asyncio.run(
        oneshot.complete("discover", "q", output_type=Pick, instructions="sys")
    )
    assert out == Pick(items=["a", "b"])
    assert seen["output_tools"] and seen["allow_text"] is False
    assert seen["instructions"] == "sys"
    # Legacy wire parity: one-shots go over Chat Completions.
    assert fake.built[0].api == "chat"
    assert fake.closed == 1


def test_text_output_and_sync_wrapper_close_the_transport(monkeypatch):
    fake = FakeRegistry(
        lambda messages, info: ModelResponse(parts=[TextPart("  A title \n")]),
        _spec(provider=LLMProvider.CODEX_PROXY, api="chat"),
    )
    monkeypatch.setattr(oneshot, "get_registry", lambda: fake)
    assert oneshot.complete_sync("chat.title", "history") == "  A title \n"
    assert fake.closed == 1


def test_failure_propagates_and_still_closes(monkeypatch):
    def boom(messages, info):
        raise RuntimeError("deterministic failure")

    fake = FakeRegistry(boom, _spec())
    monkeypatch.setattr(oneshot, "get_registry", lambda: fake)
    with pytest.raises(RuntimeError, match="deterministic failure"):
        oneshot.complete_sync("ingest.outline", "x", output_type=Pick)
    assert fake.closed == 1
