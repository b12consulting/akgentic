"""Tests for ToolCatalog service."""

import pytest

from akgentic.catalog.models.errors import CatalogValidationError, EntryNotFoundError
from akgentic.catalog.models.queries import ToolQuery
from akgentic.catalog.models.tool import ToolEntry
from akgentic.catalog.services.tool_catalog import ToolCatalog
from tests.conftest import InMemoryToolCatalogRepository, MockAgentCatalog, make_agent, make_tool

# --- Fixtures ---


@pytest.fixture
def repo() -> InMemoryToolCatalogRepository:
    return InMemoryToolCatalogRepository()


@pytest.fixture
def catalog(repo: InMemoryToolCatalogRepository) -> ToolCatalog:
    return ToolCatalog(repository=repo)


class TestCreate:
    """AC #2: create with unique id persists, duplicate raises CatalogValidationError."""

    def test_create_unique_id_persists(self, catalog: ToolCatalog) -> None:
        entry = make_tool("search-1")
        result = catalog.create(entry)
        assert result == "search-1"
        assert catalog.get("search-1") is not None

    def test_create_duplicate_id_raises(self, catalog: ToolCatalog) -> None:
        entry = make_tool("search-1")
        catalog.create(entry)
        with pytest.raises(CatalogValidationError, match="already exists"):
            catalog.create(entry)


class TestGet:
    """AC #3: get delegates correctly."""

    def test_get_existing(self, catalog: ToolCatalog) -> None:
        entry = make_tool("search-1")
        catalog.create(entry)
        result = catalog.get("search-1")
        assert result is not None
        assert result.id == "search-1"

    def test_get_nonexistent_returns_none(self, catalog: ToolCatalog) -> None:
        assert catalog.get("nonexistent") is None


class TestList:
    """AC #3: list delegates correctly."""

    def test_list_delegates(self, catalog: ToolCatalog) -> None:
        catalog.create(make_tool("search-1"))
        catalog.create(make_tool("search-2"))
        result = catalog.list()
        assert len(result) == 2


class TestSearch:
    """AC #3: search delegates correctly."""

    def test_search_delegates(self, catalog: ToolCatalog) -> None:
        catalog.create(make_tool("search-1"))
        catalog.create(make_tool("search-2"))
        query = ToolQuery(id="search-1")
        result = catalog.search(query)
        assert len(result) == 1
        assert result[0].id == "search-1"


class TestUpdate:
    """AC #3: update with existing id delegates, nonexistent raises."""

    def test_update_existing_delegates(self, catalog: ToolCatalog) -> None:
        catalog.create(make_tool("search-1"))
        updated = make_tool("search-1")
        catalog.update("search-1", updated)
        result = catalog.get("search-1")
        assert result is not None

    def test_update_nonexistent_raises(self, catalog: ToolCatalog) -> None:
        entry = make_tool("search-1")
        with pytest.raises(EntryNotFoundError, match="not found"):
            catalog.update("search-1", entry)

    def test_update_id_mismatch_raises(self, catalog: ToolCatalog) -> None:
        catalog.create(make_tool("search-1"))
        mismatched = make_tool("search-2")
        with pytest.raises(CatalogValidationError, match="does not match"):
            catalog.update("search-1", mismatched)


class TestDelete:
    """AC #3: delete with existing id delegates, nonexistent raises."""

    def test_delete_existing_no_downstream(self, catalog: ToolCatalog) -> None:
        catalog.create(make_tool("search-1"))
        catalog.delete("search-1")
        assert catalog.get("search-1") is None

    def test_delete_nonexistent_raises(self, catalog: ToolCatalog) -> None:
        with pytest.raises(EntryNotFoundError, match="not found"):
            catalog.delete("nonexistent")


class TestAgentCatalogAttribute:
    """AC #3: agent_catalog defaults to None."""

    def test_defaults_to_none(self, catalog: ToolCatalog) -> None:
        assert catalog.agent_catalog is None

    def test_can_be_set(self, catalog: ToolCatalog) -> None:
        mock_ac = MockAgentCatalog([])
        catalog.agent_catalog = mock_ac
        assert catalog.agent_catalog is mock_ac


