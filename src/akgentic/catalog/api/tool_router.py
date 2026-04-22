"""FastAPI router for tool catalog CRUD and search operations."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from fastapi import APIRouter, Response

from akgentic.catalog.models.errors import EntryNotFoundError
from akgentic.catalog.models.queries import ToolQuery
from akgentic.catalog.models.tool import ToolEntry

if TYPE_CHECKING:
    from akgentic.catalog.services.tool_catalog import ToolCatalog

__all__ = ["router", "set_catalog"]

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/tools", tags=["tools"])

_catalog: ToolCatalog | None = None


def set_catalog(catalog: ToolCatalog) -> None:
    """Inject the tool catalog service instance.

    Args:
        catalog: The ToolCatalog service to use for all operations.
    """
    global _catalog  # noqa: PLW0603
    _catalog = catalog


def _get_catalog() -> ToolCatalog:
    """Return the injected catalog or raise if not configured."""
    if _catalog is None:
        raise RuntimeError("ToolCatalog not configured — call set_catalog() first")
    return _catalog


@router.post("", response_model=ToolEntry, status_code=201)
async def create_tool(entry: ToolEntry) -> ToolEntry:
    """Create a new tool entry."""
    logger.debug("POST /api/tools — creating %s", entry.id)
    _get_catalog().create(entry)
    return entry


@router.get("", response_model=list[ToolEntry])
async def list_tools() -> list[ToolEntry]:
    """List all tool entries."""
    logger.debug("GET /api/tools — listing all")
    return _get_catalog().list()


@router.get("/{id}", response_model=ToolEntry)
async def get_tool(id: str) -> ToolEntry:
    """Get a tool entry by id."""
    logger.debug("GET /api/tools/%s", id)
    entry = _get_catalog().get(id)
    if entry is None:
        raise EntryNotFoundError(f"Tool '{id}' not found")
    return entry


@router.post("/search", response_model=list[ToolEntry])
async def search_tools(query: ToolQuery) -> list[ToolEntry]:
    """Search tool entries by query."""
    logger.debug("POST /api/tools/search")
    return _get_catalog().search(query)


@router.put("/{id}", response_model=ToolEntry)
async def update_tool(id: str, entry: ToolEntry) -> ToolEntry:
    """Update an existing tool entry."""
    logger.debug("PUT /api/tools/%s", id)
    _get_catalog().update(id, entry)
    return entry


@router.delete("/{id}", status_code=204)
async def delete_tool(id: str) -> Response:
    """Delete a tool entry."""
    logger.debug("DELETE /api/tools/%s", id)
    _get_catalog().delete(id)
    return Response(status_code=204)
