import logging
import os
from typing import Optional

import logfire
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

DEBUG = os.getenv("DEBUG", "False").lower() in ("true", "1", "t")


def track_event(
    event_name,
    properties=None,
    user_id=None,
    sync=False,
    db: Optional[Session] = None,
):
    """
    Track an event via Logfire.

    Signature is preserved from the prior PostHog-backed helper so existing
    callers don't have to change. ``sync`` and ``db`` are accepted for
    backwards compatibility but no longer have an effect — Logfire spans are
    fire-and-forget over OTLP and don't need a DB session for enrichment.
    """
    payload = dict(properties or {})
    if user_id is not None:
        payload["user_id"] = str(user_id)

    # ``logfire.info`` accepts arbitrary kwargs as structured attributes; the
    # event name is the message template so it shows up as a searchable label.
    logfire.info(event_name, **payload)