class TestDeleteProtection:
    """Delete protection when agent_catalog is set."""

    def test_delete_blocked_when_agent_references_tool(
        self, catalog: ToolCatalog
    ) -> None:
        catalog.create(make_tool("search-1"))
        agent = make_agent(id="agent-1", tool_ids=["search-1"])
        catalog.agent_catalog = MockAgentCatalog([agent])
        with pytest.raises(CatalogValidationError, match="cannot delete"):
            catalog.delete("search-1")

    def test_delete_allowed_when_agent_catalog_is_none(
        self, catalog: ToolCatalog
    ) -> None:
        catalog.create(make_tool("search-1"))
        catalog.delete("search-1")
        assert catalog.get("search-1") is None

    def test_delete_allowed_when_no_agent_references_tool(
        self, catalog: ToolCatalog
    ) -> None:
        catalog.create(make_tool("search-1"))
        agent = make_agent(id="agent-1", tool_ids=["other-tool"])
        catalog.agent_catalog = MockAgentCatalog([agent])
        catalog.delete("search-1")
        assert catalog.get("search-1") is None

    def test_delete_blocked_multiple_agents_reference_tool(
        self, catalog: ToolCatalog
    ) -> None:
        catalog.create(make_tool("search-1"))
        agent1 = make_agent(id="agent-1", tool_ids=["search-1"])
        agent2 = make_agent(id="agent-2", tool_ids=["search-1"])
        catalog.agent_catalog = MockAgentCatalog([agent1, agent2])
        with pytest.raises(CatalogValidationError) as exc_info:
            catalog.delete("search-1")
        assert len(exc_info.value.errors) == 2


class TestValidateCreate:
    """validate_create returns list[str], not raises."""

    def test_returns_empty_for_unique(self, catalog: ToolCatalog) -> None:
        entry = make_tool("search-1")
        errors = catalog.validate_create(entry)
        assert errors == []

    def test_returns_errors_for_duplicate(self, catalog: ToolCatalog) -> None:
        entry = make_tool("search-1")
        catalog.create(entry)
        errors = catalog.validate_create(entry)
        assert len(errors) == 1
        assert "already exists" in errors[0]


class TestValidateDelete:
    """validate_delete returns list[str] for downstream checks."""

    def test_returns_empty_when_no_refs(self, catalog: ToolCatalog) -> None:
        catalog.create(make_tool("search-1"))
        errors = catalog.validate_delete("search-1")
        assert errors == []

    def test_returns_errors_when_referenced(self, catalog: ToolCatalog) -> None:
        catalog.create(make_tool("search-1"))
        agent = make_agent(id="agent-1", tool_ids=["search-1"])
        catalog.agent_catalog = MockAgentCatalog([agent])
        errors = catalog.validate_delete("search-1")
        assert len(errors) == 1
        assert "cannot delete" in errors[0]

    def test_returns_not_found_error(self, catalog: ToolCatalog) -> None:
        errors = catalog.validate_delete("nonexistent")
        assert len(errors) == 1
        assert "not found" in errors[0]


class TestCatalogStoresPlaceholdersAsIs:
    """AC5: Catalog stores ${VAR} placeholders as-is, not resolved at persistence time."""

    def test_tool_entry_with_env_var_placeholder_persists_unchanged(self) -> None:
        """Create a ToolEntry with ${VAR} in a string field, persist via repo, verify unchanged."""
        entry = ToolEntry(
            id="figma-tool",
            tool_class="akgentic.tool.search.SearchTool",
            tool={"name": "Figma", "description": "Figma API with key ${FIGMA_API_KEY}"},
        )

        repo = InMemoryToolCatalogRepository()
        catalog = ToolCatalog(repository=repo)
        catalog.create(entry)

        retrieved = catalog.get("figma-tool")
        assert retrieved is not None
        assert retrieved.tool.description == "Figma API with key ${FIGMA_API_KEY}"
