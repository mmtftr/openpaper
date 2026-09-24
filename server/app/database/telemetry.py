import logging

import logfire

logger = logging.getLogger(__name__)


def track_event(event_name, properties=None, user_id=None):
    """Track an event via Logfire (fire-and-forget over OTLP)."""
    payload = dict(properties or {})
    if user_id is not None:
        payload["user_id"] = str(user_id)

    # ``logfire.info`` accepts arbitrary kwargs as structured attributes; the
    # event name is the message template so it shows up as a searchable label.
    logfire.info(event_name, **payload)
