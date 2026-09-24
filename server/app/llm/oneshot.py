"""One-shot model calls (no tools, no history) through pydantic-ai.

`complete(slot, prompt, output_type=...)` resolves the slot's model, builds
it with the chat path's own machinery (`ModelRegistry.build_model`, the
`_pai_compat` transport policy, `RetryingModel`), runs a single
`Agent.run`, and closes the per-request client.

Structured output (`output_type` a pydantic model) uses pydantic-ai's
default TOOL output (a `final_result` tool call) on purpose: the codex proxy
ignores `response_format` json_schema, so native structured output would
come back as prose there.
"""

from __future__ import annotations

import asyncio
import contextvars
import logging
import threading
import time
from collections.abc import Awaitable
from dataclasses import replace
from typing import Any, overload

from pydantic_ai import Agent

from app.database.telemetry import track_event
from app.llm._pai_compat import close_model_transport
from app.llm.model_registry import get_registry
from app.llm.model_slots import resolve_slot
from app.llm.retrying_model import RetryingModel

logger = logging.getLogger(__name__)


@overload
async def complete(
    slot: str, prompt: str, *, instructions: str | None = None
) -> str: ...


@overload
async def complete[T](
    slot: str,
    prompt: str,
    *,
    output_type: type[T],
    instructions: str | None = None,
) -> T: ...


async def complete(
    slot: str,
    prompt: str,
    *,
    output_type: Any = str,
    instructions: str | None = None,
) -> Any:
    """Run `prompt` once on the slot's model and return the output.

    Transient provider failures are retried by `RetryingModel`; anything
    else (including output that still fails validation after pydantic-ai's
    one output retry) raises.
    """
    registry = get_registry()
    resolved = resolve_slot(slot, registry)
    spec = resolved.spec
    if spec.api == "responses":
        # The pre-pydantic-ai client sent every one-shot call over Chat
        # Completions; keep that wire API for these call sites.
        spec = replace(spec, api="chat")

    model = registry.build_model(spec)
    start = time.monotonic()
    try:
        agent = Agent(
            RetryingModel(model),
            output_type=output_type,
            instructions=instructions or None,
            model_settings=registry.build_settings(spec, resolved.reasoning_effort),
        )
        result = await agent.run(prompt)
    except Exception as exc:
        duration_ms = (time.monotonic() - start) * 1000
        track_event(
            "llm_generate_content_error",
            {
                "slot": slot,
                "model": spec.id,
                "provider": spec.provider.value,
                "duration_ms": duration_ms,
                "error": str(exc),
            },
        )
        logger.error(
            "Error generating content for %s with %s/%s: %s",
            slot,
            spec.provider.value,
            spec.id,
            exc,
        )
        raise
    finally:
        await close_model_transport(model)

    duration_ms = (time.monotonic() - start) * 1000
    track_event(
        "llm_generate_content",
        {
            "slot": slot,
            "model": spec.id,
            "provider": spec.provider.value,
            "duration_ms": duration_ms,
        },
    )
    logger.info(
        "Generated content for %s using %s/%s in %.2fms",
        slot,
        spec.provider.value,
        spec.id,
        duration_ms,
    )
    return result.output


@overload
def complete_sync(
    slot: str, prompt: str, *, instructions: str | None = None
) -> str: ...


@overload
def complete_sync[T](
    slot: str,
    prompt: str,
    *,
    output_type: type[T],
    instructions: str | None = None,
) -> T: ...


def complete_sync(
    slot: str,
    prompt: str,
    *,
    output_type: Any = str,
    instructions: str | None = None,
) -> Any:
    """`complete` for sync callers (conversation title, outline cleanup)."""
    return _run_sync(
        complete(slot, prompt, output_type=output_type, instructions=instructions)
    )


def _run_sync[T](coro: Awaitable[T]) -> T:
    """Run a coroutine to completion from sync code, even from inside a
    running event loop: always on a fresh thread + loop, with the caller's
    contextvars (so Logfire spans stay parented)."""
    box: dict = {}
    ctx = contextvars.copy_context()

    def runner() -> None:
        loop = asyncio.new_event_loop()
        try:
            box["value"] = loop.run_until_complete(coro)
        except BaseException as exc:  # noqa: BLE001 - re-raised on the caller thread
            box["error"] = exc
        finally:
            loop.close()

    thread = threading.Thread(target=lambda: ctx.run(runner), daemon=True)
    thread.start()
    thread.join()
    if "error" in box:
        raise box["error"]
    return box["value"]
