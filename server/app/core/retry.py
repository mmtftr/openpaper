"""Backoff policies.

Two levels, never nested inside each other more than this:

- **Stage retries** (`STAGE_BACKOFF`, `next_stage_delay`): a failed ingest
  stage is requeued by the worker after 5 s, 30 s, 2 min, 10 min. This is
  where anything slow or long-lived is retried.
- **In-call retries** (`retry_call`): a couple of quick retries around one
  cheap idempotent call (a Crossref GET, one OCR batch) so a single dropped
  connection doesn't cost a whole stage retry. Bounded by the stage deadline.

SDK-level retries stay off (`max_retries=0`); the attempt budget lives here.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Awaitable, Callable, Optional, TypeVar

from app.core.deadline import Deadline
from app.core.errors import Classified, ErrorKind, classify

logger = logging.getLogger(__name__)

T = TypeVar("T")


@dataclass(frozen=True)
class BackoffPolicy:
    """Waits before retry 1, 2, ...; the last entry repeats."""

    delays: tuple[float, ...]
    # Clamp for a provider's Retry-After hint (a bogus "retry in 1 day"
    # must not park the work forever).
    max_hint: float = 3600.0

    def delay(self, failed_attempt: int) -> float:
        """Seconds to wait after attempt `failed_attempt` (1-based) failed."""
        index = min(max(failed_attempt, 1) - 1, len(self.delays) - 1)
        return self.delays[index]

    def delay_for(self, classified: Classified, failed_attempt: int) -> float:
        """Policy delay, or the provider's hint when it asks for longer."""
        base = self.delay(failed_attempt)
        if classified.retry_after is None:
            return base
        hint = min(classified.retry_after, self.max_hint)
        if classified.kind is ErrorKind.RATE_LIMITED:
            # "Requeue at Retry-After": the hint is the answer, even if short.
            return hint
        return max(base, hint)


STAGE_BACKOFF = BackoffPolicy(delays=(5.0, 30.0, 120.0, 600.0))
# In-call: short, and the hint is capped low — anything longer should be a
# stage retry, not a coroutine sleeping inside a running stage.
CALL_BACKOFF = BackoffPolicy(delays=(0.5, 2.0), max_hint=10.0)


def next_stage_delay(
    classified: Classified,
    attempt: int,
    max_attempts: int,
    policy: BackoffPolicy = STAGE_BACKOFF,
) -> Optional[float]:
    """Seconds until a failed stage runs again, or None = mark it `failed`.

    `attempt` is the attempt that just failed (1-based). Permanent and
    config errors never retry; retryable ones retry until `max_attempts`.
    """
    if not classified.retryable or attempt >= max_attempts:
        return None
    return policy.delay_for(classified, attempt)


async def retry_call(
    fn: Callable[[], Awaitable[T]],
    *,
    attempts: int = 3,
    policy: BackoffPolicy = CALL_BACKOFF,
    deadline: Optional[Deadline] = None,
    what: str = "call",
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> T:
    """Run `fn()`, retrying temporary / rate-limited failures a few times.

    Only for quick idempotent calls. Gives up (re-raising the last error)
    when the error isn't retryable, attempts run out, or the wait would not
    fit in `deadline` with a second to spare for the call itself.
    """
    attempt = 1
    while True:
        try:
            return await fn()
        except Exception as exc:
            classified = classify(exc)
            if not classified.retryable or attempt >= attempts:
                raise
            delay = policy.delay_for(classified, attempt)
            if classified.retry_after is not None and classified.retry_after > (
                policy.max_hint
            ):
                raise  # the provider wants a long pause: that's a stage retry
            if deadline is not None and deadline.remaining() < delay + 1.0:
                raise
            logger.info(
                "%s failed (attempt %s/%s, %s), retrying in %.1fs: %s",
                what,
                attempt,
                attempts,
                classified.kind.value,
                delay,
                classified.message,
            )
            await sleep(delay)
            attempt += 1
