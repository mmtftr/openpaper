"""Chat usage that persisted no message row.

The weekly chat-credit meter (`message_crud.get_chat_credits_used_this_week`)
sums the characters of the user's `messages`. Ephemeral endpoints — the
code-viewer quick question — call a paid model but persist nothing, so
without a row here they would pass the quota gate forever. One row per
exchange, charged the way a chat turn is: question + answer characters.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from app.database.models import ChatUsageEvent
from sqlalchemy import func
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


def record_chat_usage(*, user_id: Any, kind: str, chars: int) -> bool:
    """Write one usage row on a FRESH session. Never raises.

    A fresh session because callers run from a stream's `finally`, where the
    request session may already be closed or mid-rollback. False means the
    exchange went unmetered (logged) — never a client-visible failure.
    """
    from app.database.database import SessionLocal

    session = None
    try:
        session = SessionLocal()
        session.add(
            ChatUsageEvent(user_id=user_id, kind=str(kind), chars=max(0, int(chars)))
        )
        session.commit()
        return True
    except Exception as exc:
        logger.error(
            "Failed to record chat usage (%s, %s chars) for user %s: %s",
            kind, chars, user_id, exc,
        )
        try:
            if session is not None:
                session.rollback()
        except Exception:
            pass
        return False
    finally:
        try:
            if session is not None:
                session.close()
        except Exception:
            pass


def chat_usage_chars_between(
    db: Session, *, user_id: Any, start: datetime, end: datetime
) -> int:
    """Characters metered through usage events in `[start, end)`."""
    total = (
        db.query(func.coalesce(func.sum(ChatUsageEvent.chars), 0))
        .filter(
            ChatUsageEvent.user_id == user_id,
            ChatUsageEvent.created_at >= start,
            ChatUsageEvent.created_at < end,
        )
        .scalar()
    )
    return int(total or 0)
