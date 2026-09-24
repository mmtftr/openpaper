"""Named model slots: which model each LLM call site uses.

Every call site asks for a slot (`chat.title`, `discover`, ...) instead of
picking a provider/model itself. `resolve_slot` is the single place that
turns a slot into a concrete (provider, model, reasoning effort); a later
per-slot override (the Settings -> Models page) plugs in here and nowhere
else.

Defaults reproduce what each call site used before slots existed. They are
expressed as (provider, role) rather than a model id so they keep following
the provider env vars (`DEFAULT_LLM_PROVIDER`, `OPENAI_FAST_MODEL`,
`CODEX_PROXY_MODEL`, ...):

- `provider=None` means "the default provider" (`DEFAULT_LLM_PROVIDER`,
  falling back to a configured one);
- an explicit provider that isn't configured falls back to the default
  provider, as the old per-call-site clients did.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from app.llm.model_registry import (
    LLMProvider,
    ModelRegistry,
    ModelRole,
    ModelSpec,
    get_registry,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SlotDefault:
    """A slot's built-in choice."""

    provider: LLMProvider | None  # None = the default provider
    role: ModelRole
    reasoning_effort: str | None = None  # None = don't send one
    description: str = ""


SLOT_DEFAULTS: dict[str, SlotDefault] = {
    # The picker's default. A model/provider picked in the UI wins.
    "chat.default": SlotDefault(
        None, ModelRole.DEFAULT, description="Paper chat (when no model is picked)"
    ),
    # FAST slots are pinned to OpenAI/Azure: the codex proxy rejects its
    # configured fast model (`gpt-5.4-mini`). Refactor Phase 6 unpins them.
    "chat.reconcile": SlotDefault(
        LLMProvider.OPENAI,
        ModelRole.FAST,
        description="Citation reconcile: map an OCR quote onto the PDF text",
    ),
    "chat.title": SlotDefault(
        LLMProvider.OPENAI, ModelRole.FAST, description="Conversation title"
    ),
    "quick_question": SlotDefault(
        None,
        ModelRole.DEFAULT,
        description="Code quick question (when no model is picked)",
    ),
    "discover": SlotDefault(
        LLMProvider.OPENAI,
        ModelRole.FAST,
        description="Discover: split a question into search queries",
    ),
    "ingest.outline": SlotDefault(
        LLMProvider.OPENAI,
        ModelRole.FAST,
        description="Outline cleanup of OCR headings",
    ),
}


@dataclass(frozen=True)
class ResolvedSlot:
    slot: str
    spec: ModelSpec
    reasoning_effort: str | None


def resolve_slot(slot: str, registry: Any | None = None) -> ResolvedSlot:
    """The model a slot uses right now.

    `registry` defaults to the process-wide `ModelRegistry`; callers that
    already hold one (chat) pass it so both resolve against the same
    instance. Raises KeyError for an unknown slot and ValueError when no
    provider is configured at all.
    """
    default = SLOT_DEFAULTS[slot]
    reg: ModelRegistry = registry if registry is not None else get_registry()
    try:
        spec = reg.resolve(default.provider, None, role=default.role)
    except ValueError:
        if default.provider is None:
            raise
        logger.info(
            "Slot %s: provider %s not configured; using the default provider",
            slot,
            default.provider.value,
        )
        spec = reg.resolve(None, None, role=default.role)
    return ResolvedSlot(slot=slot, spec=spec, reasoning_effort=default.reasoning_effort)
