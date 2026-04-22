"""FastAPI router for team catalog CRUD and search operations."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from fastapi import APIRouter, Response

from akgentic.catalog.models.errors import EntryNotFoundError
from akgentic.catalog.models.queries import TeamQuery
from akgentic.catalog.models.team import TeamEntry

if TYPE_CHECKING:
    from akgentic.catalog.services.team_catalog import TeamCatalog

__all__ = ["router", "set_catalog"]

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/teams", tags=["teams"])

_catalog: TeamCatalog | None = None


def set_catalog(catalog: TeamCatalog) -> None:
    """Inject the team catalog service instance.

    Args:
        catalog: The TeamCatalog service to use for all operations.
    """
    global _catalog  # noqa: PLW0603
    _catalog = catalog


def _get_catalog() -> TeamCatalog:
    """Return the injected catalog or raise if not configured."""
    if _catalog is None:
        raise RuntimeError("TeamCatalog not configured — call set_catalog() first")
    return _catalog


@router.post("", response_model=TeamEntry, status_code=201)
async def create_team(entry: TeamEntry) -> TeamEntry:
    """Create a new team entry."""
    logger.debug("POST /api/teams — creating %s", entry.id)
    _get_catalog().create(entry)
    return entry


@router.get("", response_model=list[TeamEntry])
async def list_teams() -> list[TeamEntry]:
    """List all team entries."""
    logger.debug("GET /api/teams — listing all")
    return _get_catalog().list()


@router.get("/{id}", response_model=TeamEntry)
async def get_team(id: str) -> TeamEntry:
    """Get a team entry by id."""
    logger.debug("GET /api/teams/%s", id)
    entry = _get_catalog().get(id)
    if entry is None:
        raise EntryNotFoundError(f"Team '{id}' not found")
    return entry


@router.post("/search", response_model=list[TeamEntry])
async def search_teams(query: TeamQuery) -> list[TeamEntry]:
    """Search team entries by query."""
    logger.debug("POST /api/teams/search")
    return _get_catalog().search(query)


@router.put("/{id}", response_model=TeamEntry)
async def update_team(id: str, entry: TeamEntry) -> TeamEntry:
    """Update an existing team entry."""
    logger.debug("PUT /api/teams/%s", id)
    _get_catalog().update(id, entry)
    return entry


@router.delete("/{id}", status_code=204)
async def delete_team(id: str) -> Response:
    """Delete a team entry."""
    logger.debug("DELETE /api/teams/%s", id)
    _get_catalog().delete(id)
    return Response(status_code=204)
