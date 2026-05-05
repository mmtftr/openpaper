"""Pydantic AI compatibility shims.

Lives next to provider.py so the OpenAI/Anthropic/Gemini providers can opt
into Pydantic AI internally without changing their public interface.
"""

from __future__ import annotations

import asyncio
import os
import queue
import threading
from dataclasses import replace
from typing import AsyncIterator, Awaitable, Callable, Iterator, Optional, TypeVar

import openai
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.profiles.openai import (
    OpenAIJsonSchemaTransformer,
    openai_model_profile,
)
from pydantic_ai.providers.azure import AzureProvider
from pydantic_ai.providers.openai import OpenAIProvider as PaiOpenAIProvider


_AZURE_UNSUPPORTED_STRICT_KEYWORDS = frozenset(
    {
        "minLength", "maxLength", "pattern", "format",
        "minimum", "maximum", "multipleOf",
        "patternProperties", "unevaluatedProperties", "propertyNames",
        "minProperties", "maxProperties",
        "unevaluatedItems", "contains", "minContains", "maxContains",
        "minItems", "maxItems", "uniqueItems",
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


def _is_v1_azure_endpoint(url: Optional[str]) -> bool:
    return bool(url) and url.rstrip("/").endswith("/openai/v1")


def make_openai_chat_model(
    model_name: str,
    *,
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
) -> OpenAIChatModel:
    """Build a Pydantic AI OpenAIChatModel honoring our Azure / custom-base-url
    rules. Azure path swaps in the strict transformer.
    """
    if _is_azure_openai_enabled() and base_url is None:
        endpoint = os.getenv("AZURE_OPENAI_ENDPOINT")
        if not endpoint:
            raise ValueError(
                "AZURE_OPENAI=true requires AZURE_OPENAI_ENDPOINT to be set"
            )
        if _is_v1_azure_endpoint(endpoint):
            # v1 OpenAI-compatible endpoint: use the plain async client + base_url.
            client = openai.AsyncOpenAI(api_key=api_key, base_url=endpoint)
            provider = PaiOpenAIProvider(openai_client=client)
        else:
            provider = AzureProvider(
                azure_endpoint=endpoint,
                api_key=api_key,
                api_version=os.getenv("AZURE_OPENAI_API_VERSION", "2025-04-01-preview"),
            )
        # Both Azure paths need the stricter schema transformer.
        base = openai_model_profile(model_name)
        profile = replace(base, json_schema_transformer=AzureStrictJsonSchemaTransformer)
        return OpenAIChatModel(model_name, provider=provider, profile=profile)

    # Standard OpenAI or OpenAI-compatible (Groq, Cerebras).
    resolved_base = base_url or os.getenv("OPENAI_BASE_URL")
    if resolved_base:
        client = openai.AsyncOpenAI(api_key=api_key, base_url=resolved_base)
        provider = PaiOpenAIProvider(openai_client=client)
    else:
        provider = PaiOpenAIProvider(api_key=api_key)
    return OpenAIChatModel(model_name, provider=provider)


_T = TypeVar("_T")


def run_async(coro: Awaitable[_T]) -> _T:
    """Run a coroutine to completion from a sync caller, even one nested
    inside a running event loop. Always uses a fresh background thread+loop
    so we never collide with an outer loop.
    """
    box: dict = {}

    def runner() -> None:
        loop = asyncio.new_event_loop()
        try:
            box["value"] = loop.run_until_complete(coro)
        except BaseException as exc:
            box["error"] = exc
        finally:
            loop.close()

    t = threading.Thread(target=runner, daemon=True)
    t.start()
    t.join()
    if "error" in box:
        raise box["error"]
    return box["value"]


def async_iter_to_sync(
    factory: Callable[[], AsyncIterator[_T]],
) -> Iterator[_T]:
    """Bridge an async iterator to a sync one. Runs the async iteration on a
    background thread+loop and pushes items onto a queue.
    """
    q: "queue.Queue[object]" = queue.Queue()
    sentinel = object()

    async def driver() -> None:
        try:
            async for item in factory():
                q.put(item)
        except BaseException as exc:
            q.put(("__error__", exc))
        finally:
            q.put(sentinel)

    def runner() -> None:
        asyncio.run(driver())

    t = threading.Thread(target=runner, daemon=True)
    t.start()
    while True:
        item = q.get()
        if item is sentinel:
            return
        if isinstance(item, tuple) and len(item) == 2 and item[0] == "__error__":
            raise item[1]  # type: ignore[misc]
        yield item  # type: ignore[misc]
