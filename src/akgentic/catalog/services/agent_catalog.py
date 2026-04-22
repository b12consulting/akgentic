"""AgentCatalog service — domain logic over agent repository with cross-validation."""

from __future__ import annotations

import builtins
import logging
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from akgentic.catalog.models.agent import AgentEntry
from akgentic.catalog.models.errors import CatalogValidationError, EntryNotFoundError
from akgentic.catalog.models.team import TeamEntry, agent_in_members
from akgentic.catalog.refs import _is_catalog_ref, _resolve_ref
from akgentic.catalog.repositories.base import AgentCatalogRepository
from akgentic.catalog.services.template_catalog import TemplateCatalog
from akgentic.catalog.services.tool_catalog import ToolCatalog

if TYPE_CHECKING:
    from akgentic.catalog.models.queries import AgentQuery

__all__ = ["AgentCatalog"]

logger = logging.getLogger(__name__)

_list = builtins.list  # Alias: the service's list() method shadows the built-in


@runtime_checkable
class _TeamCatalogProtocol(Protocol):
    """Structural type for team catalog (avoids circular import with TeamCatalog)."""

    def list(self) -> _list[TeamEntry]:
        """List all team entries."""
        ...


class AgentCatalog:
    """Service layer for agent catalog entries with cross-validation."""

    # --- Initialization ---

    def __init__(
        self,
        repository: AgentCatalogRepository,
        template_catalog: TemplateCatalog,
        tool_catalog: ToolCatalog,
    ) -> None:
        """Initialize with repository and upstream catalogs for cross-validation.

        Args:
            repository: Storage backend for agent entries.
            template_catalog: For validating @-reference prompts.
            tool_catalog: For validating tool_ids references.
        """
        self.repository = repository
        self._template_catalog = template_catalog
        self._tool_catalog = tool_catalog
        self._team_catalog: _TeamCatalogProtocol | None = None

    @property
    def team_catalog(self) -> _TeamCatalogProtocol | None:
        """Optional downstream team catalog for delete protection."""
        return self._team_catalog

    @team_catalog.setter
    def team_catalog(self, value: _TeamCatalogProtocol | None) -> None:
        """Set the downstream team catalog for delete protection."""
        self._team_catalog = value

    # --- Validation ---

    def _validate_entry(
        self,
        entry: AgentEntry,
        pending_names: set[str] | None = None,
        *,
        exclude_id: str | None = None,
    ) -> _list[str]:
        """Cross-validate an agent entry. Shared by create and update.

        Args:
            entry: The agent entry to validate.
            pending_names: Names treated as valid route targets for batch loading.
            exclude_id: If set, skip the duplicate-id check for this id (used by update).

        Returns:
            List of validation error strings (empty if valid).
        """
        errors: _list[str] = []

        # AC8: Duplicate ID rejection (skipped when updating own entry)
        if exclude_id != entry.id and self.repository.get(entry.id) is not None:
            errors.append(f"Agent id '{entry.id}' already exists")

        # AC1: Tool reference validation
        for tool_id in entry.tool_ids:
            if self._tool_catalog.get(tool_id) is None:
                errors.append(f"Tool '{tool_id}' not found in ToolCatalog")

        # AC2: Template @-reference validation
        config = entry.card.config
        if hasattr(config, "prompt"):
            prompt = config.prompt  # ADR-003: duck-type gate
            if _is_catalog_ref(prompt.template):
                template_id = _resolve_ref(prompt.template)
                tpl_entry = self._template_catalog.get(template_id)
                if tpl_entry is None:
                    errors.append(f"Template '@{template_id}' not found in TemplateCatalog")
                else:
                    expected = set(tpl_entry.placeholders)
                    actual = set(prompt.params.keys())
                    missing = expected - actual
                    extra = actual - expected
                    if missing:
                        errors.append(
                            f"Template '@{template_id}' missing params: "
                            f"{', '.join(sorted(missing))}"
                        )
                    if extra:
                        errors.append(
                            f"Template '@{template_id}' extra params: {', '.join(sorted(extra))}"
                        )

        # AC3/AC4: Route target validation
        valid_names = pending_names or set()
        existing_names = {a.card.config.name for a in self.repository.list()}
        for route_name in entry.card.routes_to:
            if route_name not in existing_names and route_name not in valid_names:
                errors.append(f"Route target '{route_name}' not found in AgentCatalog")

        return errors

    def validate_create(
        self,
        entry: AgentEntry,
        pending_names: set[str] | None = None,
    ) -> _list[str]:
        """Check duplicate id, tool refs, template @-refs, and route targets.

        Args:
            entry: The agent entry to validate.
            pending_names: Names treated as valid route targets for batch loading.

        Returns:
            List of validation error strings (empty if valid).
        """
        return self._validate_entry(entry, pending_names)

    # --- CRUD Operations ---

    def create(
        self,
        entry: AgentEntry,
        pending_names: set[str] | None = None,
    ) -> str:
        """Persist a new agent entry.

        Args:
            entry: The agent entry to create.
            pending_names: Names treated as valid route targets for batch loading.

        Returns:
            The id of the created entry.

        Raises:
            CatalogValidationError: If cross-validation fails.
        """
        logger.debug("creating agent %s", entry.id)
        errors = self.validate_create(entry, pending_names)
        if errors:
            raise CatalogValidationError(errors)
        result = self.repository.create(entry)
        logger.info("agent created: %s", entry.id)
        return result

    def get(self, id: str) -> AgentEntry | None:
        """Retrieve an agent entry by id.

        Args:
            id: The agent entry id.

        Returns:
            The agent entry, or None if not found.
        """
        return self.repository.get(id)

    def list(self) -> _list[AgentEntry]:
        """List all agent entries.

        Returns:
            All agent entries in the repository.
        """
        return self.repository.list()

    def search(self, query: AgentQuery) -> _list[AgentEntry]:
        """Search agent entries by query.

        Args:
            query: Query with optional filter fields.

        Returns:
            Matching agent entries.
        """
        return self.repository.search(query)

    def update(
        self,
        id: str,
        entry: AgentEntry,
        pending_names: set[str] | None = None,
    ) -> None:
        """Update an existing agent entry.

        Args:
            id: The id of the entry to update.
            entry: The new agent entry data.
            pending_names: Names treated as valid route targets for batch loading.

        Raises:
            EntryNotFoundError: If no entry with the given id exists.
            CatalogValidationError: If id mismatch or cross-validation fails.
        """
        logger.debug("updating agent %s", id)
        if self.repository.get(id) is None:
            raise EntryNotFoundError(f"Agent id '{id}' not found")
        if entry.id != id:
            raise CatalogValidationError(
                [f"Entry id '{entry.id}' does not match update target '{id}'"]
            )
        errors = self._validate_entry(entry, pending_names, exclude_id=id)
        if errors:
            raise CatalogValidationError(errors)
        self.repository.update(id, entry)
        logger.info("agent updated: %s", id)

    # --- Delete Protection ---

    def validate_delete(self, id: str) -> _list[str]:
        """Check existence, routing deps, and team refs before delete.

        Args:
            id: The agent entry id to validate for deletion.

        Returns:
            List of validation error strings (empty if safe to delete).
        """
        errors: _list[str] = []
        agent = self.repository.get(id)
        if agent is None:
            errors.append(f"Agent id '{id}' not found")
            return errors

        agent_name = agent.card.config.name

        # Check routing dependencies: no other agent routes to this agent's name
        for other in self.repository.list():
            if other.id == id:
                continue
            if agent_name in other.card.routes_to:
                errors.append(f"Agent '{other.id}' routes to '{agent_name}' — cannot delete")

        # Check downstream TeamCatalog references (only when wired)
        if self._team_catalog is not None:
            for team in self._team_catalog.list():
                if agent_in_members(id, team.members):
                    errors.append(
                        f"Team '{team.id}' references agent '{id}' in members — cannot delete"
                    )
                if id in team.agent_profiles:
                    errors.append(
                        f"Team '{team.id}' references agent '{id}' in profiles — cannot delete"
                    )

        return errors

    def delete(self, id: str) -> None:
        """Delete an agent entry.

        Args:
            id: The id of the entry to delete.

        Raises:
            EntryNotFoundError: If no entry with the given id exists.
            CatalogValidationError: If the agent is referenced by other entries.
        """
        logger.debug("deleting agent %s", id)
        errors = self.validate_delete(id)
        if errors:
            if "not found" in errors[0]:
                raise EntryNotFoundError(errors[0])
            raise CatalogValidationError(errors)
        self.repository.delete(id)
        logger.info("agent deleted: %s", id)
