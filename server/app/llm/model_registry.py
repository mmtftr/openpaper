"""Chat model registry.

One place that knows which chat models exist, what each can do, and how to
build the pydantic-ai `Model` + `ModelSettings` for a call. Replaces the
scattered capability checks that used to live in `paper_pydantic_agent.py`
(`_RESPONSES_API_BROKEN_MODELS`, `_VISION_UNSUPPORTED_MODELS`, `gpt-`
prefix sniffing) and the `ModelType`/provider-default plumbing in `base.py`
for the chat path.

Configuration sources, merged in order (later wins):

1. Provider-family defaults (Responses API for OpenAI-family, chat
   completions for chat-only proxies).
2. The built-in per-model capability table (`_KNOWN_MODEL_CAPS`) for
   deployments with verified quirks.
3. The `MODEL_OVERRIDES` env var: a JSON object mapping model id ->
   partial spec fields, e.g.
   `{"FW-Kimi-K3": {"api": "chat"}, "my-model": {"supports_vision": false}}`.
   This is the dynamic-adjustment knob: capabilities can be corrected per
   deployment without a code change.

Model lists come from the same env vars as before: `OPENAI_MODELS`,
`CODEX_PROXY_MODELS`, `GEMINI_MODELS`, plus the per-provider default/fast
model env vars.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, fields, replace
from enum import Enum
from typing import Any, Dict, List, Literal, Optional, Tuple

from app.llm.provider import LLMProvider, _parse_models_env

logger = logging.getLogger(__name__)


ApiKind = Literal["responses", "chat", "native"]


class ModelRole(str, Enum):
    DEFAULT = "default"
    FAST = "fast"


@dataclass(frozen=True)
class ModelSpec:
    """A chat-capable model and its capabilities.

    `id` is the wire id (for Azure: the deployment name). `api` selects the
    transport for OpenAI-family providers ("native" for Anthropic/Gemini,
    where there is no responses-vs-chat choice).
    """

    id: str
    provider: LLMProvider
    display_name: str
    api: ApiKind = "responses"
    supports_vision: bool = True
    supports_reasoning_effort: bool = False
    supports_reasoning_summaries: bool = False

    def to_public_dict(self) -> Dict[str, Any]:
        """Shape served to the model picker."""
        return {
            "id": self.id,
            "name": self.display_name,
            "provider": self.provider.value,
            "supports_reasoning_effort": self.supports_reasoning_effort,
            "supports_vision": self.supports_vision,
        }


# Verified per-deployment quirks. Keys are wire ids as they appear in the
# model env lists. Values are partial overrides applied over family defaults.
_KNOWN_MODEL_CAPS: Dict[str, Dict[str, Any]] = {
    # Azure's /responses gateway 400s on image input for this deployment even
    # though the model handles images via /chat/completions (verified live).
    "FW-Kimi-K3": {"api": "chat"},
    # Rejects image input on both endpoints and 400s on reasoning_effort.
    "DeepSeek-V4-Flash-0731": {
        "supports_vision": False,
        "supports_reasoning_effort": False,
    },
    "FW-DeepSeek-V4-Flash-0731": {
        "api": "chat",
        "supports_vision": False,
        "supports_reasoning_effort": False,
    },
}

_SPEC_FIELD_NAMES = {f.name for f in fields(ModelSpec)}


def _family_defaults(provider: LLMProvider, model_id: str) -> Dict[str, Any]:
    """Capability defaults derived from the provider family and id shape."""
    is_gpt = model_id.lower().startswith("gpt-")
    if provider == LLMProvider.OPENAI:
        return {
            "api": "responses",
            "supports_reasoning_effort": is_gpt,
            "supports_reasoning_summaries": is_gpt,
        }
    if provider == LLMProvider.CODEX_PROXY:
        # The codex proxy speaks Chat Completions only; reasoning summaries
        # are a Responses-API feature.
        return {
            "api": "chat",
            "supports_reasoning_effort": is_gpt,
            "supports_reasoning_summaries": False,
        }
    if provider in (LLMProvider.GROQ, LLMProvider.CEREBRAS):
        return {"api": "chat"}
    return {"api": "native"}


def _env_overrides() -> Dict[str, Dict[str, Any]]:
    raw = os.getenv("MODEL_OVERRIDES")
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        logger.warning("Ignoring invalid MODEL_OVERRIDES JSON: %s", exc)
        return {}
    if not isinstance(parsed, dict):
        logger.warning("Ignoring MODEL_OVERRIDES: expected a JSON object")
        return {}
    out: Dict[str, Dict[str, Any]] = {}
    for model_id, override in parsed.items():
        if not isinstance(override, dict):
            logger.warning(
                "Ignoring MODEL_OVERRIDES entry for %s: not an object", model_id
            )
            continue
        unknown = set(override) - _SPEC_FIELD_NAMES
        if unknown:
            logger.warning(
                "Ignoring unknown MODEL_OVERRIDES fields for %s: %s",
                model_id,
                sorted(unknown),
            )
        out[model_id] = {k: v for k, v in override.items() if k in _SPEC_FIELD_NAMES}
    return out


def _build_spec(
    model_id: str,
    display_name: str,
    provider: LLMProvider,
    overrides: Dict[str, Dict[str, Any]],
) -> ModelSpec:
    spec = ModelSpec(
        id=model_id,
        provider=provider,
        display_name=display_name,
        **_family_defaults(provider, model_id),
    )
    caps = _KNOWN_MODEL_CAPS.get(model_id)
    if caps:
        spec = replace(spec, **caps)
    env_caps = overrides.get(model_id)
    if env_caps:
        spec = replace(spec, **env_caps)
    return spec


@dataclass(frozen=True)
class _ProviderConfig:
    """Per-provider connection facts the registry needs to build models."""

    api_key: Optional[str]
    base_url: Optional[str]
    default_model: str
    fast_model: str


def _provider_configs() -> Dict[LLMProvider, _ProviderConfig]:
    """Read connection config for every provider whose credentials exist."""
    configs: Dict[LLMProvider, _ProviderConfig] = {}

    if os.getenv("OPENAI_API_KEY"):
        configs[LLMProvider.OPENAI] = _ProviderConfig(
            api_key=os.getenv("OPENAI_API_KEY"),
            base_url=None,  # Azure handling lives in _pai_compat
            default_model=os.getenv("OPENAI_MODEL") or "gpt-5.5",
            fast_model=os.getenv("OPENAI_FAST_MODEL") or "gpt-5.4-mini",
        )
    if os.getenv("CODEX_PROXY_BASE_URL"):
        configs[LLMProvider.CODEX_PROXY] = _ProviderConfig(
            api_key=os.getenv("CODEX_PROXY_API_KEY", "codex-proxy-local"),
            base_url=os.getenv("CODEX_PROXY_BASE_URL"),
            default_model=os.getenv("CODEX_PROXY_MODEL", "gpt-5.5"),
            fast_model=os.getenv("CODEX_PROXY_FAST_MODEL", "gpt-5.4-mini"),
        )
    if os.getenv("ANTHROPIC_API_KEY"):
        configs[LLMProvider.ANTHROPIC] = _ProviderConfig(
            api_key=os.getenv("ANTHROPIC_API_KEY"),
            base_url=None,
            default_model=os.getenv("ANTHROPIC_MODEL") or "claude-sonnet-5",
            fast_model=os.getenv("ANTHROPIC_FAST_MODEL") or "claude-haiku-4-5",
        )
    if os.getenv("GEMINI_API_KEY"):
        configs[LLMProvider.GEMINI] = _ProviderConfig(
            api_key=os.getenv("GEMINI_API_KEY"),
            base_url=None,
            default_model=os.getenv("GEMINI_MODEL") or "gemini-3.7-flash",
            fast_model=os.getenv("GEMINI_FAST_MODEL") or "gemini-3.7-flash",
        )
    return configs


_MODELS_ENV_VAR_BY_PROVIDER: Dict[LLMProvider, Optional[str]] = {
    LLMProvider.OPENAI: "OPENAI_MODELS",
    LLMProvider.CODEX_PROXY: "CODEX_PROXY_MODELS",
    LLMProvider.GEMINI: "GEMINI_MODELS",
    LLMProvider.ANTHROPIC: "ANTHROPIC_MODELS",
}


class ModelRegistry:
    """Registry of chat models across all configured providers."""

    def __init__(
        self,
        specs: List[ModelSpec],
        configs: Dict[LLMProvider, _ProviderConfig],
        default_provider: LLMProvider,
    ) -> None:
        self._specs = specs
        self._configs = configs
        self._by_key: Dict[Tuple[LLMProvider, str], ModelSpec] = {
            (s.provider, s.id): s for s in specs
        }
        self.default_provider = default_provider

    # -- construction ---------------------------------------------------

    @classmethod
    def from_env(cls) -> "ModelRegistry":
        configs = _provider_configs()
        overrides = _env_overrides()
        specs: List[ModelSpec] = []
        for provider, config in configs.items():
            env_var = _MODELS_ENV_VAR_BY_PROVIDER.get(provider)
            options = _parse_models_env(os.getenv(env_var)) if env_var else []
            listed_ids = {o.id for o in options}
            if config.default_model not in listed_ids:
                options.insert(0, _default_option(config.default_model))
            for option in options:
                specs.append(
                    _build_spec(option.id, option.name, provider, overrides)
                )

        env_default = os.getenv("DEFAULT_LLM_PROVIDER", LLMProvider.OPENAI.value)
        try:
            default_provider = LLMProvider(env_default.lower())
        except ValueError:
            default_provider = LLMProvider.OPENAI
        if default_provider not in configs and configs:
            default_provider = next(iter(configs))
        return cls(specs, configs, default_provider)

    # -- lookup ---------------------------------------------------------

    def chat_models(
        self, exclude: Optional[List[LLMProvider]] = None
    ) -> List[ModelSpec]:
        excluded = set(exclude or [])
        return [s for s in self._specs if s.provider not in excluded]

    def resolve(
        self,
        provider: Optional[LLMProvider] = None,
        model_id: Optional[str] = None,
        role: ModelRole = ModelRole.DEFAULT,
    ) -> ModelSpec:
        """Resolve a (provider, model_id) selection to a ModelSpec.

        - Both given: exact lookup (disambiguates ids shared across
          providers).
        - Only model_id: first provider exposing that id, in registry order.
        - Neither: the default provider's model for `role`.
        Raises ValueError when the selection doesn't exist.
        """
        if model_id and provider:
            spec = self._by_key.get((provider, model_id))
            if spec is None:
                raise ValueError(
                    f"Model '{model_id}' is not available under provider "
                    f"'{provider.value}'"
                )
            return spec
        if model_id:
            for spec in self._specs:
                if spec.id == model_id:
                    return spec
            raise ValueError(f"Model '{model_id}' not found in any provider")

        target = provider or self.default_provider
        config = self._configs.get(target)
        if config is None:
            raise ValueError(f"Provider '{target.value}' is not configured")
        wanted = (
            config.fast_model if role == ModelRole.FAST else config.default_model
        )
        spec = self._by_key.get((target, wanted))
        if spec is None:
            # Role model not in the advertised list — still usable; derive.
            spec = _build_spec(wanted, wanted, target, _env_overrides())
        return spec

    # -- model construction ---------------------------------------------

    def build_model(self, spec: ModelSpec) -> Any:
        """Build the pydantic-ai Model for a spec.

        The returned model carries a transport closer (see
        `_pai_compat.attach_transport_closer`): these clients are built per
        request and are not owned by the pydantic-ai provider, so the caller
        must release them once the run's streams are closed.
        """
        from app.llm._pai_compat import attach_transport_closer
        config = self._configs.get(spec.provider)
        if config is None:
            raise ValueError(f"Provider '{spec.provider.value}' is not configured")

        if spec.provider in (
            LLMProvider.OPENAI,
            LLMProvider.CODEX_PROXY,
            LLMProvider.GROQ,
            LLMProvider.CEREBRAS,
        ):
            from app.llm._pai_compat import (
                make_openai_chat_model,
                make_openai_responses_model,
            )

            maker = (
                make_openai_responses_model
                if spec.api == "responses"
                else make_openai_chat_model
            )
            model = maker(
                spec.id, api_key=config.api_key, base_url=config.base_url
            )
            return attach_transport_closer(model, model.client.close)

        if spec.provider == LLMProvider.ANTHROPIC:
            import anthropic
            from pydantic_ai.models.anthropic import AnthropicModel
            from pydantic_ai.providers.anthropic import (
                AnthropicProvider as PaiAnthropicProvider,
            )

            from app.llm._pai_compat import CHAT_HTTP_TIMEOUT, CHAT_MAX_RETRIES

            # Explicit client for the same reason as the OpenAI ones: the
            # attempt budget belongs to `RetryingModel` alone, and the SDK
            # default (2 internal retries) would multiply into 9 HTTP calls.
            client = anthropic.AsyncAnthropic(
                api_key=config.api_key,
                max_retries=CHAT_MAX_RETRIES,
                timeout=CHAT_HTTP_TIMEOUT,
            )
            return attach_transport_closer(
                AnthropicModel(
                    spec.id, provider=PaiAnthropicProvider(anthropic_client=client)
                ),
                client.close,
            )

        if spec.provider == LLMProvider.GEMINI:
            import httpx
            from pydantic_ai.models.google import GoogleModel
            from pydantic_ai.providers.google import GoogleProvider

            from app.llm._pai_compat import CHAT_HTTP_TIMEOUT

            # Without an explicit client pydantic-ai falls back to a 600s
            # timeout, so a hung stream would burn 3 x 600s under the retry
            # wrapper. google-genai does no retrying of its own by default
            # (`retry_args(None)` = stop_after_attempt(1)), so there is no
            # SDK-level budget to disable here.
            http_client = httpx.AsyncClient(timeout=CHAT_HTTP_TIMEOUT)
            return attach_transport_closer(
                GoogleModel(
                    spec.id,
                    provider=GoogleProvider(
                        api_key=config.api_key, http_client=http_client
                    ),
                ),
                http_client.aclose,
            )

        raise ValueError(f"Unsupported provider: {spec.provider.value}")

    def build_settings(
        self, spec: ModelSpec, reasoning_effort: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """ModelSettings for a call, honoring the spec's capabilities.

        An unsupported reasoning_effort is dropped silently (the picker is
        capability-gated client-side; a stale selection shouldn't 400).
        """
        if not reasoning_effort or not spec.supports_reasoning_effort:
            return None
        from pydantic_ai.models.openai import (
            OpenAIChatModelSettings,
            OpenAIResponsesModelSettings,
        )

        if spec.api == "responses":
            settings = OpenAIResponsesModelSettings(
                openai_reasoning_effort=str(reasoning_effort)
            )
            if spec.supports_reasoning_summaries:
                settings["openai_reasoning_summary"] = "auto"
            return settings
        # Chat Completions has no `xhigh` tier — map it down.
        effort = "high" if reasoning_effort == "xhigh" else str(reasoning_effort)
        return OpenAIChatModelSettings(openai_reasoning_effort=effort)


def _default_option(model_id: str):
    from app.llm.provider import ModelOption

    return ModelOption(id=model_id, name=model_id)


_registry: Optional[ModelRegistry] = None


def get_registry(refresh: bool = False) -> ModelRegistry:
    """Process-wide registry instance. `refresh=True` rebuilds from env."""
    global _registry
    if _registry is None or refresh:
        _registry = ModelRegistry.from_env()
    return _registry
