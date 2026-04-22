"""YAML-backed repository for team catalog entries."""

from __future__ import annotations

import builtins
import logging
from pathlib import Path
from typing import TYPE_CHECKING

from akgentic.catalog.models.team import TeamEntry, TeamMemberSpec
from akgentic.catalog.repositories.base import TeamCatalogRepository
from akgentic.catalog.repositories.yaml._base import YamlRepositoryBase

if TYPE_CHECKING:
    from akgentic.catalog.models.queries import TeamQuery

logger = logging.getLogger(__name__)

_list = builtins.list  # Alias: the repository's list() method shadows the built-in


def _agent_in_members(agent_id: str, members: _list[TeamMemberSpec]) -> bool:
    """Recursively check if agent_id appears anywhere in the members tree.

    Args:
        agent_id: The agent id to search for.
        members: The member tree to search.

    Returns:
        True if agent_id is found anywhere in the tree.
    """
    for member in members:
        if member.agent_id == agent_id:
            return True
        if member.members and _agent_in_members(agent_id, member.members):
            return True
    return False


class YamlTeamCatalogRepository(TeamCatalogRepository, YamlRepositoryBase[TeamEntry]):
    """YAML directory-backed team catalog repository."""

    _entry_type = TeamEntry

    def __init__(self, catalog_dir: Path) -> None:
        """Initialize with the directory containing team YAML files.

        Args:
            catalog_dir: Path to the directory of team catalog YAML files.
        """
        YamlRepositoryBase.__init__(self, catalog_dir)

    def create(self, team_entry: TeamEntry) -> str:
        """Persist a new team entry."""
        return YamlRepositoryBase.create(self, team_entry)

    def get(self, id: str) -> TeamEntry | None:
        """Retrieve a team entry by id."""
        return YamlRepositoryBase.get(self, id)

    def list(self) -> _list[TeamEntry]:
        """Return all team entries."""
        return YamlRepositoryBase.list(self)

    def update(self, id: str, team_entry: TeamEntry) -> None:
        """Update an existing team entry."""
        YamlRepositoryBase.update(self, id, team_entry)

    def delete(self, id: str) -> None:
        """Delete a team entry by id."""
        YamlRepositoryBase.delete(self, id)

    def search(self, query: TeamQuery) -> _list[TeamEntry]:
        """Filter teams by AND-ing all non-None query fields.

        Args:
            query: Query with optional filter fields.

        Returns:
            Matching team entries.
        """
        results: _list[TeamEntry] = []
        for entry in self._ensure_loaded():
            if query.id is not None and entry.id != query.id:
                continue
            if query.name is not None and query.name.lower() not in entry.name.lower():
                continue
            if (
                query.description is not None
                and query.description.lower() not in entry.description.lower()
            ):
                continue
            if query.agent_id is not None and not _agent_in_members(query.agent_id, entry.members):
                continue
            results.append(entry)
        return results
