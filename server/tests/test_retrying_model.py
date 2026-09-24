"""Unit tests for `app.llm.retrying_model`.

Covers the retryability classifier, the backoff schedule (including
`Retry-After` honoring and its cap), the `on_retry` notification contract,
and the two Model entry points — `request` and the context-manager
`request_stream`, whose retry window closes the moment a stream is yielded.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from typing import Any, List, Optional, Sequence

import httpx
import pytest
from pydantic_ai.exceptions import (
    ModelAPIError,
    ModelHTTPError,
    UnexpectedModelBehavior,
)
from pydantic_ai.messages import ModelResponse, TextPart
from pydantic_ai.models import Model

from app.llm.retrying_model import (
    MAX_ATTEMPTS,
    MAX_BACKOFF_SECONDS,
    RetryingModel,
    RetryStatus,
    is_retryable,
    retry_after_seconds,
    retry_status_payload,
    short_error_text,
)

# =====================================================================
# helpers
# =====================================================================


class _HeaderBearer(Exception):
    """Stand-in for a provider SDK error that still carries its headers."""

    def __init__(self, headers: dict) -> None:
        super().__init__("upstream")
        self.headers = headers


def _http_error(
    status: int,
    *,
    headers: Optional[dict] = None,
    body: Any = None,
    httpx_response: bool = False,
) -> ModelHTTPError:
    error = ModelHTTPError(status_code=status, model_name="fake", body=body)
    if headers is not None:
        if httpx_response:
            cause: Exception = httpx.HTTPStatusError(
                "boom",
                request=httpx.Request("POST", "https://example.invalid/v1"),
                response=httpx.Response(status, headers=headers),
            )
        else:
            cause = _HeaderBearer(headers)
        error.__cause__ = cause
    return error


_STREAM_SENTINEL = object()


class FakeModel(Model):
    """Minimal `Model` that fails a scripted number of times, then works."""

    def __init__(
        self,
        *,
        request_errors: Sequence[BaseException] = (),
        stream_errors: Sequence[BaseException] = (),
    ) -> None:
        super().__init__()
        self._request_errors = list(request_errors)
        self._stream_errors = list(stream_errors)
        self.request_calls = 0
        self.stream_calls = 0
        self.stream_closed = 0

    @property
    def model_name(self) -> str:
        return "fake"

    @property
    def system(self) -> str:
        return "fake"

    async def request(self, messages, model_settings, model_request_parameters):
        self.request_calls += 1
        if self._request_errors:
            raise self._request_errors.pop(0)
        return ModelResponse(parts=[TextPart(content="ok")])

    @asynccontextmanager
    async def request_stream(
        self,
        messages,
        model_settings,
        model_request_parameters,
        run_context=None,
    ):
        self.stream_calls += 1
        if self._stream_errors:
            raise self._stream_errors.pop(0)
        try:
            yield _STREAM_SENTINEL
        finally:
            self.stream_closed += 1


class _Harness:
    """A RetryingModel with sleeps recorded instead of slept."""

    def __init__(self, wrapped: FakeModel, **kwargs: Any) -> None:
        self.wrapped = wrapped
        self.sleeps: List[float] = []
        self.statuses: List[RetryStatus] = []
        # One ordered log of both, so a swapped notify/sleep is visible.
        self.events: List[str] = []

        async def sleep(delay: float) -> None:
            self.sleeps.append(delay)
            self.events.append(f"sleep:{delay}")

        async def on_retry(status: RetryStatus) -> None:
            self.statuses.append(status)
            self.events.append(f"status:{status.state}:{status.attempt}")

        kwargs.setdefault("on_retry", on_retry)
        self.model = RetryingModel(wrapped, sleep=sleep, **kwargs)

    def call(self):
        return asyncio.run(self.model.request([], None, None))  # type: ignore[arg-type]

    def stream(self, consume=None):
        async def run():
            async with self.model.request_stream(
                [],
                None,
                None,  # type: ignore[arg-type]
            ) as response:
                if consume is not None:
                    await consume(response)
                return response

        return asyncio.run(run())


# =====================================================================
# classifier
# =====================================================================


class TestIsRetryable:
    @pytest.mark.parametrize("status", [408, 409, 429, 500, 502, 503, 504, 529])
    def test_transient_statuses(self, status: int):
        assert is_retryable(_http_error(status)) is True

    @pytest.mark.parametrize("status", [400, 401, 403, 404, 422])
    def test_deterministic_statuses(self, status: int):
        assert is_retryable(_http_error(status)) is False

    def test_content_filter_400_is_never_retried(self):
        error = _http_error(400, body={"error": {"code": "content_filter"}})
        assert is_retryable(error) is False

    def test_connection_errors(self):
        assert is_retryable(httpx.ConnectError("refused")) is True
        assert is_retryable(httpx.ReadTimeout("gap")) is True
        assert is_retryable(httpx.RemoteProtocolError("truncated")) is True
        assert is_retryable(ModelAPIError(model_name="m", message="conn")) is True

    def test_unrelated_exception_is_not_retryable(self):
        assert is_retryable(ValueError("bug")) is False

    def test_google_style_code_attribute_is_read(self):
        """GoogleModel peeks the first chunk outside pydantic-ai's error
        mapper, so the raw SDK error arrives with `.code`, not
        `.status_code`."""

        class GoogleAPIError(Exception):
            def __init__(self, code: int) -> None:
                super().__init__(f"google error {code}")
                self.code = code

        assert is_retryable(GoogleAPIError(503)) is True
        assert is_retryable(GoogleAPIError(400)) is False

    def test_empty_stream_is_retryable(self):
        """Overloaded deployments hang up with nothing — transient."""
        error = UnexpectedModelBehavior(
            "Streamed response ended without content or tool calls"
        )
        assert is_retryable(error) is True

    def test_other_unexpected_model_behavior_is_not_retryable(self):
        """Deterministic misbehavior would just cost 3x."""
        assert (
            is_retryable(UnexpectedModelBehavior("Exceeded maximum retries")) is False
        )
        assert is_retryable(UnexpectedModelBehavior("Invalid JSON")) is False

    def test_content_filter_error_is_not_retryable(self):
        from pydantic_ai.exceptions import ContentFilterError

        assert is_retryable(ContentFilterError("blocked by content filter")) is False


class TestRetryAfter:
    def test_absent(self):
        assert retry_after_seconds(_http_error(429)) is None

    def test_seconds_header_via_cause(self):
        assert retry_after_seconds(_http_error(429, headers={"retry-after": "7"})) == 7

    def test_seconds_header_from_httpx_response(self):
        error = _http_error(429, headers={"retry-after": "4"}, httpx_response=True)
        assert retry_after_seconds(error) == 4

    def test_millisecond_header_wins(self):
        error = _http_error(429, headers={"retry-after-ms": "2500"})
        assert retry_after_seconds(error) == 2.5

    def test_http_date_form_is_parsed(self):
        """RFC 9110 allows a date instead of a delta, and providers send it."""
        when = datetime.now(timezone.utc) + timedelta(seconds=42)
        error = _http_error(
            429, headers={"retry-after": format_datetime(when, usegmt=True)}
        )
        delay = retry_after_seconds(error)
        assert delay is not None and 40 <= delay <= 43

    def test_http_date_in_the_past_is_ignored(self):
        when = datetime.now(timezone.utc) - timedelta(seconds=30)
        error = _http_error(
            429, headers={"retry-after": format_datetime(when, usegmt=True)}
        )
        assert retry_after_seconds(error) is None

    def test_unparseable_header_is_ignored(self):
        error = _http_error(429, headers={"retry-after": "soonish"})
        assert retry_after_seconds(error) is None

    def test_http_date_is_capped_like_any_other_hint(self):
        when = datetime.now(timezone.utc) + timedelta(hours=1)
        wrapped = FakeModel(
            request_errors=[
                _http_error(
                    429, headers={"retry-after": format_datetime(when, usegmt=True)}
                )
            ]
        )
        harness = _Harness(wrapped)
        harness.call()
        assert harness.sleeps == [MAX_BACKOFF_SECONDS]

    def test_body_field(self):
        error = _http_error(429, body={"error": {"retry_after": 3}})
        assert retry_after_seconds(error) == 3


class TestShortErrorText:
    def test_includes_status_and_type(self):
        text = short_error_text(_http_error(503))
        assert text.startswith("ModelHTTPError (503)")

    def test_is_truncated(self):
        error = ModelHTTPError(status_code=500, model_name="m", body="x" * 5000)
        assert len(short_error_text(error)) <= 200


class TestRetryStatusPayload:
    def test_retrying_is_camel_case(self):
        payload = retry_status_payload(
            RetryStatus(
                state="retrying",
                attempt=2,
                max_attempts=3,
                delay_ms=2000,
                error="boom",
            )
        )
        assert payload == {
            "state": "retrying",
            "attempt": 2,
            "maxAttempts": 3,
            "delayMs": 2000,
            "error": "boom",
        }

    def test_recovered_is_bare(self):
        assert retry_status_payload(RetryStatus(state="recovered")) == {
            "state": "recovered"
        }


# =====================================================================
# request()
# =====================================================================


class TestRequestRetries:
    def test_two_failures_then_success(self):
        wrapped = FakeModel(request_errors=[_http_error(500), _http_error(500)])
        harness = _Harness(wrapped)
        response = harness.call()

        assert isinstance(response, ModelResponse)
        assert wrapped.request_calls == 3
        assert harness.sleeps == [1.0, 2.0]
        assert [s.state for s in harness.statuses] == [
            "retrying",
            "retrying",
            "recovered",
        ]
        assert [s.attempt for s in harness.statuses[:2]] == [2, 3]
        assert [s.max_attempts for s in harness.statuses] == [3, 3, 3]
        assert [s.delay_ms for s in harness.statuses[:2]] == [1000, 2000]
        assert all("500" in (s.error or "") for s in harness.statuses[:2])
        # Each status is announced BEFORE its wait — a client that only
        # learns about the retry after the backoff has no use for it.
        assert harness.events == [
            "status:retrying:2",
            "sleep:1.0",
            "status:retrying:3",
            "sleep:2.0",
            "status:recovered:None",
        ]

    def test_exhausts_budget_and_raises_last_error(self):
        errors = [_http_error(503) for _ in range(MAX_ATTEMPTS)]
        wrapped = FakeModel(request_errors=errors)
        harness = _Harness(wrapped)
        with pytest.raises(ModelHTTPError) as excinfo:
            harness.call()
        assert excinfo.value.status_code == 503
        assert wrapped.request_calls == MAX_ATTEMPTS
        assert [s.state for s in harness.statuses] == ["retrying", "retrying"]

    def test_non_retryable_fails_on_first_attempt(self):
        wrapped = FakeModel(request_errors=[_http_error(400)])
        harness = _Harness(wrapped)
        with pytest.raises(ModelHTTPError):
            harness.call()
        assert wrapped.request_calls == 1
        assert harness.statuses == []
        assert harness.sleeps == []

    def test_success_notifies_nothing(self):
        wrapped = FakeModel()
        harness = _Harness(wrapped)
        harness.call()
        assert wrapped.request_calls == 1
        assert harness.statuses == []

    def test_retry_after_overrides_backoff_and_is_capped(self):
        wrapped = FakeModel(
            request_errors=[
                _http_error(429, headers={"retry-after": "6"}),
                _http_error(429, headers={"retry-after": "600"}),
            ]
        )
        harness = _Harness(wrapped)
        harness.call()
        assert harness.sleeps == [6.0, MAX_BACKOFF_SECONDS]

    def test_retry_after_smaller_than_backoff_is_ignored(self):
        wrapped = FakeModel(
            request_errors=[_http_error(429, headers={"retry-after": "0.2"})]
        )
        harness = _Harness(wrapped)
        harness.call()
        assert harness.sleeps == [1.0]

    def test_callback_failure_never_breaks_the_run(self):
        wrapped = FakeModel(request_errors=[_http_error(500)])
        sleeps: List[float] = []

        async def sleep(delay: float) -> None:
            sleeps.append(delay)

        async def on_retry(status: RetryStatus) -> None:
            raise RuntimeError("observer exploded")

        model = RetryingModel(wrapped, on_retry=on_retry, sleep=sleep)
        response = asyncio.run(model.request([], None, None))  # type: ignore[arg-type]
        assert isinstance(response, ModelResponse)
        assert wrapped.request_calls == 2

    def test_callback_cancellation_is_not_swallowed(self):
        """The runtime's callback can BLOCK (the `recovered` chunk waits for
        queue room), so teardown cancels it — and that cancellation has to
        escape the retry loop rather than being logged as an observer bug."""
        wrapped = FakeModel(request_errors=[_http_error(500)])

        async def sleep(delay: float) -> None:
            return None

        async def on_retry(status: RetryStatus) -> None:
            raise asyncio.CancelledError()

        model = RetryingModel(wrapped, on_retry=on_retry, sleep=sleep)
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(model.request([], None, None))  # type: ignore[arg-type]
        assert wrapped.request_calls == 1

    def test_custom_attempt_budget(self):
        wrapped = FakeModel(request_errors=[_http_error(500) for _ in range(5)])
        harness = _Harness(wrapped, max_attempts=5, backoff_seconds=(0.5,))
        with pytest.raises(ModelHTTPError):
            asyncio.run(harness.model.request([], None, None))  # type: ignore[arg-type]
        # 5 attempts, and the single backoff entry is reused for each wait.
        assert wrapped.request_calls == 5
        assert harness.sleeps == [0.5, 0.5, 0.5, 0.5]


# =====================================================================
# request_stream()
# =====================================================================


class TestRequestStreamRetries:
    def test_entry_failure_is_retried(self):
        wrapped = FakeModel(stream_errors=[_http_error(502), _http_error(429)])
        harness = _Harness(wrapped)
        response = harness.stream()

        assert response is _STREAM_SENTINEL
        assert wrapped.stream_calls == 3
        assert harness.sleeps == [1.0, 2.0]
        assert [s.state for s in harness.statuses] == [
            "retrying",
            "retrying",
            "recovered",
        ]

    def test_entry_failure_exhausts_budget(self):
        wrapped = FakeModel(stream_errors=[_http_error(500) for _ in range(3)])
        harness = _Harness(wrapped)
        with pytest.raises(ModelHTTPError):
            harness.stream()
        assert wrapped.stream_calls == 3

    def test_non_retryable_entry_failure_raises_immediately(self):
        wrapped = FakeModel(stream_errors=[_http_error(422)])
        harness = _Harness(wrapped)
        with pytest.raises(ModelHTTPError):
            harness.stream()
        assert wrapped.stream_calls == 1
        assert harness.statuses == []

    def test_mid_stream_failure_is_never_retried(self):
        """Once tokens can have reached the client, re-entry would duplicate
        output — the consumer's exception must propagate untouched."""
        wrapped = FakeModel()
        harness = _Harness(wrapped)

        async def consume(_response):
            raise httpx.ReadError("connection dropped mid-stream")

        with pytest.raises(httpx.ReadError):
            harness.stream(consume=consume)
        assert wrapped.stream_calls == 1
        assert wrapped.stream_closed == 1
        assert harness.statuses == []

    def test_wrapped_stream_is_closed_on_success(self):
        wrapped = FakeModel()
        harness = _Harness(wrapped)
        harness.stream()
        assert wrapped.stream_closed == 1

    def test_model_name_is_delegated(self):
        assert _Harness(FakeModel()).model.model_name == "fake"

    def test_empty_stream_failure_is_retried(self):
        wrapped = FakeModel(
            stream_errors=[
                UnexpectedModelBehavior(
                    "Streamed response ended without content or tool calls"
                )
            ]
        )
        harness = _Harness(wrapped)
        assert harness.stream() is _STREAM_SENTINEL
        assert wrapped.stream_calls == 2

    def test_base_url_is_delegated(self):
        """WrapperModel leaves `base_url` at None, blanking it in spans."""

        class WithBaseUrl(FakeModel):
            @property
            def base_url(self) -> str:
                return "https://example.invalid/v1"

        assert _Harness(WithBaseUrl()).model.base_url == "https://example.invalid/v1"
