"""Behavioural tests for :class:`NagraToolCatalogRepository`.

Mirrors the Mongo suite at ``tests/repositories/mongo/test_mongo_tool_repo.py``
so the Nagra backend honours the same CRUD and search contract.
"""

from __future__ import annotations

import pytest

pytest.importorskip("nagra")
pytest.importorskip("testcontainers.postgres")

from akgentic.catalog.models.errors import (  # noqa: E402
    CatalogValidationError,
    EntryNotFoundError,
)
from akgentic.catalog.models.queries import ToolQuery  # noqa: E402
from akgentic.catalog.repositories.postgres.tool_repo import (  # noqa: E402
    NagraToolCatalogRepository,
)
from tests.conftest import make_tool  # noqa: E402


@pytest.fixture
def repo(postgres_clean_tables: str) -> NagraToolCatalogRepository:
    """Fresh repo backed by the per-test-truncated Postgres container."""
    return NagraToolCatalogRepository(postgres_clean_tables)


class TestCreateAndGet:
    """create + get round-trip and duplicate-id handling."""

    def test_create_and_get_round_trip(self, repo: NagraToolCatalogRepository) -> None:
        entry = make_tool(
            id="tool-1",
            name="Search",
            description="Web search tool",
        )
        created_id = repo.create(entry)
        assert created_id == "tool-1"

        retrieved = repo.get("tool-1")
        assert retrieved is not None
        assert retrieved.id == "tool-1"
        assert retrieved.tool_class == "akgentic.tool.search.SearchTool"
        assert retrieved.tool.name == "Search"
        assert retrieved.tool.description == "Web search tool"

    def test_create_duplicate_raises_validation_error(
        self, repo: NagraToolCatalogRepository
    ) -> None:
        entry = make_tool(id="dup-1")
        repo.create(entry)

        with pytest.raises(CatalogValidationError, match="already exists"):
            repo.create(entry)

    def test_get_nonexistent_returns_none(self, repo: NagraToolCatalogRepository) -> None:
        assert repo.get("nonexistent") is None


class TestList:
    """list() enumerates all rows."""

    def test_list_empty(self, repo: NagraToolCatalogRepository) -> None:
        assert repo.list() == []

    def test_list_returns_all_entries(self, repo: NagraToolCatalogRepository) -> None:
        repo.create(make_tool(id="tool-a", name="Alpha"))
        repo.create(make_tool(id="tool-b", name="Beta"))
        repo.create(make_tool(id="tool-c", name="Gamma"))

        results = repo.list()
        assert {e.id for e in results} == {"tool-a", "tool-b", "tool-c"}


class TestSearch:
    """search() predicate builders behave like the Mongo backend."""

    def test_search_by_id_hit_and_miss(
        self, repo: NagraToolCatalogRepository
    ) -> None:
        repo.create(make_tool(id="t1", name="Alpha"))
        repo.create(make_tool(id="t2", name="Beta"))

        hit = repo.search(ToolQuery(id="t1"))
        assert len(hit) == 1
        assert hit[0].id == "t1"

        assert repo.search(ToolQuery(id="t-missing")) == []

    def test_search_by_tool_class_hit_and_miss(
        self, repo: NagraToolCatalogRepository
    ) -> None:
        repo.create(make_tool(id="t1", tool_class="akgentic.tool.search.SearchTool"))
        repo.create(
            make_tool(
                id="t2",
                tool_class="akgentic.tool.planning.PlanningTool",
                name="planner",
                description="Planning tool",
            )
        )

        hit = repo.search(ToolQuery(tool_class="akgentic.tool.planning.PlanningTool"))
        assert len(hit) == 1
        assert hit[0].id == "t2"

        assert repo.search(ToolQuery(tool_class="missing.Class")) == []

    def test_search_by_name_case_insensitive_substring(
        self, repo: NagraToolCatalogRepository
    ) -> None:
        repo.create(make_tool(id="t1", name="Web Search Tool"))
        repo.create(make_tool(id="t2", name="Calculator"))
        repo.create(make_tool(id="t3", name="Advanced SEARCH Engine"))

        results = repo.search(ToolQuery(name="search"))
        assert {e.id for e in results} == {"t1", "t3"}

        assert repo.search(ToolQuery(name="nothing-like-this")) == []

    def test_search_by_description_case_insensitive_substring(
        self, repo: NagraToolCatalogRepository
    ) -> None:
        repo.create(make_tool(id="t1", description="Search the web for information"))
        repo.create(make_tool(id="t2", description="Perform calculations"))
        repo.create(make_tool(id="t3", description="Deep WEB crawler"))

        results = repo.search(ToolQuery(description="web"))
        assert {e.id for e in results} == {"t1", "t3"}

        assert repo.search(ToolQuery(description="nothing")) == []

    def test_search_no_filters_returns_all(
        self, repo: NagraToolCatalogRepository
    ) -> None:
        repo.create(make_tool(id="t1"))
        repo.create(make_tool(id="t2"))

        results = repo.search(ToolQuery())
        assert len(results) == 2

    def test_search_multiple_anded_fields(
        self, repo: NagraToolCatalogRepository
    ) -> None:
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

        # tool_class AND name — only t1 matches both
        results = repo.search(
            ToolQuery(
                tool_class="akgentic.tool.planning.PlanningTool",
                name="search",
            )
        )
        assert len(results) == 1
        assert results[0].id == "t1"


class TestUpdate:
    """update() replaces rows and enforces id contract."""

    def test_update_existing_entry(self, repo: NagraToolCatalogRepository) -> None:
        repo.create(make_tool(id="tool-1", name="Old Name", description="Old desc"))

        updated = make_tool(id="tool-1", name="New Name", description="New desc")
        repo.update("tool-1", updated)

        retrieved = repo.get("tool-1")
        assert retrieved is not None
        assert retrieved.tool.name == "New Name"
        assert retrieved.tool.description == "New desc"

    def test_update_nonexistent_raises_not_found(
        self, repo: NagraToolCatalogRepository
    ) -> None:
        with pytest.raises(EntryNotFoundError, match="not found"):
            repo.update("ghost", make_tool(id="ghost"))

    def test_update_id_mismatch_raises_validation_error(
        self, repo: NagraToolCatalogRepository
    ) -> None:
        repo.create(make_tool(id="tool-1"))
        mismatched = make_tool(id="tool-2")
        with pytest.raises(CatalogValidationError, match="id mismatch"):
            repo.update("tool-1", mismatched)


class TestDelete:
    """delete() removes rows and surfaces EntryNotFoundError."""

    def test_delete_existing_entry(self, repo: NagraToolCatalogRepository) -> None:
        repo.create(make_tool(id="tool-1"))
        assert repo.get("tool-1") is not None

        repo.delete("tool-1")
        assert repo.get("tool-1") is None

    def test_delete_nonexistent_raises_not_found(
        self, repo: NagraToolCatalogRepository
    ) -> None:
        with pytest.raises(EntryNotFoundError, match="not found"):
            repo.delete("ghost")
