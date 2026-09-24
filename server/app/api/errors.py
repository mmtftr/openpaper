"""One error shape for the whole API.

Handlers signal failures with `raise HTTPException(status_code, detail="...")`
(a plain string). FastAPI already renders that as `{"detail": "..."}`; the
handler below gives unhandled exceptions the same shape. Request validation
errors keep FastAPI's standard 422 body (`detail` is a list), which the
OpenAPI schema describes as `HTTPValidationError`.
"""

import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

logger = logging.getLogger(__name__)


class ApiError(BaseModel):
    detail: str


# Declared on the app so every operation in the OpenAPI schema documents it.
ERROR_RESPONSES: dict[int | str, dict] = {
    "4XX": {"model": ApiError, "description": "Client error"},
    "5XX": {"model": ApiError, "description": "Server error"},
}


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("Unhandled error on %s %s", request.method, request.url.path)
        return JSONResponse(status_code=500, content={"detail": "Internal server error"})
