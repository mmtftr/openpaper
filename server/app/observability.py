"""Logfire setup shared by the API server and the ingest worker.

Request bodies, prompts and model outputs are captured on purpose: the owner
reviews chat and ingest transcripts in Logfire.
"""

import logging

import logfire

logger = logging.getLogger(__name__)


def _safe_instrument(label: str, instrument) -> None:
    """Best-effort observability hookup.

    Each ``logfire.instrument_*`` pulls an optional OpenTelemetry integration
    whose version must line up with logfire's. A mismatch (e.g. an unpinned
    rebuild floats logfire ahead of an integration package) raises at import
    time — but instrumentation is observability, not core function, so it must
    not take down server boot. Log and continue.
    """
    try:
        instrument()
    except Exception as exc:  # noqa: BLE001 - telemetry must never break boot
        logger.warning("Skipping logfire instrumentation %s: %s", label, exc)


def configure_logfire(service_name: str) -> None:
    """Configure Logfire (sends only when a token is set) and instrument the
    pydantic, OpenAI SDK and httpx layers."""
    logfire.configure(service_name=service_name, send_to_logfire="if-token-present")
    _safe_instrument("pydantic", lambda: logfire.instrument_pydantic(record="failure"))
    _safe_instrument("openai", logfire.instrument_openai)
    _safe_instrument("httpx", lambda: logfire.instrument_httpx(capture_all=True))


def instrument_fastapi(app) -> None:
    _safe_instrument(
        "fastapi", lambda: logfire.instrument_fastapi(app, capture_headers=True)
    )
