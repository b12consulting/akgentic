"""Shared error response model and exception handlers for the catalog API."""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from akgentic.catalog.models.errors import CatalogValidationError, EntryNotFoundError

__all__ = ["ErrorResponse", "add_exception_handlers"]

logger = logging.getLogger(__name__)


class ErrorResponse(BaseModel):
    """Structured error response returned by catalog API endpoints."""

    detail: str
    errors: list[str] = []


async def _handle_entry_not_found(
    request: Request,
    exc: EntryNotFoundError,
) -> JSONResponse:
    """Convert EntryNotFoundError to a 404 JSON response."""
    logger.debug("entry not found: %s", exc)
    return JSONResponse(
        status_code=404,
        content=ErrorResponse(detail=str(exc)).model_dump(),
    )


async def _handle_catalog_validation_error(
    request: Request,
    exc: CatalogValidationError,
) -> JSONResponse:
    """Convert CatalogValidationError to a 409 JSON response."""
    logger.debug("catalog validation error: %s", exc)
    return JSONResponse(
        status_code=409,
        content=ErrorResponse(detail=str(exc), errors=exc.errors).model_dump(),
    )


def add_exception_handlers(app: FastAPI) -> None:
    """Register catalog exception handlers on a FastAPI application.

    Args:
        app: The FastAPI application to add handlers to.
    """
    app.add_exception_handler(EntryNotFoundError, _handle_entry_not_found)  # type: ignore[arg-type]
    app.add_exception_handler(CatalogValidationError, _handle_catalog_validation_error)  # type: ignore[arg-type]
