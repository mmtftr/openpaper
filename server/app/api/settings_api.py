"""App settings: per-call-site model choice (Settings -> Models).

GET  /models         every slot with its built-in default, stored override
                     and effective choice, plus the selectable providers and
                     models.
PUT  /models/{slot}  set a slot's override (all fields null = reset).

Global settings (single-user deployment): no per-user rows.
"""

import logging
from datetime import datetime
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.auth.dependencies import get_required_user
from app.database.crud.model_slot_crud import (
    get_model_slot,
    list_model_slots,
    set_model_slot,
)
from app.database.database import get_db
from app.database.models import ModelSlot
from app.llm.model_registry import LLMProvider, ModelRole, ModelSpec, get_registry
from app.llm.model_slots import (
    SLOT_DEFAULTS,
    ReasoningEffort,
    SlotOverride,
    invalidate_overrides,
    lookup_choice,
    resolve_slot,
)
from app.schemas.user import CurrentUser

logger = logging.getLogger(__name__)

settings_router = APIRouter()


class SlotChoice(BaseModel):
    """A concrete model a slot resolves to."""

    provider: str
    model: str
    model_name: str
    reasoning_effort: Optional[str] = None


class SlotOverrideOut(BaseModel):
    provider: Optional[str] = None
    model: Optional[str] = None
    reasoning_effort: Optional[str] = None
    updated_at: Optional[datetime] = None


class ModelSlotOut(BaseModel):
    slot: str
    description: str
    # "default" or "fast": which of a provider's models the default uses,
    # and what a provider-only override picks.
    role: str
    default: SlotChoice
    override: Optional[SlotOverrideOut] = None
    # Set when the stored override no longer resolves (provider
    # unconfigured, model removed); the slot then uses its default.
    override_error: Optional[str] = None
    effective: SlotChoice


class ProviderOut(BaseModel):
    id: str
    default_model: str
    fast_model: str
    is_default: bool


class SelectableModel(BaseModel):
    id: str
    name: str
    provider: str
    supports_reasoning_effort: bool
    supports_vision: bool


class ModelSettingsOut(BaseModel):
    slots: list[ModelSlotOut]
    providers: list[ProviderOut]
    models: list[SelectableModel]


class ModelSlotUpdate(BaseModel):
    """All null = back to the built-in default."""

    provider: Optional[str] = None
    model: Optional[str] = None
    reasoning_effort: Optional[ReasoningEffort] = None


def _choice(spec: ModelSpec, effort: Optional[str]) -> SlotChoice:
    return SlotChoice(
        provider=spec.provider.value,
        model=spec.id,
        model_name=spec.display_name,
        reasoning_effort=effort,
    )


def _to_override(row: ModelSlot) -> SlotOverride:
    return SlotOverride(
        provider=row.provider,  # type: ignore[arg-type]
        model=row.model,  # type: ignore[arg-type]
        reasoning_effort=row.reasoning_effort,  # type: ignore[arg-type]
    )


def _slot_out(slot: str, row: Optional[ModelSlot], registry: Any) -> ModelSlotOut:
    default = SLOT_DEFAULTS[slot]
    builtin = resolve_slot(slot, registry, overrides={})
    override = _to_override(row) if row is not None else None
    override_error: Optional[str] = None
    if override is not None and override.picks_model:
        try:
            lookup_choice(registry, override.provider, override.model, default.role)
        except ValueError as exc:
            override_error = str(exc)
    effective = resolve_slot(
        slot, registry, overrides={slot: override} if override else {}
    )
    return ModelSlotOut(
        slot=slot,
        description=default.description,
        role=default.role.value,
        default=_choice(builtin.spec, builtin.reasoning_effort),
        override=(
            SlotOverrideOut(
                provider=override.provider,
                model=override.model,
                reasoning_effort=override.reasoning_effort,
                updated_at=row.updated_at,  # type: ignore[union-attr,arg-type]
            )
            if override is not None
            else None
        ),
        override_error=override_error,
        effective=_choice(effective.spec, effective.reasoning_effort),
    )


def _providers(registry: Any) -> list[ProviderOut]:
    out: list[ProviderOut] = []
    for provider in LLMProvider:
        try:
            default_spec = registry.resolve(provider, None, role=ModelRole.DEFAULT)
            fast_spec = registry.resolve(provider, None, role=ModelRole.FAST)
        except ValueError:
            continue  # not configured
        out.append(
            ProviderOut(
                id=provider.value,
                default_model=default_spec.id,
                fast_model=fast_spec.id,
                is_default=provider == registry.default_provider,
            )
        )
    return out


def _selectable_models(registry: Any) -> list[SelectableModel]:
    """The chat picker's models plus each provider's default/fast model."""
    specs: list[ModelSpec] = list(registry.chat_models())
    seen = {(s.provider, s.id) for s in specs}
    for provider in LLMProvider:
        for role in ModelRole:
            try:
                spec = registry.resolve(provider, None, role=role)
            except ValueError:
                break  # not configured
            if (spec.provider, spec.id) not in seen:
                seen.add((spec.provider, spec.id))
                specs.append(spec)
    return [SelectableModel(**spec.to_public_dict()) for spec in specs]


def _require_slot(slot: str) -> None:
    if slot not in SLOT_DEFAULTS:
        raise HTTPException(status_code=404, detail=f"Unknown model slot '{slot}'")


@settings_router.get("/models")
def get_model_settings(
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_required_user),
) -> ModelSettingsOut:
    """Every model slot with its default, override and effective choice."""
    registry = get_registry()
    rows = {str(row.slot): row for row in list_model_slots(db)}
    return ModelSettingsOut(
        slots=[_slot_out(slot, rows.get(slot), registry) for slot in SLOT_DEFAULTS],
        providers=_providers(registry),
        models=_selectable_models(registry),
    )


@settings_router.put("/models/{slot}")
def update_model_slot(
    slot: str,
    body: ModelSlotUpdate,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_required_user),
) -> ModelSlotOut:
    """Set a slot's override; all fields null resets it to the default."""
    _require_slot(slot)
    registry = get_registry()
    default = SLOT_DEFAULTS[slot]
    provider = body.provider or None
    model = body.model or None

    if model and not provider:
        raise HTTPException(
            status_code=400, detail="Pick a provider together with the model."
        )
    try:
        if provider or model:
            spec = lookup_choice(registry, provider, model, default.role)
        else:
            spec = resolve_slot(slot, registry, overrides={}).spec
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if body.reasoning_effort and not spec.supports_reasoning_effort:
        raise HTTPException(
            status_code=400,
            detail=f"Model '{spec.id}' does not support a reasoning effort.",
        )

    set_model_slot(
        db,
        slot,
        provider=provider,
        model=model,
        reasoning_effort=body.reasoning_effort,
    )
    invalidate_overrides()
    return _slot_out(slot, get_model_slot(db, slot), registry)
