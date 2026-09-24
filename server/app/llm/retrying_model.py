"""Transport-level retry wrapper for pydantic-ai chat models.

Chat turns die on transient provider failures (429s from a low-capacity
Azure deployment, 502/503 from a proxy, a dropped connection). Pydantic-ai
retries nothing at this level: `Agent(retries=...)` only covers tool
*validation*, and the OpenAI SDK's own `max_retries` is disabled by
`_pai_compat` so the attempt budget lives in exactly one place — here.

`RetryingModel` wraps any `Model` and re-issues the request up to
`MAX_ATTEMPTS` times with exponential backoff. Retrying at the MODEL layer
is what makes this safe: the agent graph re-sends the full message history
(including already-executed tool calls and their returns), so a retry
re-asks the provider, it does not re-run tools.

Streaming caveat (mirrors `pydantic_ai.models.fallback.FallbackModel`):
`request_stream` is an async context manager, so only failures raised while
ENTERING it can be retried. Once the stream is yielded, a consumer-side
failure has already been observed by the caller and must propagate.

Entering covers more than connecting: pydantic-ai peeks the first event
inside `__aenter__`, so connection setup, response headers, and the wait for
the first chunk (including a slow model's whole time-to-first-token) are all
inside the retry boundary. The accepted gap is narrow and deliberate: a
failure after the provider has emitted a control-level event but before any
visible content is NOT retried, because from here it is indistinguishable
from a failure that already streamed text. Closing it would mean proxying
and replaying the stream — much more machinery than the case is worth.

An optional `on_retry` callback lets the caller surface retry state to the
UI (see `app.llm.chat.runtime`); callback failures are swallowed so a
broken observer can never break a chat turn.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import (
    Any,
    AsyncIterator,
    Awaitable,
    Callable,
    Dict,
    List,
    Literal,
    Optional,
    Sequence,
    Tuple,
)

import httpx
import httpx2
from pydantic_ai.exceptions import (
    ModelAPIError,
    ModelHTTPError,
    UnexpectedModelBehavior,
)
from pydantic_ai.messages import ModelMessage, ModelResponse
from pydantic_ai.models import Model, ModelRequestParameters, StreamedResponse
from pydantic_ai.models.wrapper import WrapperModel
from pydantic_ai.settings import ModelSettings

logger = logging.getLogger(__name__)

# Total attempts, including the first one. 3 = original + 2 retries.
MAX_ATTEMPTS = 3
# Wait before attempt 2, then before attempt 3.
BACKOFF_SECONDS: Tuple[float, ...] = (1.0, 2.0)
# Ceiling for a provider-supplied `Retry-After`: a chat request the user is
# staring at must not sit idle for a minute.
MAX_BACKOFF_SECONDS = 15.0
# Cap on the error text handed to the callback (it goes over the wire).
MAX_ERROR_CHARS = 200

# Transient by nature: overload, rate limit, gateway hiccup, request timeout.
# The one `UnexpectedModelBehavior` worth retrying: every provider raises it
# with this exact text when a stream closes without producing anything, which
# is what an overloaded deployment does.
EMPTY_STREAM_MESSAGE = "Streamed response ended without content or tool calls"

RETRYABLE_STATUS_CODES = frozenset({408, 409, 429, 500, 502, 503, 504})
# Deterministic failures — a retry burns latency and money for the same
# answer. 400 in particular is where content filters land.
NON_RETRYABLE_STATUS_CODES = frozenset({400, 401, 403, 404, 422})


@dataclass(frozen=True)
class RetryStatus:
    """One retry-lifecycle notification handed to `on_retry`."""

    state: Literal["retrying", "recovered"]
    attempt: Optional[int] = None
    max_attempts: int = MAX_ATTEMPTS
    delay_ms: Optional[int] = None
    error: Optional[str] = None


OnRetry = Callable[[RetryStatus], Awaitable[None]]


def _status_code(error: BaseException) -> Optional[int]:
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


def is_retryable(error: BaseException) -> bool:
    """Whether re-issuing the identical request could plausibly succeed."""
    if isinstance(error, UnexpectedModelBehavior):
        # NARROW on purpose. Low-capacity deployments do hang up with an
        # empty stream — squarely the transient class this wrapper exists
        # for — but most `UnexpectedModelBehavior`s (exceeded tool retries,
        # unparseable output) are deterministic and would just cost 3x.
        return EMPTY_STREAM_MESSAGE in str(error)
    status = _status_code(error)
    if status is not None:
        if status in NON_RETRYABLE_STATUS_CODES:
            return False
        # Everything 5xx is treated as transient (covers 529 "overloaded"
        # and other provider-specific server codes), plus the explicit list.
        return status in RETRYABLE_STATUS_CODES or status >= 500
    # Connection resets, DNS failures, read timeouts — httpx raises these
    # directly when they happen mid-stream-consumption, and pydantic-ai
    # wraps them in ModelAPIError when they happen at request time.
    if isinstance(error, (httpx.TransportError, httpx2.TransportError)):
        return True
    if isinstance(error, (ConnectionError, TimeoutError, asyncio.TimeoutError)):
        return True
    if isinstance(error, ModelAPIError):
        return True
    return False


def _header_bag(error: BaseException) -> Optional[Any]:
    """Best-effort headers for `error`, following one `__cause__` hop.

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


