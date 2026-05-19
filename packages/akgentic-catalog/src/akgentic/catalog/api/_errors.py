"""Shared error response model and exception handlers for the catalog API."""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ValidationError

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


async def _handle_pydantic_validation_error(
    request: Request,
    exc: ValidationError,
) -> JSONResponse:
    """Convert raw ``pydantic.ValidationError`` to a 422 JSON response.

    FastAPI's default 422 handler only fires for ``RequestValidationError``
    — the exception type it raises when parameter/body validation fails on
    the typed-argument path. The multi-format body handlers in
    :mod:`akgentic.catalog.api.router` call ``model.model_validate(...)``
    directly (after parsing JSON or YAML into a dict), which raises the raw
    ``pydantic.ValidationError``. This handler mirrors FastAPI's default
    shape (``{"detail": [...]}``) so clients see the same 422 contract
    regardless of whether the body path is typed-argument or
    ``_parse_body_as`` (Epic 21).
    """
    logger.debug("pydantic validation error: %s", exc)
    return JSONResponse(
        status_code=422,
        content={"detail": jsonable_encoder(exc.errors())},
    )


def add_exception_handlers(app: FastAPI) -> None:
    """Register catalog exception handlers on a FastAPI application.

    Args:
        app: The FastAPI application to add handlers to.
    """
    app.add_exception_handler(EntryNotFoundError, _handle_entry_not_found)  # type: ignore[arg-type]
    app.add_exception_handler(CatalogValidationError, _handle_catalog_validation_error)  # type: ignore[arg-type]
    app.add_exception_handler(ValidationError, _handle_pydantic_validation_error)  # type: ignore[arg-type]
