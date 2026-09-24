"""Unit tests for `ModelRegistry.build_settings`.

The reasoning-effort behavior is covered in `test_chat_runtime.py`; this
file covers the prompt-cache KEY setting and the fact that every setting is
gated independently (the function used to bail out early whenever no
reasoning effort was requested, which would have swallowed the cache
setting too).

Capability defaults are asserted against `_build_spec`, so a change to the
family defaults or `_KNOWN_MODEL_CAPS` that silently starts (or stops)
sending `prompt_cache_key` to a deployment fails here. `prompt_cache_retention`
is intentionally never sent — see `build_settings` — and a test pins that.
"""

from __future__ import annotations

from typing import Optional

import pytest

from app.llm.model_registry import ModelRegistry, ModelSpec, _build_spec
from app.llm.model_registry import LLMProvider

CACHE_KEY = "openpaper:conv-1"


@pytest.fixture
def registry() -> ModelRegistry:
    return ModelRegistry([], {}, LLMProvider.OPENAI)


def _spec(
    *,
    api: str = "responses",
    provider: LLMProvider = LLMProvider.OPENAI,
    effort: bool = False,
    summaries: bool = False,
    cache_key: bool = False,
    model_id: str = "test-model",
) -> ModelSpec:
    return ModelSpec(
        id=model_id,
        provider=provider,
        display_name=model_id,
        api=api,  # type: ignore[arg-type]
        supports_reasoning_effort=effort,
        supports_reasoning_summaries=summaries,
        supports_prompt_cache_key=cache_key,
    )


class TestNothingApplies:
    def test_none_without_effort_or_cache_key(self, registry):
        assert registry.build_settings(_spec(effort=True, cache_key=True)) is None

    def test_none_when_the_model_supports_neither_setting(self, registry):
        settings = registry.build_settings(_spec(), "high", cache_key=CACHE_KEY)
        assert settings is None

    def test_none_for_a_native_api_model(self, registry):
        """Anthropic/Gemini never get OpenAI settings, whatever the flags."""
        spec = _spec(
            api="native",
            provider=LLMProvider.ANTHROPIC,
            effort=True,
            cache_key=True,
        )
        assert registry.build_settings(spec, "high", cache_key=CACHE_KEY) is None

    def test_empty_cache_key_is_ignored(self, registry):
        spec = _spec(cache_key=True)
        assert registry.build_settings(spec, None, cache_key="") is None


class TestPromptCacheKey:
    def test_set_when_supported(self, registry):
        spec = _spec(cache_key=True)
        settings = registry.build_settings(spec, None, cache_key=CACHE_KEY)
        assert settings == {"openai_prompt_cache_key": CACHE_KEY}

    def test_absent_when_unsupported(self, registry):
        spec = _spec(cache_key=False, effort=True)
        settings = registry.build_settings(spec, "high", cache_key=CACHE_KEY)
        assert "openai_prompt_cache_key" not in settings

    def test_absent_when_the_caller_passes_no_key(self, registry):
        spec = _spec(cache_key=True, effort=True)
        settings = registry.build_settings(spec, "high")
        assert settings == {"openai_reasoning_effort": "high"}

    def test_sent_on_chat_completions_too(self, registry):
        spec = _spec(api="chat", cache_key=True)
        settings = registry.build_settings(spec, None, cache_key=CACHE_KEY)
        assert settings["openai_prompt_cache_key"] == CACHE_KEY

    def test_retention_is_never_sent(self, registry):
        """Deliberate: the default cache lifetime is enough for a live
        thread, and Fireworks-hosted Azure deployments 400 on the parameter
        ("Extra inputs are not permitted", verified live 2026-09-06)."""
        for api in ("responses", "chat"):
            spec = _spec(api=api, cache_key=True, effort=True)
            settings = registry.build_settings(spec, "high", cache_key=CACHE_KEY)
            assert "openai_prompt_cache_retention" not in settings


class TestReasoningEffortUnchanged:
    """The cache setting must not perturb the existing effort behavior."""

    def test_responses_effort_and_summary(self, registry):
        spec = _spec(effort=True, summaries=True, cache_key=True)
        settings = registry.build_settings(spec, "low", cache_key=CACHE_KEY)
        assert settings["openai_reasoning_effort"] == "low"
        assert settings["openai_reasoning_summary"] == "auto"
        assert settings["openai_prompt_cache_key"] == CACHE_KEY

    def test_no_summary_when_unsupported(self, registry):
        settings = registry.build_settings(_spec(effort=True), "medium")
        assert "openai_reasoning_summary" not in settings

    def test_xhigh_downgraded_for_chat_completions(self, registry):
        spec = _spec(api="chat", effort=True, cache_key=True)
        settings = registry.build_settings(spec, "xhigh", cache_key=CACHE_KEY)
        assert settings["openai_reasoning_effort"] == "high"
        assert "openai_reasoning_summary" not in settings

    def test_xhigh_preserved_for_responses(self, registry):
        settings = registry.build_settings(_spec(effort=True), "xhigh")
        assert settings["openai_reasoning_effort"] == "xhigh"

    def test_effort_dropped_when_unsupported_but_cache_survives(self, registry):
        spec = _spec(effort=False, cache_key=True)
        settings = registry.build_settings(spec, "high", cache_key=CACHE_KEY)
        assert "openai_reasoning_effort" not in settings
        assert settings["openai_prompt_cache_key"] == CACHE_KEY


class TestVerifiedCapabilityDefaults:
    """Live-probed per-deployment truth (2026-09-06): every configured
    OpenAI-family deployment, including the Fireworks-hosted `FW-*`
    passthroughs and the codex proxy, accepts `prompt_cache_key`."""

    @pytest.mark.parametrize(
        "model_id,provider,cache_key",
        [
            ("gpt-5.5", LLMProvider.OPENAI, True),
            ("gpt-5.4-mini", LLMProvider.OPENAI, True),
            ("FW-Kimi-K3", LLMProvider.OPENAI, True),
            ("FW-DeepSeek-V4-Flash-0731", LLMProvider.OPENAI, True),
            ("DeepSeek-V4-Flash-0731", LLMProvider.OPENAI, True),
            ("gpt-6-astra", LLMProvider.CODEX_PROXY, True),
            # Never probed (not configured here) -> stays off.
            ("claude-sonnet-5", LLMProvider.ANTHROPIC, False),
        ],
    )
    def test_defaults(self, model_id, provider, cache_key):
        spec = _build_spec(model_id, model_id, provider, {})
        assert spec.supports_prompt_cache_key is cache_key

    def test_env_override_can_correct_a_flag(self):
        """`MODEL_OVERRIDES` is the per-deployment escape hatch."""
        spec = _build_spec(
            "gpt-5.5",
            "GPT 5.5",
            LLMProvider.OPENAI,
            {"gpt-5.5": {"supports_prompt_cache_key": False}},
        )
        assert spec.supports_prompt_cache_key is False

    def test_a_fireworks_hosted_model_sends_the_key(self, registry):
        spec = _build_spec("FW-Kimi-K3", "Kimi K3", LLMProvider.OPENAI, {})
        settings = registry.build_settings(spec, None, cache_key=CACHE_KEY)
        assert settings == {"openai_prompt_cache_key": CACHE_KEY}


def test_cache_key_is_keyword_only(registry: ModelRegistry) -> None:
    """Guards the call sites: a positional third argument must not silently
    land in `cache_key`."""
    spec: Optional[ModelSpec] = _spec(cache_key=True)
    with pytest.raises(TypeError):
        registry.build_settings(spec, "high", CACHE_KEY)  # type: ignore[misc]
