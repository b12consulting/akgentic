"""TemplateCatalog service — domain logic over template repository with delete protection."""

from __future__ import annotations

import builtins
import logging
from typing import TYPE_CHECKING

from akgentic.catalog.models.errors import CatalogValidationError, EntryNotFoundError
from akgentic.catalog.models.template import TemplateEntry
from akgentic.catalog.refs import _is_catalog_ref, _resolve_ref
from akgentic.catalog.repositories.base import TemplateCatalogRepository

if TYPE_CHECKING:
    from akgentic.catalog.models.queries import TemplateQuery
    from akgentic.catalog.services.agent_catalog import AgentCatalog

__all__ = ["TemplateCatalog"]

logger = logging.getLogger(__name__)

_list = builtins.list  # Alias: the service's list() method shadows the built-in


class TemplateCatalog:
    """Service layer for template catalog entries with delete protection."""

    # --- Initialization ---

    def __init__(self, repository: TemplateCatalogRepository) -> None:
        """Initialize with repository for template entry storage.

        Args:
            repository: Storage backend for template entries.
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

    def validate_create(self, entry: TemplateEntry) -> _list[str]:
        """Check for duplicate id.

        Args:
            entry: The template entry to validate.

        Returns:
            List of validation error strings (empty if valid).
        """
        errors: _list[str] = []
        if self.repository.get(entry.id) is not None:
            errors.append(f"Template id '{entry.id}' already exists")
        return errors

    def create(self, entry: TemplateEntry) -> str:
        """Persist a new template entry.

        Args:
            entry: The template entry to create.

        Returns:
            The id of the created entry.

        Raises:
            CatalogValidationError: If an entry with the same id already exists.
        """
        logger.debug("creating template %s", entry.id)
        errors = self.validate_create(entry)
        if errors:
            raise CatalogValidationError(errors)
        result = self.repository.create(entry)
        logger.info("template created: %s", entry.id)
        return result

    def get(self, template_id: str) -> TemplateEntry | None:
        """Retrieve a template entry by id.

        Args:
            template_id: The template entry id.

        Returns:
            The template entry, or None if not found.
        """
        return self.repository.get(template_id)

    def list(self) -> _list[TemplateEntry]:
        """List all template entries.

        Returns:
            All template entries in the repository.
        """
        return self.repository.list()

    def search(self, query: TemplateQuery) -> _list[TemplateEntry]:
        """Search template entries by query.

        Args:
            query: Query with optional filter fields.

        Returns:
            Matching template entries.
        """
        return self.repository.search(query)

    def update(self, id: str, entry: TemplateEntry) -> None:
        """Update an existing template entry.

        Args:
            id: The id of the entry to update.
            entry: The new template entry data.

        Raises:
            EntryNotFoundError: If no entry with the given id exists.
            CatalogValidationError: If the entry id does not match the update target.
        """
        logger.debug("updating template %s", id)
        if self.repository.get(id) is None:
            raise EntryNotFoundError(f"Template id '{id}' not found")
        if entry.id != id:
            raise CatalogValidationError(
                [f"Entry id '{entry.id}' does not match update target '{id}'"]
            )
        self.repository.update(id, entry)
        logger.info("template updated: %s", id)

    # --- Delete Protection ---

    def validate_delete(self, id: str) -> _list[str]:
        """Check existence and downstream references before delete.

        Args:
            id: The template entry id to validate for deletion.

        Returns:
            List of validation error strings (empty if safe to delete).
        """
        errors: _list[str] = []
        if self.repository.get(id) is None:
            errors.append(f"Template id '{id}' not found")
            return errors
        if self._agent_catalog is not None:
            for agent in self._agent_catalog.repository.list():
                config = agent.card.config
                if hasattr(config, "prompt"):
                    prompt = config.prompt  # ADR-003: duck-type gate
                    if _is_catalog_ref(prompt.template):
                        ref_id = _resolve_ref(prompt.template)
                        if ref_id == id:
                            errors.append(
                                f"Agent '{agent.id}' references template '@{id}' — cannot delete"
                            )
        return errors

    def delete(self, id: str) -> None:
        """Delete a template entry.

        Args:
            id: The id of the entry to delete.

        Raises:
            EntryNotFoundError: If no entry with the given id exists.
            CatalogValidationError: If the template is referenced by agents.
        """
        logger.debug("deleting template %s", id)
        errors = self.validate_delete(id)
        if errors:
            if "not found" in errors[0]:
                raise EntryNotFoundError(errors[0])
            raise CatalogValidationError(errors)
        self.repository.delete(id)
        logger.info("template deleted: %s", id)
