"""ToolCatalog service — domain logic over tool repository with delete protection."""

from __future__ import annotations

import builtins
import logging
from typing import TYPE_CHECKING

from akgentic.catalog.models.errors import CatalogValidationError, EntryNotFoundError
from akgentic.catalog.models.tool import ToolEntry
from akgentic.catalog.repositories.base import ToolCatalogRepository

if TYPE_CHECKING:
    from akgentic.catalog.models.queries import ToolQuery
    from akgentic.catalog.services.agent_catalog import AgentCatalog

__all__ = ["ToolCatalog"]

logger = logging.getLogger(__name__)

_list = builtins.list  # Alias: the service's list() method shadows the built-in


class ToolCatalog:
    """Service layer for tool catalog entries with delete protection."""

    # --- Initialization ---

    def __init__(self, repository: ToolCatalogRepository) -> None:
        """Initialize with repository for tool entry storage.

        Args:
            repository: Storage backend for tool entries.
        """
        self.repository = repository
        self._agent_catalog: AgentCatalog | None = None

    @property
    def agent_catalog(self) -> AgentCatalog | None:
        """Optional downstream agent catalog for delete protection."""
        return self._agent_catalog

    @agent_catalog.setter
    def agent_catalog(self, value: AgentCatalog | None) -> None:
        """Set the downstream agent catalog for delete protection."""
        self._agent_catalog = value

    # --- CRUD Operations ---

    def validate_create(self, entry: ToolEntry) -> _list[str]:
        """Check for duplicate id.

        Args:
            entry: The tool entry to validate.

        Returns:
            List of validation error strings (empty if valid).
        """
        errors: _list[str] = []
        if self.repository.get(entry.id) is not None:
            errors.append(f"Tool id '{entry.id}' already exists")
        return errors

    def create(self, entry: ToolEntry) -> str:
        """Persist a new tool entry.

        Args:
            entry: The tool entry to create.

        Returns:
            The id of the created entry.

        Raises:
            CatalogValidationError: If an entry with the same id already exists.
        """
        logger.debug("creating tool %s", entry.id)
        errors = self.validate_create(entry)
        if errors:
            raise CatalogValidationError(errors)
        result = self.repository.create(entry)
        logger.info("tool created: %s", entry.id)
        return result

    def get(self, tool_id: str) -> ToolEntry | None:
        """Retrieve a tool entry by id.

        Args:
            tool_id: The tool entry id.

        Returns:
            The tool entry, or None if not found.
        """
        return self.repository.get(tool_id)

    def list(self) -> _list[ToolEntry]:
        """List all tool entries.

        Returns:
            All tool entries in the repository.
        """
        return self.repository.list()

    def search(self, query: ToolQuery) -> _list[ToolEntry]:
        """Search tool entries by query.

        Args:
            query: Query with optional filter fields.

        Returns:
            Matching tool entries.
        """
        return self.repository.search(query)

    def update(self, id: str, entry: ToolEntry) -> None:
        """Update an existing tool entry.

        Args:
            id: The id of the entry to update.
            entry: The new tool entry data.

        Raises:
            EntryNotFoundError: If no entry with the given id exists.
            CatalogValidationError: If the entry id does not match the update target.
        """
        logger.debug("updating tool %s", id)
        if self.repository.get(id) is None:
            raise EntryNotFoundError(f"Tool id '{id}' not found")
        if entry.id != id:
            raise CatalogValidationError(
                [f"Entry id '{entry.id}' does not match update target '{id}'"]
            )
        self.repository.update(id, entry)
        logger.info("tool updated: %s", id)

    # --- Delete Protection ---

    def validate_delete(self, id: str) -> _list[str]:
        """Check existence and downstream references before delete.

        Args:
            id: The tool entry id to validate for deletion.

        Returns:
            List of validation error strings (empty if safe to delete).
        """
        errors: _list[str] = []
        if self.repository.get(id) is None:
            errors.append(f"Tool id '{id}' not found")
            return errors
        if self._agent_catalog is not None:
            for agent in self._agent_catalog.repository.list():
                if id in agent.tool_ids:
                    errors.append(
                        f"Agent '{agent.id}' references tool '{id}' in tool_ids — cannot delete"
                    )
        return errors

    def delete(self, id: str) -> None:
        """Delete a tool entry.

        Args:
            id: The id of the entry to delete.

        Raises:
            EntryNotFoundError: If no entry with the given id exists.
            CatalogValidationError: If the tool is referenced by agents.
        """
        logger.debug("deleting tool %s", id)
        errors = self.validate_delete(id)
        if errors:
            if "not found" in errors[0]:
                raise EntryNotFoundError(errors[0])
            raise CatalogValidationError(errors)
        self.repository.delete(id)
        logger.info("tool deleted: %s", id)
