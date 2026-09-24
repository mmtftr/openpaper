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

Overrides live in the `model_slots` table (Settings -> Models, served by
`app.api.settings_api`). They are read through a short TTL cache: gunicorn
runs several worker processes, so a PUT invalidates only its own worker's
cache and the others pick the change up within `OVERRIDE_TTL_SECONDS`. An
override that no longer resolves (provider unconfigured, model removed from
the env lists) is logged and ignored, never fatal.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any, Literal, Mapping, Optional

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
    # The call sends page images: only vision-capable models may fill it.
    requires_vision: bool = False


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
    # The three below reproduce the old `jobs/` pipeline: OCR repair on
    # `OPENAI_OCR_MODEL` (default gpt-5.4-mini = the OpenAI fast model),
    # metadata and highlights on `OPENAI_MODEL`, all on Azure/OpenAI.
    "ingest.ocr_repair": SlotDefault(
        LLMProvider.OPENAI,
        ModelRole.FAST,
        description="Ingest: re-OCR pages whose OCR scored badly (vision)",
        requires_vision=True,
    ),
    "ingest.metadata": SlotDefault(
        LLMProvider.OPENAI,
        ModelRole.DEFAULT,
        description="Ingest: extract title/authors when no record is found",
    ),
    "ingest.highlights": SlotDefault(
        LLMProvider.OPENAI,
        ModelRole.DEFAULT,
        description="Ingest: pick the paper's AI highlights",
    ),
}


ReasoningEffort = Literal["low", "medium", "high", "xhigh"]


@dataclass(frozen=True)
class SlotOverride:
    """A stored override. Any field may be None (= the default's value)."""

    provider: Optional[str] = None
    model: Optional[str] = None
    reasoning_effort: Optional[str] = None

    @property
    def picks_model(self) -> bool:
        return bool(self.provider or self.model)


# -- override cache ----------------------------------------------------------

OVERRIDE_TTL_SECONDS = 15.0

_cache: Optional[tuple[float, dict[str, SlotOverride]]] = None


def load_overrides() -> dict[str, SlotOverride]:
    """Read every stored override from the database (uncached)."""
    from app.database.crud.model_slot_crud import list_model_slots
    from app.database.database import SessionLocal

    with SessionLocal() as db:
        return {
            str(row.slot): SlotOverride(
                provider=row.provider,  # type: ignore[arg-type]
                model=row.model,  # type: ignore[arg-type]
                reasoning_effort=row.reasoning_effort,  # type: ignore[arg-type]
            )
            for row in list_model_slots(db)
        }


def get_overrides() -> dict[str, SlotOverride]:
    """Stored overrides, cached for `OVERRIDE_TTL_SECONDS` per process.

    A failed read keeps the last good value (or none) for another TTL rather
    than failing the model call.
    """
    global _cache
    now = time.monotonic()
    cached = _cache
    if cached is not None and now - cached[0] < OVERRIDE_TTL_SECONDS:
        return cached[1]
    try:
        overrides = load_overrides()
    except Exception:
        logger.warning("Could not read model slot overrides", exc_info=True)
        overrides = cached[1] if cached is not None else {}
    _cache = (now, overrides)
    return overrides


def invalidate_overrides() -> None:
    """Drop this process's cached overrides (after a PUT)."""
    global _cache
    _cache = None


# -- resolution --------------------------------------------------------------


def lookup_choice(
    registry: Any,
    provider: Optional[str],
    model: Optional[str],
    role: ModelRole = ModelRole.DEFAULT,
) -> ModelSpec:
    """Resolve an explicit (provider, model) choice. Raises ValueError.

    - provider + model: a listed model of that provider, or the provider's
      own default/fast model (those need not be in the picker list);
    - provider only: that provider's model for `role`;
    - model only: the first provider listing that id.
    """
    provider_enum: Optional[LLMProvider] = None
    if provider:
        try:
            provider_enum = LLMProvider(provider)
        except ValueError:
            raise ValueError(f"Unknown provider '{provider}'") from None
    if provider_enum is not None and model:
        try:
            return registry.resolve(provider_enum, model)
        except ValueError:
            for other_role in ModelRole:
                role_spec = registry.resolve(provider_enum, None, role=other_role)
                if role_spec.id == model:
                    return role_spec
            raise
    return registry.resolve(provider_enum, model or None, role=role)


def _resolve_default(slot: str, default: SlotDefault, reg: Any) -> ModelSpec:
    try:
        return reg.resolve(default.provider, None, role=default.role)
    except ValueError:
        if default.provider is None:
            raise
        logger.info(
            "Slot %s: provider %s not configured; using the default provider",
            slot,
            default.provider.value,
        )
        return reg.resolve(None, None, role=default.role)


@dataclass(frozen=True)
class ResolvedSlot:
    slot: str
    spec: ModelSpec
    reasoning_effort: str | None


def resolve_slot(
    slot: str,
    registry: Any | None = None,
    overrides: Mapping[str, SlotOverride] | None = None,
) -> ResolvedSlot:
    """The model a slot uses right now.

    `registry` defaults to the process-wide `ModelRegistry`; callers that
    already hold one (chat) pass it so both resolve against the same
    instance. `overrides` defaults to the cached stored overrides (pass `{}`
    for the built-in default). Raises KeyError for an unknown slot and
    ValueError when no provider is configured at all.
    """
    default = SLOT_DEFAULTS[slot]
    reg: ModelRegistry = registry if registry is not None else get_registry()
    override = (get_overrides() if overrides is None else overrides).get(slot)

    spec: Optional[ModelSpec] = None
    if override is not None and override.picks_model:
        try:
            spec = lookup_choice(reg, override.provider, override.model, default.role)
            if default.requires_vision and not spec.supports_vision:
                raise ValueError(f"model '{spec.id}' has no image input")
        except ValueError as exc:
            spec = None
            logger.warning(
                "Slot %s: ignoring override %s/%s (%s); using the default",
                slot,
                override.provider,
                override.model,
                exc,
            )
    if spec is None:
        spec = _resolve_default(slot, default, reg)

    effort = default.reasoning_effort
    if override is not None and override.reasoning_effort:
        effort = override.reasoning_effort
    return ResolvedSlot(slot=slot, spec=spec, reasoning_effort=effort)
