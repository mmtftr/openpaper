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
UI (see `app.llm.chat.pump.StreamPump.push_retry_status`); callback
failures are swallowed so a
broken observer can never break a chat turn.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass
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

from pydantic_ai.messages import ModelMessage, ModelResponse
from pydantic_ai.models import Model, ModelRequestParameters, StreamedResponse
from pydantic_ai.models.wrapper import WrapperModel
from pydantic_ai.settings import ModelSettings

from app.core.errors import (  # noqa: F401 — re-exported for callers/tests
    EMPTY_STREAM_MESSAGE,
    NON_RETRYABLE_STATUS_CODES,
    RETRYABLE_STATUS_CODES,
    classify,
    is_retryable,
    retry_after_seconds,
)
from app.core.errors import short_error_text as _short_error_text
from app.schemas.chat_stream import retry_status_data

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


@dataclass(frozen=True)
class RetryStatus:
    """One retry-lifecycle notification handed to `on_retry`."""

    state: Literal["retrying", "recovered"]
    attempt: Optional[int] = None
    max_attempts: int = MAX_ATTEMPTS
    delay_ms: Optional[int] = None
    error: Optional[str] = None


OnRetry = Callable[[RetryStatus], Awaitable[None]]


def short_error_text(error: BaseException) -> str:
    """A compact, wire-safe description of a failure."""
    return _short_error_text(error, MAX_ERROR_CHARS)


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
        classified = classify(error)
        if not classified.retryable:
            return None
        base = self._backoff[min(attempt - 1, len(self._backoff) - 1)]
        hinted = classified.retry_after
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
    return retry_status_data(
        status.state,
        attempt=status.attempt,
        max_attempts=status.max_attempts,
        delay_ms=status.delay_ms,
        error=status.error,
    )
