"""FastAPI router for template catalog CRUD and search operations."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from fastapi import APIRouter, Response

from akgentic.catalog.models.errors import EntryNotFoundError
from akgentic.catalog.models.queries import TemplateQuery
from akgentic.catalog.models.template import TemplateEntry

if TYPE_CHECKING:
    from akgentic.catalog.services.template_catalog import TemplateCatalog

__all__ = ["router", "set_catalog"]

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/templates", tags=["templates"])

_catalog: TemplateCatalog | None = None


def set_catalog(catalog: TemplateCatalog) -> None:
    """Inject the template catalog service instance.

    Args:
        catalog: The TemplateCatalog service to use for all operations.
    """
    global _catalog  # noqa: PLW0603
    _catalog = catalog


def _get_catalog() -> TemplateCatalog:
    """Return the injected catalog or raise if not configured."""
    if _catalog is None:
        raise RuntimeError("TemplateCatalog not configured — call set_catalog() first")
    return _catalog


@router.post("", response_model=TemplateEntry, status_code=201)
async def create_template(entry: TemplateEntry) -> TemplateEntry:
    """Create a new template entry."""
    logger.debug("POST /api/templates — creating %s", entry.id)
    _get_catalog().create(entry)
    return entry


@router.get("", response_model=list[TemplateEntry])
async def list_templates() -> list[TemplateEntry]:
    """List all template entries."""
    logger.debug("GET /api/templates — listing all")
    return _get_catalog().list()


@router.get("/{id}", response_model=TemplateEntry)
async def get_template(id: str) -> TemplateEntry:
    """Get a template entry by id."""
    logger.debug("GET /api/templates/%s", id)
    entry = _get_catalog().get(id)
    if entry is None:
        raise EntryNotFoundError(f"Template '{id}' not found")
    return entry


@router.post("/search", response_model=list[TemplateEntry])
async def search_templates(query: TemplateQuery) -> list[TemplateEntry]:
    """Search template entries by query."""
    logger.debug("POST /api/templates/search")
    return _get_catalog().search(query)


@router.put("/{id}", response_model=TemplateEntry)
async def update_template(id: str, entry: TemplateEntry) -> TemplateEntry:
    """Update an existing template entry."""
    logger.debug("PUT /api/templates/%s", id)
    _get_catalog().update(id, entry)
    return entry


@router.delete("/{id}", status_code=204)
async def delete_template(id: str) -> Response:
    """Delete a template entry."""
    logger.debug("DELETE /api/templates/%s", id)
    _get_catalog().delete(id)
    return Response(status_code=204)
