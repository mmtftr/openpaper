"""Persistence for per-call-site model overrides (`model_slots`)."""

from typing import Optional

from sqlalchemy.orm import Session

from app.database.models import ModelSlot


def list_model_slots(db: Session) -> list[ModelSlot]:
    return db.query(ModelSlot).all()


def get_model_slot(db: Session, slot: str) -> Optional[ModelSlot]:
    return db.get(ModelSlot, slot)


def set_model_slot(
    db: Session,
    slot: str,
    *,
    provider: Optional[str],
    model: Optional[str],
    reasoning_effort: Optional[str],
) -> Optional[ModelSlot]:
    """Upsert a slot's override; all-None deletes it (back to the default)."""
    row = db.get(ModelSlot, slot)
    if provider is None and model is None and reasoning_effort is None:
        if row is not None:
            db.delete(row)
            db.commit()
        return None
    if row is None:
        row = ModelSlot(slot=slot)
        db.add(row)
    row.provider = provider
    row.model = model
    row.reasoning_effort = reasoning_effort
    db.commit()
    db.refresh(row)
    return row
