"""Single FastAPI exception handler mapping the whole ``ServerError`` hierarchy.

One ``add_exception_handler(ServerError, ...)`` registration covers every
subclass: Starlette resolves a raised exception by walking its MRO, so any
``ServerError`` subclass routes to this base handler. Response shape mirrors the
catalog ``ErrorResponse`` (``{"detail": ..., "code": ...}``). See ADR-031
§Decision 3.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from akgentic.infra.errors import ServerError

__all__ = ["ServerErrorResponse", "add_server_exception_handlers"]

logger = logging.getLogger(__name__)


class ServerErrorResponse(BaseModel):
    """Structured error response for any mapped ``ServerError``."""

    detail: str
    code: str | None = None


async def _handle_server_error(request: Request, exc: ServerError) -> JSONResponse:
    """Map any ``ServerError`` to a JSON response using its carried attributes."""
    logger.warning("ServerError %s (%d): %s", exc.code, exc.status_code, exc.detail)
    return JSONResponse(
        status_code=exc.status_code,
        content=ServerErrorResponse(detail=exc.detail, code=exc.code).model_dump(),
        headers=exc.headers or None,
    )


def add_server_exception_handlers(app: FastAPI) -> None:
    """Register the single ``ServerError`` handler covering the whole hierarchy."""
    app.add_exception_handler(ServerError, _handle_server_error)  # type: ignore[arg-type]
