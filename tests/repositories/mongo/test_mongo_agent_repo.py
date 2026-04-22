"""Tests for MongoAgentCatalogRepository."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from akgentic.catalog.models.agent import AgentEntry
from akgentic.catalog.models.errors import CatalogValidationError, EntryNotFoundError
from akgentic.catalog.models.queries import AgentQuery
from akgentic.catalog.repositories.mongo.agent_repo import MongoAgentCatalogRepository
from tests.conftest import make_agent

if TYPE_CHECKING:
    import pymongo.collection


@pytest.fixture
def repo(agent_collection: pymongo.collection.Collection) -> MongoAgentCatalogRepository:  # type: ignore[type-arg]
    """Provide a MongoAgentCatalogRepository backed by mongomock."""
    return MongoAgentCatalogRepository(agent_collection)


class TestCreateAndGet:
    """AC-1: MongoAgentCatalogRepository CRUD — create and get round-trip."""

    def test_create_and_get_round_trip(self, repo: MongoAgentCatalogRepository) -> None:
        """Create an entry and retrieve it by id."""
        entry = make_agent(id="agent-1", name="test-agent")
        created_id = repo.create(entry)
        assert created_id == "agent-1"

        retrieved = repo.get("agent-1")
        assert retrieved is not None
        assert retrieved.id == "agent-1"
        assert retrieved.card.role == "engineer"
        assert retrieved.card.skills == ["coding"]

    def test_create_duplicate_raises_validation_error(
        self, repo: MongoAgentCatalogRepository
    ) -> None:
        """Creating an entry with an existing id raises CatalogValidationError."""
        entry = make_agent(id="dup-1")
        repo.create(entry)

        with pytest.raises(CatalogValidationError, match="already exists"):
            repo.create(entry)

    def test_get_nonexistent_returns_none(self, repo: MongoAgentCatalogRepository) -> None:
        """Getting a nonexistent id returns None."""
        assert repo.get("nonexistent") is None


class TestList:
    """AC-1: MongoAgentCatalogRepository list operation."""

    def test_list_empty(self, repo: MongoAgentCatalogRepository) -> None:
        """Empty collection returns empty list."""
        assert repo.list() == []

    def test_list_returns_all_entries(self, repo: MongoAgentCatalogRepository) -> None:
        """List returns all created entries."""
        repo.create(make_agent(id="a1"))
        repo.create(make_agent(id="a2"))
        repo.create(make_agent(id="a3"))

        results = repo.list()
        assert len(results) == 3
        ids = {e.id for e in results}
        assert ids == {"a1", "a2", "a3"}


class TestSearch:
    """AC-2/3/4: Agent search by role, skills, and description."""

    def test_search_by_id_exact_match(self, repo: MongoAgentCatalogRepository) -> None:
        """Search by id returns exact match only."""
        repo.create(make_agent(id="a1"))
        repo.create(make_agent(id="a2"))

        results = repo.search(AgentQuery(id="a1"))
        assert len(results) == 1
        assert results[0].id == "a1"

    def test_search_by_role_exact_match(self, repo: MongoAgentCatalogRepository) -> None:
        """Search by role returns exact matches on card.role."""
        # make_agent defaults to role="engineer"
        repo.create(make_agent(id="a1"))
        repo.create(
            make_agent(id="a2"),
        )

        # Override role by creating with different card data
        manager = AgentEntry(
            id="a3",
            tool_ids=[],
            card={
                "role": "manager",
                "description": "team lead",
                "skills": ["leadership"],
                "agent_class": "akgentic.agent.BaseAgent",
                "config": {"name": "mgr-agent"},
                "routes_to": [],
            },
        )
        repo.create(manager)

        results = repo.search(AgentQuery(role="manager"))
        assert len(results) == 1
        assert results[0].id == "a3"

    def test_search_by_skills_any_overlap(self, repo: MongoAgentCatalogRepository) -> None:
        """Search by skills returns agents with ANY of the listed skills."""
        a1 = AgentEntry(
            id="a1",
            tool_ids=[],
            card={
                "role": "researcher",
                "description": "researches topics",
                "skills": ["research", "coding"],
                "agent_class": "akgentic.agent.BaseAgent",
                "config": {"name": "researcher"},
                "routes_to": [],
            },
        )
        a2 = AgentEntry(
            id="a2",
            tool_ids=[],
            card={
                "role": "writer",
                "description": "writes content",
                "skills": ["writing"],
                "agent_class": "akgentic.agent.BaseAgent",
                "config": {"name": "writer"},
                "routes_to": [],
            },
        )
        a3 = AgentEntry(
            id="a3",
            tool_ids=[],
            card={
                "role": "coder",
                "description": "writes code",
                "skills": ["coding", "testing"],
                "agent_class": "akgentic.agent.BaseAgent",
                "config": {"name": "coder"},
                "routes_to": [],
            },
        )
        repo.create(a1)
        repo.create(a2)
        repo.create(a3)

        # Query for "research" — matches a1 only
        results = repo.search(AgentQuery(skills=["research"]))
        assert len(results) == 1
        assert results[0].id == "a1"

        # Query for "coding" — matches a1 and a3
        results = repo.search(AgentQuery(skills=["coding"]))
        assert len(results) == 2
        ids = {e.id for e in results}
        assert ids == {"a1", "a3"}

        # Query for ANY of ["writing", "testing"] — matches a2 and a3
        results = repo.search(AgentQuery(skills=["writing", "testing"]))
        assert len(results) == 2
        ids = {e.id for e in results}
        assert ids == {"a2", "a3"}

    def test_search_by_description_case_insensitive(
        self, repo: MongoAgentCatalogRepository
    ) -> None:
        """Search by description uses case-insensitive substring matching."""
        a1 = AgentEntry(
            id="a1",
            tool_ids=[],
            card={
                "role": "engineer",
                "description": "Builds REST APIs",
                "skills": ["coding"],
                "agent_class": "akgentic.agent.BaseAgent",
                "config": {"name": "api-builder"},
                "routes_to": [],
            },
        )
        a2 = AgentEntry(
            id="a2",
            tool_ids=[],
            card={
                "role": "engineer",
                "description": "Builds CLI tools",
                "skills": ["coding"],
                "agent_class": "akgentic.agent.BaseAgent",
                "config": {"name": "cli-builder"},
                "routes_to": [],
            },
        )
        repo.create(a1)
        repo.create(a2)

        results = repo.search(AgentQuery(description="rest"))
        assert len(results) == 1
        assert results[0].id == "a1"

    def test_search_multiple_anded_fields(self, repo: MongoAgentCatalogRepository) -> None:
        """Search with multiple fields AND-ed together."""
        a1 = AgentEntry(
            id="a1",
            tool_ids=[],
            card={
                "role": "engineer",
                "description": "builds APIs",
                "skills": ["coding", "research"],
                "agent_class": "akgentic.agent.BaseAgent",
                "config": {"name": "a1"},
                "routes_to": [],
            },
        )
        a2 = AgentEntry(
            id="a2",
            tool_ids=[],
            card={
                "role": "engineer",
                "description": "builds UIs",
                "skills": ["coding"],
                "agent_class": "akgentic.agent.BaseAgent",
                "config": {"name": "a2"},
                "routes_to": [],
            },
        )
        a3 = AgentEntry(
            id="a3",
            tool_ids=[],
            card={
                "role": "manager",
                "description": "manages APIs team",
                "skills": ["leadership"],
                "agent_class": "akgentic.agent.BaseAgent",
                "config": {"name": "a3"},
                "routes_to": [],
            },
        )
        repo.create(a1)
        repo.create(a2)
        repo.create(a3)

        # role=engineer AND description contains "api" → only a1
        results = repo.search(AgentQuery(role="engineer", description="api"))
        assert len(results) == 1
        assert results[0].id == "a1"

    def test_search_no_filters_returns_all(self, repo: MongoAgentCatalogRepository) -> None:
        """Search with no filters returns all entries."""
        repo.create(make_agent(id="a1"))
        repo.create(make_agent(id="a2"))

        results = repo.search(AgentQuery())
        assert len(results) == 2

    def test_search_description_with_regex_special_chars(
        self, repo: MongoAgentCatalogRepository
    ) -> None:
        """Search with regex special characters in description treats them literally."""
        a1 = AgentEntry(
            id="a1",
            tool_ids=[],
            card={
                "role": "engineer",
                "description": "builds APIs (v2)",
                "skills": ["coding"],
                "agent_class": "akgentic.agent.BaseAgent",
                "config": {"name": "a1"},
                "routes_to": [],
            },
        )
        a2 = AgentEntry(
            id="a2",
            tool_ids=[],
            card={
                "role": "engineer",
                "description": "builds APIs v2",
                "skills": ["coding"],
                "agent_class": "akgentic.agent.BaseAgent",
                "config": {"name": "a2"},
                "routes_to": [],
            },
        )
        repo.create(a1)
        repo.create(a2)

        results = repo.search(AgentQuery(description="(v2)"))
        assert len(results) == 1
        assert results[0].id == "a1"


class TestUpdate:
    """AC-1: MongoAgentCatalogRepository update with id-mismatch guard."""

    def test_update_existing_entry(self, repo: MongoAgentCatalogRepository) -> None:
        """Update replaces the entry data."""
        repo.create(make_agent(id="agent-1", name="old-name"))

        updated = make_agent(id="agent-1", name="new-name")
        repo.update("agent-1", updated)

        retrieved = repo.get("agent-1")
        assert retrieved is not None
        assert retrieved.card.config.name == "new-name"

    def test_update_nonexistent_raises_not_found(self, repo: MongoAgentCatalogRepository) -> None:
        """Updating a nonexistent entry raises EntryNotFoundError."""
        entry = make_agent(id="ghost")
        with pytest.raises(EntryNotFoundError, match="not found"):
            repo.update("ghost", entry)

    def test_update_id_mismatch_raises_validation_error(
        self, repo: MongoAgentCatalogRepository
    ) -> None:
        """Updating with mismatched entry id raises CatalogValidationError."""
        repo.create(make_agent(id="agent-1"))
        mismatched = make_agent(id="agent-2")
        with pytest.raises(CatalogValidationError, match="id mismatch"):
            repo.update("agent-1", mismatched)


class TestDelete:
    """AC-1: MongoAgentCatalogRepository delete operation."""

    def test_delete_existing_entry(self, repo: MongoAgentCatalogRepository) -> None:
        """Delete removes the entry from the collection."""
        repo.create(make_agent(id="agent-1"))
        assert repo.get("agent-1") is not None

        repo.delete("agent-1")
        assert repo.get("agent-1") is None

    def test_delete_nonexistent_raises_not_found(self, repo: MongoAgentCatalogRepository) -> None:
        """Deleting a nonexistent entry raises EntryNotFoundError."""
        with pytest.raises(EntryNotFoundError, match="not found"):
            repo.delete("ghost")
