"""Pydantic AI model construction for OpenAI-family endpoints.

The one place that owns TRANSPORT POLICY for every model call (chat, quick
question and the `app.llm.oneshot` calls). Every client is built explicitly
here (never left to the provider to construct) for two reasons:

- `max_retries=0`, so the attempt budget lives only in
  `app.llm.retrying_model.RetryingModel` instead of multiplying with the
  SDK's own retries;
- an httpx timeout with a bounded read gap, so a dead stream fails instead
  of hanging (see `CHAT_HTTP_TIMEOUT`).

Because these clients are per-request and the pydantic-ai provider does not
own them, they must be closed explicitly — `attach_transport_closer` /
`close_model_transport`.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Awaitable, Callable, Optional

import httpx2 as httpx
import openai
from pydantic_ai.models.openai import OpenAIChatModel, OpenAIResponsesModel
from pydantic_ai.profiles.openai import (
    OpenAIJsonSchemaTransformer,
    openai_model_profile,
)
from pydantic_ai.providers.azure import AzureProvider, _openai_compatible_v1_base_url
from pydantic_ai.providers.openai import OpenAIProvider as PaiOpenAIProvider

logger = logging.getLogger(__name__)

# Chat transport policy. `read` is httpx's per-CHUNK gap timeout, not a cap
# on total stream duration, so a long answer is safe — 180s of dead air is
# not. `max_retries=0` because the attempt budget belongs to exactly one
# layer: `app.llm.retrying_model.RetryingModel`. Leaving the SDK's implicit
# 2 retries on would multiply into 9 attempts and blow past any deadline.
CHAT_HTTP_TIMEOUT = httpx.Timeout(connect=5.0, read=180.0, write=60.0, pool=30.0)
CHAT_MAX_RETRIES = 0


_AZURE_UNSUPPORTED_STRICT_KEYWORDS = frozenset(
    {
        "minLength",
        "maxLength",
        "pattern",
        "format",
        "minimum",
        "maximum",
        "multipleOf",
        "patternProperties",
        "unevaluatedProperties",
        "propertyNames",
        "minProperties",
        "maxProperties",
        "unevaluatedItems",
        "contains",
        "minContains",
        "maxContains",
        "minItems",
        "maxItems",
        "uniqueItems",
    }
)


class AzureStrictJsonSchemaTransformer(OpenAIJsonSchemaTransformer):
    """OpenAI strict transformer + Azure's extra prohibitions.

    Azure structured-output rejects validation keywords that vanilla OpenAI
    strict accepts. We strip them at every walked node.
    """

    def transform(self, schema):  # type: ignore[override]
        schema = super().transform(schema)
        for kw in _AZURE_UNSUPPORTED_STRICT_KEYWORDS:
            schema.pop(kw, None)
        return schema


def _is_azure_openai_enabled() -> bool:
    return os.getenv("AZURE_OPENAI", "").strip().lower() in ("1", "true", "yes")


def _openai_provider(
    *,
    api_key: Optional[str],
    base_url: Optional[str],
    max_retries: int,
    timeout: httpx.Timeout,
) -> tuple[Any, bool]:
    """(pydantic-ai provider, needs_azure_strict_schema).

    Every branch constructs the `openai` client EXPLICITLY rather than
    letting the provider build one, because `max_retries` and the httpx
    timeouts are client-level knobs and a provider-built client would
    silently keep the SDK defaults (2 retries, 600s read).
    """
    if _is_azure_openai_enabled() and base_url is None:
        endpoint = os.getenv("AZURE_OPENAI_ENDPOINT")
        if not endpoint:
            raise ValueError(
                "AZURE_OPENAI=true requires AZURE_OPENAI_ENDPOINT to be set"
            )
        # Verified against pinned Pydantic AI 2.44: includes Foundry
        # *.models.ai.azure.com endpoints, which also reject api-version.
        v1_base_url = _openai_compatible_v1_base_url(endpoint)
        if v1_base_url:
            # v1 OpenAI-compatible endpoint: use the plain async client + base_url.
            client = openai.AsyncOpenAI(
                api_key=api_key,
                base_url=v1_base_url,
                max_retries=max_retries,
                timeout=timeout,
            )
            return PaiOpenAIProvider(openai_client=client), True
        azure_client = openai.AsyncAzureOpenAI(
            azure_endpoint=endpoint,
            api_key=api_key,
            api_version=os.getenv("AZURE_OPENAI_API_VERSION", "2025-04-01-preview"),
            max_retries=max_retries,
            timeout=timeout,
        )
        # Both Azure paths need the stricter schema transformer.
        return AzureProvider(openai_client=azure_client), True

    # Standard OpenAI or OpenAI-compatible (codex proxy).
    resolved_base = base_url or os.getenv("OPENAI_BASE_URL")
    if api_key is None and resolved_base and not os.getenv("OPENAI_API_KEY"):
        # Locally-served OpenAI-compatible endpoints often need no key, but
        # the SDK insists on a non-empty one (same workaround pydantic-ai's
        # OpenAIProvider applies when it builds the client itself).
        api_key = "api-key-not-set"
    client = openai.AsyncOpenAI(
        api_key=api_key,
        base_url=resolved_base,
        max_retries=max_retries,
        timeout=timeout,
    )
    return PaiOpenAIProvider(openai_client=client), False


def _azure_strict_profile(model_name: str):
    base = openai_model_profile(model_name)
    return {**base, "json_schema_transformer": AzureStrictJsonSchemaTransformer}


def make_openai_chat_model(
    model_name: str,
    *,
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
    max_retries: int = CHAT_MAX_RETRIES,
    timeout: httpx.Timeout = CHAT_HTTP_TIMEOUT,
) -> OpenAIChatModel:
    """Build a Pydantic AI OpenAIChatModel honoring our Azure / custom-base-url
    rules. Azure path swaps in the strict transformer.
    """
    provider, azure_strict = _openai_provider(
        api_key=api_key,
        base_url=base_url,
        max_retries=max_retries,
        timeout=timeout,
    )
    if azure_strict:
        return OpenAIChatModel(
            model_name, provider=provider, profile=_azure_strict_profile(model_name)
        )
    return OpenAIChatModel(model_name, provider=provider)


def make_openai_responses_model(
    model_name: str,
    *,
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
    max_retries: int = CHAT_MAX_RETRIES,
    timeout: httpx.Timeout = CHAT_HTTP_TIMEOUT,
) -> OpenAIResponsesModel:
    """Build a Pydantic AI OpenAIResponsesModel with the same Azure/custom
    endpoint handling as make_openai_chat_model.
    """
    provider, azure_strict = _openai_provider(
        api_key=api_key,
        base_url=base_url,
        max_retries=max_retries,
        timeout=timeout,
    )
    if azure_strict:
        return OpenAIResponsesModel(
            model_name, provider=provider, profile=_azure_strict_profile(model_name)
        )
    return OpenAIResponsesModel(model_name, provider=provider)


# -- per-request transport lifecycle -------------------------------------

# Attribute holding a model's "close your HTTP client" coroutine factory.
# Reachable through `RetryingModel` too: `WrapperModel.__getattr__` delegates
# unknown attributes to the wrapped model.
MODEL_TRANSPORT_CLOSER = "_openpaper_close_transport"


def attach_transport_closer(model: Any, closer: Callable[[], Awaitable[None]]) -> Any:
    """Tag a model with how to release the client built for it.

    Chat models are built PER REQUEST, and the clients handed to the
    pydantic-ai providers are externally owned — providers only close what
    they created themselves — so without this the sockets linger until the
    garbage collector gets to them.
    """
    setattr(model, MODEL_TRANSPORT_CLOSER, closer)
    return model


async def close_model_transport(model: Optional[Any]) -> None:
    """Release a model's HTTP client. Never raises."""
    if model is None:
        return
    closer = getattr(model, MODEL_TRANSPORT_CLOSER, None)
    if closer is None:
        return
    try:
        await closer()
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("Failed to close model transport: %s", exc)
