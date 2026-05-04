import logging

import logfire

logger = logging.getLogger(__name__)


def track_event(event_name, distinct_id="celery", properties=None):
    """
    Track an event via Logfire.

    Preserves the prior PostHog-backed signature so callers in the jobs
    package don't have to change.
    """
    payload = dict(properties or {})
    payload["distinct_id"] = distinct_id
    logfire.info(event_name, **payload)
