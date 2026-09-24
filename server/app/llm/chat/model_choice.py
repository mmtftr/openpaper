"""Which model answers a request: the client's pick, or a slot's default.

Shared by paper chat (`chat.default` slot) and quick question
(`quick_question` slot). A request that names neither a provider nor a model
gets the slot — model AND its reasoning effort, unless the request set one;
otherwise the registry resolves the named pair.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from app.llm.model_registry import LLMProvider, ModelRegistry, ModelSpec, get_registry
from app.llm.model_slots import resolve_slot


@dataclass
class ModelChoice:
    registry: ModelRegistry
    spec: ModelSpec
    reasoning_effort: Optional[str]


class ModelChoiceError(ValueError):
    """The request named a provider/model that can't be used (a 4xx)."""


def choose_model(
    *,
    slot: str,
    provider: Optional[str],
    model: Optional[str],
    reasoning_effort: Optional[str],
) -> ModelChoice:
    """Resolve the request's model.

    Raises ModelChoiceError with a client-facing message (unknown provider,
    unknown/unavailable model). A registry that fails to build is a server
    error and propagates as whatever it raised.
    """
    registry = get_registry()
    try:
        provider_enum: Optional[LLMProvider] = None
        if provider:
            try:
                provider_enum = LLMProvider(provider.lower())
            except ValueError:
                raise ValueError(f"Unknown provider '{provider}'.")
        if provider_enum is None and not model:
            resolved = resolve_slot(slot, registry)
            return ModelChoice(
                registry=registry,
                spec=resolved.spec,
                reasoning_effort=reasoning_effort or resolved.reasoning_effort,
            )
        return ModelChoice(
            registry=registry,
            spec=registry.resolve(provider_enum, model),
            reasoning_effort=reasoning_effort,
        )
    except ValueError as exc:
        raise ModelChoiceError(str(exc)) from exc
