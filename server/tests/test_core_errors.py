"""`app.core.errors.classify` and its agreement with the chat retry policy."""

import asyncio

import httpx
import pytest
from pydantic_ai.exceptions import (
    ModelAPIError,
    ModelHTTPError,
    UnexpectedModelBehavior,
)

from app.core.deadline import DeadlineExceeded
from app.core.errors import (
    EMPTY_STREAM_MESSAGE,
    ConfigError,
    ErrorKind,
    PermanentError,
    TemporaryError,
    classify,
    is_retryable,
)


def _http_status_error(status: int, headers: dict | None = None):
    request = httpx.Request("GET", "https://api.crossref.org/works/x")
    response = httpx.Response(status, headers=headers or {}, request=request)
    return httpx.HTTPStatusError("boom", request=request, response=response)


@pytest.mark.parametrize(
    "status,kind",
    [
        (429, ErrorKind.RATE_LIMITED),
        (500, ErrorKind.TEMPORARY),
        (502, ErrorKind.TEMPORARY),
        (503, ErrorKind.TEMPORARY),
        (529, ErrorKind.TEMPORARY),
        (408, ErrorKind.TEMPORARY),
        (409, ErrorKind.TEMPORARY),
        (400, ErrorKind.PERMANENT),
        (404, ErrorKind.PERMANENT),
        (422, ErrorKind.PERMANENT),
        (418, ErrorKind.PERMANENT),
        (401, ErrorKind.CONFIG),
        (403, ErrorKind.CONFIG),
    ],
)
def test_http_status_codes(status, kind):
    assert classify(_http_status_error(status)).kind is kind
    assert classify(ModelHTTPError(status, "m", body=None)).kind is kind


def test_rate_limit_carries_retry_after():
    got = classify(_http_status_error(429, {"retry-after": "7"}))
    assert got.kind is ErrorKind.RATE_LIMITED
    assert got.retry_after == 7.0
    # A hint on a 503 is kept too (the policy decides how to use it).
    got = classify(_http_status_error(503, {"retry-after-ms": "1500"}))
    assert got.kind is ErrorKind.TEMPORARY
    assert got.retry_after == 1.5


def test_retry_after_in_json_body():
    exc = ModelHTTPError(429, "m", body={"error": {"retry_after": 3}})
    assert classify(exc).retry_after == 3.0


@pytest.mark.parametrize(
    "exc",
    [
        httpx.ConnectError("refused"),
        httpx.ReadTimeout("slow"),
        ConnectionResetError(),
        TimeoutError(),
        asyncio.TimeoutError(),
        DeadlineExceeded("out of time"),
        ModelAPIError("m", "connection dropped"),
        UnexpectedModelBehavior(EMPTY_STREAM_MESSAGE),
        TemporaryError("try again"),
    ],
)
def test_transient_failures_are_temporary(exc):
    assert classify(exc).kind is ErrorKind.TEMPORARY
    assert is_retryable(exc)


@pytest.mark.parametrize(
    "exc,kind",
    [
        (UnexpectedModelBehavior("Exceeded maximum retries"), ErrorKind.PERMANENT),
        (ValueError("bug"), ErrorKind.PERMANENT),
        (PermanentError("PDF is encrypted"), ErrorKind.PERMANENT),
        (ConfigError("MISTRAL_API_KEY is not set"), ErrorKind.CONFIG),
    ],
)
def test_non_retryable(exc, kind):
    got = classify(exc)
    assert got.kind is kind
    assert not got.retryable
    assert not is_retryable(exc)


def test_messages_are_readable_and_capped():
    # Our own errors are shown verbatim.
    assert classify(ConfigError("MISTRAL_API_KEY is not set")).message == (
        "MISTRAL_API_KEY is not set"
    )
    # Foreign ones get type (+ status) as a prefix.
    assert classify(_http_status_error(404)).message.startswith(
        "HTTPStatusError (404): "
    )
    long = classify(ValueError("x" * 2000), max_chars=100).message
    assert len(long) == 100 and long.endswith("…")


def test_temporary_error_retry_after():
    assert classify(TemporaryError("busy", retry_after=12)).retry_after == 12


def test_messages_hide_api_keys_in_urls():
    request = httpx.Request(
        "GET", "https://api.openalex.org/works?search=x&api_key=s3cret&mailto=a@b.c"
    )
    response = httpx.Response(403, request=request)
    error = httpx.HTTPStatusError(
        f"Client error for url '{request.url}'", request=request, response=response
    )
    message = classify(error).message
    assert "s3cret" not in message
    assert "api_key=***&mailto=a@b.c" in message
