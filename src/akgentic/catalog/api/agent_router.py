"""FastAPI router for agent catalog CRUD and search operations."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from fastapi import APIRouter, Response

from akgentic.catalog.models.agent import AgentEntry
from akgentic.catalog.models.errors import EntryNotFoundError
from akgentic.catalog.models.queries import AgentQuery

if TYPE_CHECKING:
    from akgentic.catalog.services.agent_catalog import AgentCatalog

__all__ = ["router", "set_catalog"]

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/agents", tags=["agents"])

_catalog: AgentCatalog | None = None


def set_catalog(catalog: AgentCatalog) -> None:
    """Inject the agent catalog service instance.

    Args:
        catalog: The AgentCatalog service to use for all operations.
    """
    global _catalog  # noqa: PLW0603
    _catalog = catalog


def _get_catalog() -> AgentCatalog:
    """Return the injected catalog or raise if not configured."""
    if _catalog is None:
        raise RuntimeError("AgentCatalog not configured — call set_catalog() first")
    return _catalog


@router.post("", response_model=AgentEntry, status_code=201)
async def create_agent(entry: AgentEntry) -> AgentEntry:
    """Create a new agent entry."""
    logger.debug("POST /api/agents — creating %s", entry.id)
    _get_catalog().create(entry)
    return entry


@router.get("", response_model=list[AgentEntry])
async def list_agents() -> list[AgentEntry]:
    """List all agent entries."""
    logger.debug("GET /api/agents — listing all")
    return _get_catalog().list()


@router.get("/{id}", response_model=AgentEntry)
async def get_agent(id: str) -> AgentEntry:
    """Get an agent entry by id."""
    logger.debug("GET /api/agents/%s", id)
    entry = _get_catalog().get(id)
    if entry is None:
        raise EntryNotFoundError(f"Agent '{id}' not found")
    return entry


@router.post("/search", response_model=list[AgentEntry])
async def search_agents(query: AgentQuery) -> list[AgentEntry]:
    """Search agent entries by query."""
    logger.debug("POST /api/agents/search")
    return _get_catalog().search(query)


@router.put("/{id}", response_model=AgentEntry)
async def update_agent(id: str, entry: AgentEntry) -> AgentEntry:
    """Update an existing agent entry."""
    logger.debug("PUT /api/agents/%s", id)
    _get_catalog().update(id, entry)
    return entry


@router.delete("/{id}", status_code=204)
async def delete_agent(id: str) -> Response:
    """Delete an agent entry."""
    logger.debug("DELETE /api/agents/%s", id)
    _get_catalog().delete(id)
    return Response(status_code=204)
