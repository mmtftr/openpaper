"""One error classifier for every retry decision in the server.

`classify(exc)` sorts any failure — an HTTP error from httpx, an OpenAI /
Anthropic / google-genai SDK error, a pydantic-ai `ModelHTTPError`, a dropped
connection, one of our own `ConfigError`/`PermanentError` — into:

- `temporary`: re-issuing the same request could plausibly succeed
  (5xx, 408/409, connection reset, timeout, empty model stream);
- `rate_limited`: 429 — wait (ideally the provider's `Retry-After`) and retry;
- `permanent`: the same request will fail the same way (400/404/422, a
  content filter, a malformed response, a corrupt input);
- `config`: something the owner must fix before a retry can help (missing or
  rejected API key, unconfigured model).

`Classified.retry_after` carries the provider's own backoff hint whatever the
kind. The chat transport retry (`app.llm.retrying_model`) and the ingest
worker both decide from this one function.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from enum import StrEnum
from typing import Any, Optional

import httpx
import httpx2
from pydantic_ai.exceptions import (
    ModelAPIError,
    ModelHTTPError,
    UnexpectedModelBehavior,
)


class ErrorKind(StrEnum):
    TEMPORARY = "temporary"
    RATE_LIMITED = "rate_limited"
    PERMANENT = "permanent"
    CONFIG = "config"

    @property
    def retryable(self) -> bool:
        return self in (ErrorKind.TEMPORARY, ErrorKind.RATE_LIMITED)


@dataclass(frozen=True)
class Classified:
    kind: ErrorKind
    message: str
    retry_after: Optional[float] = None  # seconds, the provider's own hint

    @property
    def retryable(self) -> bool:
        return self.kind.retryable


class ConfigError(Exception):
    """Missing/invalid configuration (API key, model). Fix it, then retry."""


class PermanentError(Exception):
    """A failure a retry cannot fix (corrupt PDF, record not found, ...)."""


class TemporaryError(Exception):
    """A failure worth retrying that isn't an HTTP/transport error."""

    def __init__(self, message: str, retry_after: Optional[float] = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


# Every provider raises `UnexpectedModelBehavior` with this exact text when a
# stream closes without producing anything, which is what an overloaded
# deployment does. The one `UnexpectedModelBehavior` worth retrying.
EMPTY_STREAM_MESSAGE = "Streamed response ended without content or tool calls"

RETRYABLE_STATUS_CODES = frozenset({408, 409, 429, 500, 502, 503, 504})
# Deterministic failures — a retry burns latency and money for the same
# answer. 400 in particular is where content filters land.
NON_RETRYABLE_STATUS_CODES = frozenset({400, 401, 403, 404, 422})
# Credentials rejected: the owner has to fix a key, not wait.
CONFIG_STATUS_CODES = frozenset({401, 403})

# Default cap on `Classified.message` (it is stored and shown in the UI).
MAX_MESSAGE_CHARS = 500


def status_code(error: BaseException) -> Optional[int]:
    """HTTP status carried by `error`, if it is an HTTP failure at all."""
    if isinstance(error, ModelHTTPError):
        return error.status_code
    response = getattr(error, "response", None)
    if isinstance(response, (httpx.Response, httpx2.Response)):
        return response.status_code
    status = getattr(error, "status_code", None)
    if isinstance(status, int):
        return status
    # google-genai's `APIError.code`. GoogleModel peeks the first chunk
    # outside pydantic-ai's error mapper, so first-chunk failures surface as
    # the raw SDK error with no `status_code` at all.
    code = getattr(error, "code", None)
    if isinstance(code, int):
        return code
    return None


def _header_bag(error: BaseException) -> Optional[Any]:
    """Best-effort headers for `error`, following up to two `__cause__` hops.

    Pydantic AI 2.x preserves headers on `ModelHTTPError`; raw SDK errors
    and connection failures may still carry them on their response/cause.
    """
    seen = 0
    candidate: Optional[BaseException] = error
    while candidate is not None and seen < 3:
        headers = getattr(candidate, "headers", None)
        if headers is not None and hasattr(headers, "get"):
            return headers
        response = getattr(candidate, "response", None)
        headers = getattr(response, "headers", None)
        if headers is not None and hasattr(headers, "get"):
            return headers
        candidate = candidate.__cause__
        seen += 1
    return None


def _positive_float(raw: Any) -> Optional[float]:
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def _http_date_delay(raw: Any) -> Optional[float]:
    """Seconds until an HTTP-date `Retry-After`, or None.

    RFC 9110 allows either a delta in seconds or an absolute date; providers
    do send the date form. A date already in the past means "retry now".
    """
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        when = parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        return None
    if when is None:
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    delay = (when - datetime.now(timezone.utc)).total_seconds()
    return delay if delay > 0 else None


def retry_after_seconds(error: BaseException) -> Optional[float]:
    """The provider's own backoff hint, in seconds, if it gave one.

    Handles `Retry-After` in both RFC 9110 forms (delta-seconds and
    HTTP-date), `retry-after-ms`, and the `retry_after` / `retry_after_ms`
    fields some JSON error bodies carry.
    """
    if isinstance(error, TemporaryError) and error.retry_after is not None:
        return error.retry_after
    headers = _header_bag(error)
    if headers is not None:
        for name, scale in (
            ("retry-after-ms", 0.001),
            ("x-ratelimit-reset-after", 1.0),
            ("retry-after", 1.0),
        ):
            value = _positive_float(headers.get(name))
            if value is not None:
                return value * scale
        date_delay = _http_date_delay(headers.get("retry-after"))
        if date_delay is not None:
            return date_delay

    body = getattr(error, "body", None)
    if isinstance(body, dict):
        nested = body.get("error")
        for source in (body, nested if isinstance(nested, dict) else {}):
            value = _positive_float(source.get("retry_after_ms"))
            if value is not None:
                return value * 0.001
            value = _positive_float(source.get("retry_after"))
            if value is not None:
                return value
    return None


def short_error_text(error: BaseException, limit: int = MAX_MESSAGE_CHARS) -> str:
    """A compact, single-line description: `Type (status): detail`."""
    status = status_code(error)
    prefix = type(error).__name__
    if status is not None:
        prefix = f"{prefix} ({status})"
    detail = str(error).strip().replace("\n", " ")
    text = f"{prefix}: {detail}" if detail else prefix
    return _truncate(text, limit)


def _truncate(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _kind(error: BaseException) -> ErrorKind:
    # Our own markers first: they carry an explicit decision.
    if isinstance(error, ConfigError):
        return ErrorKind.CONFIG
    if isinstance(error, PermanentError):
        return ErrorKind.PERMANENT
    if isinstance(error, TemporaryError):
        return ErrorKind.TEMPORARY
    if isinstance(error, UnexpectedModelBehavior):
        # NARROW on purpose: most `UnexpectedModelBehavior`s (exceeded tool
        # retries, unparseable output) are deterministic.
        if EMPTY_STREAM_MESSAGE in str(error):
            return ErrorKind.TEMPORARY
        return ErrorKind.PERMANENT
    status = status_code(error)
    if status is not None:
        if status == 429:
            return ErrorKind.RATE_LIMITED
        if status in CONFIG_STATUS_CODES:
            return ErrorKind.CONFIG
        if status in NON_RETRYABLE_STATUS_CODES:
            return ErrorKind.PERMANENT
        # Everything 5xx is transient (covers 529 "overloaded" and other
        # provider-specific server codes), plus the explicit list.
        if status in RETRYABLE_STATUS_CODES or status >= 500:
            return ErrorKind.TEMPORARY
        return ErrorKind.PERMANENT
    # Connection resets, DNS failures, read timeouts — httpx raises these
    # directly mid-stream, pydantic-ai wraps them in ModelAPIError at
    # request time.
    if isinstance(error, (httpx.TransportError, httpx2.TransportError)):
        return ErrorKind.TEMPORARY
    if isinstance(error, (ConnectionError, TimeoutError, asyncio.TimeoutError)):
        return ErrorKind.TEMPORARY
    if isinstance(error, ModelAPIError):
        return ErrorKind.TEMPORARY
    return ErrorKind.PERMANENT


def classify(error: BaseException, max_chars: int = MAX_MESSAGE_CHARS) -> Classified:
    """Sort `error` into temporary / rate_limited / permanent / config.

    Unknown exception types are `permanent`: a bug in our code is not fixed
    by running it again, and it should surface rather than loop.
    """
    kind = _kind(error)
    if isinstance(error, (ConfigError, PermanentError, TemporaryError)):
        # Written by us for the owner to read: no type-name prefix.
        message = _truncate(str(error).strip() or type(error).__name__, max_chars)
    else:
        message = short_error_text(error, max_chars)
    return Classified(
        kind=kind, message=message, retry_after=retry_after_seconds(error)
    )


def is_retryable(error: BaseException) -> bool:
    """Whether re-issuing the identical request could plausibly succeed."""
    return _kind(error).retryable