def short_error_text(error: BaseException) -> str:
    """A compact, wire-safe description of a failure."""
    status = _status_code(error)
    prefix = type(error).__name__
    if status is not None:
        prefix = f"{prefix} ({status})"
    detail = str(error).strip().replace("\n", " ")
    text = f"{prefix}: {detail}" if detail else prefix
    if len(text) > MAX_ERROR_CHARS:
        text = text[: MAX_ERROR_CHARS - 1] + "…"
    return text


class RetryingModel(WrapperModel):
    """A `Model` that retries transient provider failures.

    Args:
        wrapped: the model to delegate to.
        on_retry: async callback notified before each backoff sleep and once
            after a previously-failed request succeeds. Never awaited for
            the happy path (no failure => no callback at all).
        max_attempts: total attempts including the first.
        backoff_seconds: waits before attempts 2, 3, ... The last entry is
            reused if `max_attempts` exceeds its length.
        sleep: injection seam for tests.
    """

    def __init__(
        self,
        wrapped: Model,
        *,
        on_retry: Optional[OnRetry] = None,
        max_attempts: int = MAX_ATTEMPTS,
        backoff_seconds: Sequence[float] = BACKOFF_SECONDS,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        super().__init__(wrapped)
        self._on_retry = on_retry
        self._max_attempts = max(1, int(max_attempts))
        self._backoff: Tuple[float, ...] = tuple(backoff_seconds) or (1.0,)
        self._sleep = sleep

    # -- attempt policy --------------------------------------------------

    def _delay_for(self, error: BaseException, attempt: int) -> Optional[float]:
        """Seconds to wait before attempt `attempt + 1`, or None to give up."""
        if attempt >= self._max_attempts:
            return None
        if not is_retryable(error):
            return None
        base = self._backoff[min(attempt - 1, len(self._backoff) - 1)]
        hinted = retry_after_seconds(error)
        delay = max(base, hinted) if hinted is not None else base
        return min(delay, MAX_BACKOFF_SECONDS)

    async def _notify(self, status: RetryStatus) -> None:
        if self._on_retry is None:
            return
        try:
            await self._on_retry(status)
        except Exception as exc:
            # `Exception`, never `BaseException`: an observer must not be
            # able to break the run, but a CancelledError raised through it
            # is TEARDOWN talking (the callback may block on a queue) and
            # swallowing that would strand the retry loop.
            logger.warning("retry-status callback failed (non-fatal): %s", exc)

    async def _before_retry(
        self, error: BaseException, attempt: int, delay: float
    ) -> None:
        logger.warning(
            "Model request failed (attempt %s/%s), retrying in %.1fs: %s",
            attempt,
            self._max_attempts,
            delay,
            short_error_text(error),
        )
        await self._notify(
            RetryStatus(
                state="retrying",
                attempt=attempt + 1,
                max_attempts=self._max_attempts,
                delay_ms=int(delay * 1000),
                error=short_error_text(error),
            )
        )
        await self._sleep(delay)

    async def _after_recovery(self, attempt: int) -> None:
        logger.info("Model request recovered on attempt %s", attempt)
        await self._notify(
            RetryStatus(state="recovered", max_attempts=self._max_attempts)
        )

    # -- Model API -------------------------------------------------------

    async def request(
        self,
        messages: List[ModelMessage],
        model_settings: Optional[ModelSettings],
        model_request_parameters: ModelRequestParameters,
    ) -> ModelResponse:
        attempt = 1
        while True:
            try:
                response = await self.wrapped.request(
                    messages, model_settings, model_request_parameters
                )
            except Exception as exc:
                delay = self._delay_for(exc, attempt)
                if delay is None:
                    raise
                await self._before_retry(exc, attempt, delay)
                attempt += 1
                continue
            if attempt > 1:
                await self._after_recovery(attempt)
            return response

    @asynccontextmanager
    async def request_stream(
        self,
        messages: List[ModelMessage],
        model_settings: Optional[ModelSettings],
        model_request_parameters: ModelRequestParameters,
        run_context: Any | None = None,
    ) -> AsyncIterator[StreamedResponse]:
        attempt = 1
        while True:
            # One exit stack per attempt so a half-entered context is torn
            # down before the next try (same shape as FallbackModel).
            async with AsyncExitStack() as stack:
                try:
                    response = await stack.enter_async_context(
                        self.wrapped.request_stream(
                            messages,
                            model_settings,
                            model_request_parameters,
                            run_context,
                        )
                    )
                except Exception as exc:
                    delay = self._delay_for(exc, attempt)
                    if delay is None:
                        raise
                    await self._before_retry(exc, attempt, delay)
                    attempt += 1
                    continue
                if attempt > 1:
                    await self._after_recovery(attempt)
                # Past this point the client may already have seen tokens:
                # consumer-side failures MUST propagate, never re-enter.
                yield response
                return


def retry_status_payload(status: RetryStatus) -> Dict[str, Any]:
    """`RetryStatus` -> the camelCase `data-retry-status` payload."""
    if status.state == "recovered":
        return {"state": "recovered"}
    payload: Dict[str, Any] = {
        "state": status.state,
        "attempt": status.attempt,
        "maxAttempts": status.max_attempts,
        "delayMs": status.delay_ms,
    }
    if status.error:
        payload["error"] = status.error
    return payload
