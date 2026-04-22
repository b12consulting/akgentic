"""Tests for MongoToolCatalogRepository."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from akgentic.catalog.models.errors import CatalogValidationError, EntryNotFoundError
from akgentic.catalog.models.queries import ToolQuery
from akgentic.catalog.repositories.mongo.tool_repo import MongoToolCatalogRepository
from tests.conftest import make_tool

if TYPE_CHECKING:
    import pymongo.collection


@pytest.fixture
def repo(tool_collection: pymongo.collection.Collection) -> MongoToolCatalogRepository:  # type: ignore[type-arg]
    """Provide a MongoToolCatalogRepository backed by mongomock."""
    return MongoToolCatalogRepository(tool_collection)


class TestCreateAndGet:
    """Test create + get round-trip."""

    def test_create_and_get_round_trip(self, repo: MongoToolCatalogRepository) -> None:
        """Create an entry and retrieve it by id."""
        entry = make_tool(id="tool-1", name="Search", description="Web search tool")
        created_id = repo.create(entry)
        assert created_id == "tool-1"

        retrieved = repo.get("tool-1")
        assert retrieved is not None
        assert retrieved.id == "tool-1"
        assert retrieved.tool.name == "Search"
        assert retrieved.tool.description == "Web search tool"

    def test_create_duplicate_raises_validation_error(
        self, repo: MongoToolCatalogRepository
    ) -> None:
        """Creating an entry with an existing id raises CatalogValidationError."""
        entry = make_tool(id="dup-1")
        repo.create(entry)

        with pytest.raises(CatalogValidationError, match="already exists"):
            repo.create(entry)

    def test_get_nonexistent_returns_none(self, repo: MongoToolCatalogRepository) -> None:
        """Getting a nonexistent id returns None."""
        assert repo.get("nonexistent") is None


class TestList:
    """Test list returns all entries."""

    def test_list_empty(self, repo: MongoToolCatalogRepository) -> None:
        """Empty collection returns empty list."""
        assert repo.list() == []

    def test_list_returns_all_entries(self, repo: MongoToolCatalogRepository) -> None:
        """List returns all created entries."""
        repo.create(make_tool(id="tool-a", name="Alpha"))
        repo.create(make_tool(id="tool-b", name="Beta"))
        repo.create(make_tool(id="tool-c", name="Gamma"))

        results = repo.list()
        assert len(results) == 3
        ids = {e.id for e in results}
        assert ids == {"tool-a", "tool-b", "tool-c"}


class TestSearch:
    """Test search with various query combinations."""

    def test_search_by_id_exact_match(self, repo: MongoToolCatalogRepository) -> None:
        """Search by id returns exact match only."""
        repo.create(make_tool(id="t1", name="Alpha"))
        repo.create(make_tool(id="t2", name="Beta"))

        results = repo.search(ToolQuery(id="t1"))
        assert len(results) == 1
        assert results[0].id == "t1"

    def test_search_by_tool_class_exact_match(self, repo: MongoToolCatalogRepository) -> None:
        """Search by tool_class returns exact matches only."""
        repo.create(make_tool(id="t1", tool_class="akgentic.tool.search.SearchTool"))
        repo.create(
            make_tool(
                id="t2",
                tool_class="akgentic.tool.planning.PlanningTool",
                name="planner",
                description="Planning tool",
            )
        )

        results = repo.search(ToolQuery(tool_class="akgentic.tool.planning.PlanningTool"))
        assert len(results) == 1
        assert results[0].id == "t2"

    def test_search_by_name_case_insensitive_substring(
        self, repo: MongoToolCatalogRepository
    ) -> None:
        """Search by name uses case-insensitive substring matching."""
        repo.create(make_tool(id="t1", name="Web Search Tool"))
        repo.create(make_tool(id="t2", name="Calculator"))
        repo.create(make_tool(id="t3", name="Advanced SEARCH Engine"))

        results = repo.search(ToolQuery(name="search"))
        assert len(results) == 2
        ids = {e.id for e in results}
        assert ids == {"t1", "t3"}

    def test_search_by_description_case_insensitive_substring(
        self, repo: MongoToolCatalogRepository
    ) -> None:
        """Search by description uses case-insensitive substring matching."""
        repo.create(make_tool(id="t1", description="Search the web for information"))
        repo.create(make_tool(id="t2", description="Perform calculations"))
        repo.create(make_tool(id="t3", description="Deep WEB crawler"))

        results = repo.search(ToolQuery(description="web"))
        assert len(results) == 2
        ids = {e.id for e in results}
        assert ids == {"t1", "t3"}

    def test_search_no_filters_returns_all(self, repo: MongoToolCatalogRepository) -> None:
        """Search with no filters returns all entries."""
        repo.create(make_tool(id="t1"))
        repo.create(make_tool(id="t2"))

        results = repo.search(ToolQuery())
        assert len(results) == 2

    def test_search_name_with_regex_special_chars(self, repo: MongoToolCatalogRepository) -> None:
        """Search with regex special characters in name treats them literally."""
        repo.create(make_tool(id="t1", name="Search (v2)"))
        repo.create(make_tool(id="t2", name="Search v2"))

        results = repo.search(ToolQuery(name="(v2)"))
        assert len(results) == 1
        assert results[0].id == "t1"

    def test_search_multiple_anded_fields(self, repo: MongoToolCatalogRepository) -> None:
        """Search with multiple fields AND-ed together."""
        repo.create(
            make_tool(
                id="t1",
                tool_class="akgentic.tool.planning.PlanningTool",
                name="Plan Search",
                description="Search via planning",
            )
        )
        repo.create(
            make_tool(
                id="t2",
                tool_class="akgentic.tool.planning.PlanningTool",
                name="Plan Calculator",
                description="Calculate via planning",
            )
        )
        repo.create(
            make_tool(
                id="t3",
                tool_class="akgentic.tool.search.SearchTool",
                name="Tavily Search",
                description="Search the web",
            )
        )

        # tool_class AND name
        results = repo.search(
            ToolQuery(
                tool_class="akgentic.tool.planning.PlanningTool",
                name="search",
            )
        )
        assert len(results) == 1
        assert results[0].id == "t1"


class TestUpdate:
    """Test update operations."""

    def test_update_existing_entry(self, repo: MongoToolCatalogRepository) -> None:
        """Update replaces the entry data."""
        repo.create(make_tool(id="tool-1", name="Old Name", description="Old desc"))

        updated = make_tool(id="tool-1", name="New Name", description="New desc")
        repo.update("tool-1", updated)

        retrieved = repo.get("tool-1")
        assert retrieved is not None
        assert retrieved.tool.name == "New Name"
        assert retrieved.tool.description == "New desc"

    def test_update_nonexistent_raises_not_found(self, repo: MongoToolCatalogRepository) -> None:
        """Updating a nonexistent entry raises EntryNotFoundError."""
        entry = make_tool(id="ghost")
        with pytest.raises(EntryNotFoundError, match="not found"):
            repo.update("ghost", entry)

    def test_update_id_mismatch_raises_validation_error(
        self, repo: MongoToolCatalogRepository
    ) -> None:
        """Updating with mismatched entry id raises CatalogValidationError."""
        repo.create(make_tool(id="tool-1"))
        mismatched = make_tool(id="tool-2")
        with pytest.raises(CatalogValidationError, match="id mismatch"):
            repo.update("tool-1", mismatched)


class TestDelete:
    """Test delete operations."""

    def test_delete_existing_entry(self, repo: MongoToolCatalogRepository) -> None:
        """Delete removes the entry from the collection."""
        repo.create(make_tool(id="tool-1"))
        assert repo.get("tool-1") is not None

        repo.delete("tool-1")
        assert repo.get("tool-1") is None

    def test_delete_nonexistent_raises_not_found(self, repo: MongoToolCatalogRepository) -> None:
        """Deleting a nonexistent entry raises EntryNotFoundError."""
        with pytest.raises(EntryNotFoundError, match="not found"):
            repo.delete("ghost")
