"""YAML-backed repository for agent catalog entries."""

from __future__ import annotations

import builtins
import logging
from pathlib import Path
from typing import TYPE_CHECKING

from akgentic.catalog.models.agent import AgentEntry
from akgentic.catalog.repositories.base import AgentCatalogRepository
from akgentic.catalog.repositories.yaml._base import YamlRepositoryBase

if TYPE_CHECKING:
    from akgentic.catalog.models.queries import AgentQuery

logger = logging.getLogger(__name__)

_list = builtins.list  # Alias: the repository's list() method shadows the built-in


class YamlAgentCatalogRepository(AgentCatalogRepository, YamlRepositoryBase[AgentEntry]):
    """YAML directory-backed agent catalog repository."""

    _entry_type = AgentEntry

    def __init__(self, catalog_dir: Path) -> None:
        """Initialize with the directory containing agent YAML files.

        Args:
            catalog_dir: Path to the directory of agent catalog YAML files.
        """
        YamlRepositoryBase.__init__(self, catalog_dir)

    def create(self, agent_entry: AgentEntry) -> str:
        """Persist a new agent entry."""
        return YamlRepositoryBase.create(self, agent_entry)

    def get(self, id: str) -> AgentEntry | None:
        """Retrieve an agent entry by id."""
        return YamlRepositoryBase.get(self, id)

    def list(self) -> _list[AgentEntry]:
        """Return all agent entries."""
        return YamlRepositoryBase.list(self)

    def update(self, id: str, agent_entry: AgentEntry) -> None:
        """Update an existing agent entry."""
        YamlRepositoryBase.update(self, id, agent_entry)

    def delete(self, id: str) -> None:
        """Delete an agent entry by id."""
        YamlRepositoryBase.delete(self, id)

    def search(self, query: AgentQuery) -> _list[AgentEntry]:
        """Filter agents by AND-ing all non-None query fields.

        Args:
            query: Query with optional filter fields.

        Returns:
            Matching agent entries.
        """
        results: _list[AgentEntry] = []
        for entry in self._ensure_loaded():
            if query.id is not None and entry.id != query.id:
                continue
            if query.role is not None and entry.card.role != query.role:
                continue
            if query.skills is not None and not (set(query.skills) & set(entry.card.skills)):
                continue
            if (
                query.description is not None
                and query.description.lower() not in entry.card.description.lower()
            ):
                continue
            results.append(entry)
        return results
