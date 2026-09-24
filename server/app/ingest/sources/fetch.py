"""One GET for the bibliographic APIs: deadline-bound, quick retries, 404 = None."""

from __future__ import annotations

from typing import Any, Optional

import httpx

from app.core.deadline import Deadline
from app.core.retry import retry_call

# Cap for one lookup call; the stage deadline may cut it shorter.
CALL_TIMEOUT_S = 15.0


async def get_or_none(
    client: httpx.AsyncClient,
    url: str,
    *,
    params: dict[str, Any],
    deadline: Deadline,
    what: str,
) -> Optional[httpx.Response]:
    """GET `url`; None on 404, raises on other errors (for `classify`).

    Query parameters go in `params` only: httpx replaces a URL's own query
    string when `params` is given.
    """

    async def call() -> Optional[httpx.Response]:
        response = await client.get(
            url, params=params, timeout=deadline.timeout(CALL_TIMEOUT_S)
        )
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return response

    return await retry_call(call, attempts=2, deadline=deadline, what=what)
