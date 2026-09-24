"""Time budgets: every HTTP/LLM call's timeout comes out of what's left.

A stage gets a `Deadline` for its whole run. Each call inside asks
`deadline.timeout(cap)` for its own timeout — the smaller of the call's
usual cap and the time remaining — so a slow first call can't make the
stage overrun, and the stage can't start a call it has no time left for.

    deadline = Deadline(300)
    resp = await client.get(url, timeout=deadline.timeout(30))
"""

from __future__ import annotations

import time
from typing import Callable, Optional

# Below this, starting a call is pointless: it would time out on connect.
MIN_CALL_SECONDS = 1.0


class DeadlineExceeded(TimeoutError):
    """The budget ran out. A `TimeoutError`, so `classify` calls it temporary."""


class Deadline:
    def __init__(
        self, seconds: float, *, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self._clock = clock
        self.budget = float(seconds)
        self._expires_at = clock() + self.budget

    def remaining(self) -> float:
        """Seconds left, never negative."""
        return max(0.0, self._expires_at - self._clock())

    @property
    def expired(self) -> bool:
        return self.remaining() <= 0.0

    def timeout(self, cap: Optional[float] = None) -> float:
        """Timeout for one call: `min(cap, remaining)`.

        Raises `DeadlineExceeded` when less than `MIN_CALL_SECONDS` is left,
        so the caller doesn't start a call that can only time out.
        """
        left = self.remaining()
        if left < MIN_CALL_SECONDS:
            raise DeadlineExceeded(f"Out of time ({self.budget:.0f} s budget used up)")
        return left if cap is None else min(cap, left)

    def check(self) -> None:
        """Raise `DeadlineExceeded` if the budget is used up."""
        self.timeout()

    def __repr__(self) -> str:
        return f"<Deadline {self.remaining():.1f}s of {self.budget:.0f}s left>"
