"""Shared httpx client for outbound API calls (Crossref, OpenAlex, arXiv, ...).

- Sane default timeouts; per-call timeouts should still come from the stage
  `Deadline` (`client.get(url, timeout=deadline.timeout(20))`).
- Polite-pool identification: Crossref and OpenAlex route requests that
  carry a contact address to a faster, more reliable pool. Set
  `CONTACT_EMAIL` (server/.env) to opt in; without it requests still work.
- No automatic retries here — see `app.core.retry`.

The ingest worker runs one event loop, so one client per process is shared
(`shared_client()`); close it on shutdown with `aclose_shared_client()`.
"""

from __future__ import annotations

import asyncio
from typing import Any, Mapping, Optional

import httpx

from app.settings import get_settings

DEFAULT_TIMEOUT = httpx.Timeout(30.0, connect=10.0)
DEFAULT_LIMITS = httpx.Limits(max_connections=32, max_keepalive_connections=16)
USER_AGENT = "OpenPaper/0.1 (personal research reader)"


def contact_email() -> Optional[str]:
    return get_settings().CONTACT_EMAIL


def polite_headers() -> dict[str, str]:
    """User-Agent carrying the contact address (Crossref's etiquette)."""
    email = contact_email()
    agent = f"{USER_AGENT[:-1]}; mailto:{email})" if email else USER_AGENT
    return {"User-Agent": agent, "Accept": "application/json"}


def crossref_params(**params: Any) -> dict[str, Any]:
    """Query params for api.crossref.org with `mailto` when configured."""
    email = contact_email()
    return {**params, **({"mailto": email} if email else {})}


def openalex_params(**params: Any) -> dict[str, Any]:
    """Query params for api.openalex.org: `api_key` and/or `mailto`."""
    extra: dict[str, Any] = {}
    key = get_settings().OPENALEX_API_KEY
    if key:
        extra["api_key"] = key
    email = contact_email()
    if email:
        extra["mailto"] = email
    return {**params, **extra}


def make_client(
    *,
    timeout: httpx.Timeout | float = DEFAULT_TIMEOUT,
    headers: Optional[Mapping[str, str]] = None,
    **kwargs: Any,
) -> httpx.AsyncClient:
    """A new `AsyncClient` with our defaults; caller owns closing it."""
    merged = {**polite_headers(), **(dict(headers) if headers else {})}
    return httpx.AsyncClient(
        timeout=timeout,
        headers=merged,
        limits=kwargs.pop("limits", DEFAULT_LIMITS),
        follow_redirects=kwargs.pop("follow_redirects", True),
        **kwargs,
    )


_shared: Optional[httpx.AsyncClient] = None
_shared_loop: Optional[asyncio.AbstractEventLoop] = None


def shared_client() -> httpx.AsyncClient:
    """The process-wide client (recreated if the event loop changed)."""
    global _shared, _shared_loop
    loop = asyncio.get_running_loop()
    if _shared is None or _shared.is_closed or _shared_loop is not loop:
        _shared = make_client()
        _shared_loop = loop
    return _shared


async def aclose_shared_client() -> None:
    global _shared, _shared_loop
    client, _shared, _shared_loop = _shared, None, None
    if client is not None and not client.is_closed:
        await client.aclose()
