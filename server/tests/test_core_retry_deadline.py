"""Backoff policies, in-call retries and deadlines (`app.core`)."""

import asyncio

import httpx
import pytest

from app.core.deadline import Deadline, DeadlineExceeded
from app.core.errors import Classified, ErrorKind, PermanentError, TemporaryError
from app.core.retry import (
    STAGE_BACKOFF,
    BackoffPolicy,
    next_stage_delay,
    retry_call,
)

# -- backoff ---------------------------------------------------------------


def _c(kind, retry_after=None):
    return Classified(kind=kind, message="x", retry_after=retry_after)


def test_stage_backoff_steps_then_repeats():
    delays = [STAGE_BACKOFF.delay(n) for n in range(1, 7)]
    assert delays == [5.0, 30.0, 120.0, 600.0, 600.0, 600.0]


def test_next_stage_delay_until_attempts_run_out():
    temp = _c(ErrorKind.TEMPORARY)
    assert next_stage_delay(temp, attempt=1, max_attempts=5) == 5.0
    assert next_stage_delay(temp, attempt=4, max_attempts=5) == 600.0
    assert next_stage_delay(temp, attempt=5, max_attempts=5) is None


@pytest.mark.parametrize("kind", [ErrorKind.PERMANENT, ErrorKind.CONFIG])
def test_permanent_and_config_never_retry(kind):
    assert next_stage_delay(_c(kind), attempt=1, max_attempts=5) is None


def test_rate_limited_waits_for_retry_after():
    # Requeue at Retry-After, even when shorter than the policy step...
    assert next_stage_delay(_c(ErrorKind.RATE_LIMITED, 2.0), 3, 5) == 2.0
    # ...clamped to the policy's max hint.
    assert next_stage_delay(_c(ErrorKind.RATE_LIMITED, 86400.0), 1, 5) == 3600.0
    # No hint: the policy step.
    assert next_stage_delay(_c(ErrorKind.RATE_LIMITED), 2, 5) == 30.0


def test_temporary_hint_only_lengthens():
    assert next_stage_delay(_c(ErrorKind.TEMPORARY, 1.0), 2, 5) == 30.0
    assert next_stage_delay(_c(ErrorKind.TEMPORARY, 90.0), 2, 5) == 90.0


# -- retry_call ------------------------------------------------------------


class _Flaky:
    def __init__(self, failures):
        self.failures = list(failures)
        self.calls = 0

    async def __call__(self):
        self.calls += 1
        if self.failures:
            raise self.failures.pop(0)
        return "ok"


def _run(coro):
    return asyncio.run(coro)


def test_retry_call_recovers_from_transient_errors():
    sleeps = []

    async def sleep(d):
        sleeps.append(d)

    fn = _Flaky([httpx.ConnectError("x"), TemporaryError("busy")])
    assert _run(retry_call(fn, sleep=sleep)) == "ok"
    assert fn.calls == 3
    assert sleeps == [0.5, 2.0]


def test_retry_call_gives_up_on_permanent_and_after_attempts():
    async def sleep(d):
        pass

    fn = _Flaky([PermanentError("no")])
    with pytest.raises(PermanentError):
        _run(retry_call(fn, sleep=sleep))
    assert fn.calls == 1

    fn = _Flaky([TemporaryError("a"), TemporaryError("b"), TemporaryError("c")])
    with pytest.raises(TemporaryError, match="c"):
        _run(retry_call(fn, attempts=3, sleep=sleep))
    assert fn.calls == 3


def test_retry_call_leaves_long_waits_to_the_stage():
    async def sleep(d):
        raise AssertionError("should not sleep")

    fn = _Flaky([TemporaryError("busy", retry_after=120)])
    with pytest.raises(TemporaryError):
        _run(retry_call(fn, sleep=sleep))
    assert fn.calls == 1


def test_retry_call_respects_the_deadline():
    async def sleep(d):
        raise AssertionError("should not sleep")

    now = [0.0]
    deadline = Deadline(2.0, clock=lambda: now[0])
    fn = _Flaky([TemporaryError("busy")])
    policy = BackoffPolicy(delays=(5.0,))
    with pytest.raises(TemporaryError):
        _run(retry_call(fn, policy=policy, deadline=deadline, sleep=sleep))


# -- deadline --------------------------------------------------------------


def test_deadline_hands_out_the_smaller_of_cap_and_remaining():
    now = [100.0]
    deadline = Deadline(60, clock=lambda: now[0])
    assert deadline.timeout(30) == 30
    assert deadline.timeout() == 60
    now[0] += 45
    assert deadline.remaining() == 15
    assert deadline.timeout(30) == 15
    assert not deadline.expired


def test_deadline_refuses_calls_it_has_no_time_for():
    now = [0.0]
    deadline = Deadline(10, clock=lambda: now[0])
    now[0] = 9.5
    with pytest.raises(DeadlineExceeded):
        deadline.timeout(5)
    now[0] = 20
    assert deadline.remaining() == 0.0
    assert deadline.expired
    with pytest.raises(TimeoutError):  # DeadlineExceeded is a TimeoutError
        deadline.check()
