"""TeamCatalog service — domain logic over team repository with cross-validation."""

from __future__ import annotations

import builtins
import logging
from typing import TYPE_CHECKING

from akgentic.catalog.models.errors import CatalogValidationError, EntryNotFoundError
from akgentic.catalog.models.team import TeamEntry, TeamMemberSpec, agent_in_members
from akgentic.catalog.repositories.base import TeamCatalogRepository
from akgentic.core.utils.deserializer import import_class

if TYPE_CHECKING:
    from akgentic.catalog.models.queries import TeamQuery
    from akgentic.catalog.services.agent_catalog import AgentCatalog

__all__ = ["TeamCatalog"]

logger = logging.getLogger(__name__)

_list = builtins.list  # Alias: the service's list() method shadows the built-in


def _collect_agent_ids(members: list[TeamMemberSpec]) -> list[str]:
    """Recursively collect all agent_ids from the members tree.

    Args:
        members: The member tree to traverse.

    Returns:
        Flat list of all agent_ids found in the tree.
    """
    ids: list[str] = []
    for m in members:
        ids.append(m.agent_id)
        if m.members:
            ids.extend(_collect_agent_ids(m.members))
    return ids


class TeamCatalog:
    """Service layer for team catalog entries with cross-validation."""

    # --- Initialization ---

    def __init__(
        self,
        repository: TeamCatalogRepository,
        agent_catalog: AgentCatalog,
    ) -> None:
        """Initialize with repository and agent catalog for cross-validation.

        Args:
            repository: Storage backend for team entries.
            agent_catalog: For validating member and profile agent references.
        """
        self.repository = repository
        self._agent_catalog = agent_catalog

    # --- Validation ---

    def _validate_entry(
        self,
        entry: TeamEntry,
        *,
        exclude_id: str | None = None,
    ) -> _list[str]:
        """Cross-validate a team entry. Shared by create and update.

        Args:
            entry: The team entry to validate.
            exclude_id: If set, skip the duplicate-id check for this id (used by update).

        Returns:
            List of validation error strings (empty if valid).
        """
        errors: _list[str] = []

        # AC5: Duplicate ID rejection (skipped when updating own entry)
        if exclude_id != entry.id and self.repository.get(entry.id) is not None:
            errors.append(f"Team id '{entry.id}' already exists")

        # AC2: entry_point must be an agent_id in the members tree
        if not agent_in_members(entry.entry_point, entry.members):
            errors.append(f"Entry point '{entry.entry_point}' not found in members tree")

        # AC1/AC15: Every agent_id in members tree must exist in AgentCatalog
        for agent_id in _collect_agent_ids(entry.members):
            if self._agent_catalog.get(agent_id) is None:
                errors.append(f"Agent '{agent_id}' not found in AgentCatalog")

        # AC3: Every agent_id in agent_profiles must exist in AgentCatalog
        for agent_id in entry.agent_profiles:
            if self._agent_catalog.get(agent_id) is None:
                errors.append(f"Profile agent '{agent_id}' not found in AgentCatalog")

        # AC4: Every message_type must be resolvable
        for mt in entry.message_types:
            try:
                import_class(mt)
            except (ImportError, AttributeError, ValueError) as e:
                errors.append(f"Cannot resolve message_type '{mt}': {e}")

        return errors

    def validate_create(self, entry: TeamEntry) -> _list[str]:
        """Check duplicate id, entry_point, member agents, profiles, and message types.

        Args:
            entry: The team entry to validate.

        Returns:
            List of validation error strings (empty if valid).
        """
        return self._validate_entry(entry)

    # --- CRUD Operations ---

    def create(self, entry: TeamEntry) -> str:
        """Persist a new team entry.

        Args:
            entry: The team entry to create.

        Returns:
            The id of the created entry.

        Raises:
            CatalogValidationError: If cross-validation fails.
        """
        logger.debug("creating team %s", entry.id)
        errors = self.validate_create(entry)
        if errors:
            raise CatalogValidationError(errors)
        result = self.repository.create(entry)
        logger.info("team created: %s", entry.id)
        return result

    def get(self, id: str) -> TeamEntry | None:
        """Retrieve a team entry by id.

        Args:
            id: The team entry id.

        Returns:
            The team entry, or None if not found.
        """
        return self.repository.get(id)

    def list(self) -> _list[TeamEntry]:
        """List all team entries.

        Returns:
            All team entries in the repository.
        """
        return self.repository.list()

    def search(self, query: TeamQuery) -> _list[TeamEntry]:
        """Search team entries by query.

        Args:
            query: Query with optional filter fields.

        Returns:
            Matching team entries.
        """
        return self.repository.search(query)

    def update(self, id: str, entry: TeamEntry) -> None:
        """Update an existing team entry.

        Args:
            id: The id of the entry to update.
            entry: The new team entry data.

        Raises:
            EntryNotFoundError: If no entry with the given id exists.
            CatalogValidationError: If id mismatch or cross-validation fails.
        """
        logger.debug("updating team %s", id)
        if self.repository.get(id) is None:
            raise EntryNotFoundError(f"Team id '{id}' not found")
        if entry.id != id:
            raise CatalogValidationError(
                [f"Entry id '{entry.id}' does not match update target '{id}'"]
            )
        errors = self._validate_entry(entry, exclude_id=id)
        if errors:
            raise CatalogValidationError(errors)
        self.repository.update(id, entry)
        logger.info("team updated: %s", id)

    # --- Delete Protection ---

    def validate_delete(self, id: str) -> _list[str]:
        """Check existence before delete. No downstream refs for teams (v1: D9).

        Args:
            id: The team entry id to validate for deletion.

        Returns:
            List of validation error strings (empty if safe to delete).
        """
        errors: _list[str] = []
        if self.repository.get(id) is None:
            errors.append(f"Team id '{id}' not found")
        return errors

    def delete(self, id: str) -> None:
        """Delete a team entry.

        Args:
            id: The id of the entry to delete.

        Raises:
            EntryNotFoundError: If no entry with the given id exists.
        """
        logger.debug("deleting team %s", id)
        errors = self.validate_delete(id)
        if errors:
            raise EntryNotFoundError(errors[0])
        self.repository.delete(id)
        logger.info("team deleted: %s", id)
