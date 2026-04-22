"""Tests for MongoTeamCatalogRepository."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from akgentic.catalog.models.errors import CatalogValidationError, EntryNotFoundError
from akgentic.catalog.models.queries import TeamQuery
from akgentic.catalog.models.team import TeamMemberSpec
from akgentic.catalog.repositories.mongo.team_repo import MongoTeamCatalogRepository
from tests.conftest import make_team

if TYPE_CHECKING:
    import pymongo.collection


@pytest.fixture
def repo(team_collection: pymongo.collection.Collection) -> MongoTeamCatalogRepository:  # type: ignore[type-arg]
    """Provide a MongoTeamCatalogRepository backed by mongomock."""
    return MongoTeamCatalogRepository(team_collection)


class TestCreateAndGet:
    """AC-5: MongoTeamCatalogRepository CRUD — create and get round-trip."""

    def test_create_and_get_round_trip(self, repo: MongoTeamCatalogRepository) -> None:
        """Create a team entry and retrieve it by id."""
        entry = make_team(id="team-1", name="Engineering")
        created_id = repo.create(entry)
        assert created_id == "team-1"

        retrieved = repo.get("team-1")
        assert retrieved is not None
        assert retrieved.id == "team-1"
        assert retrieved.name == "Engineering"
        assert len(retrieved.members) == 1

    def test_create_duplicate_raises_validation_error(
        self, repo: MongoTeamCatalogRepository
    ) -> None:
        """Creating a team with an existing id raises CatalogValidationError."""
        entry = make_team(id="dup-1")
        repo.create(entry)

        with pytest.raises(CatalogValidationError, match="already exists"):
            repo.create(entry)

    def test_get_nonexistent_returns_none(self, repo: MongoTeamCatalogRepository) -> None:
        """Getting a nonexistent id returns None."""
        assert repo.get("nonexistent") is None


class TestList:
    """AC-5: MongoTeamCatalogRepository list operation."""

    def test_list_empty(self, repo: MongoTeamCatalogRepository) -> None:
        """Empty collection returns empty list."""
        assert repo.list() == []

    def test_list_returns_all_entries(self, repo: MongoTeamCatalogRepository) -> None:
        """List returns all created team entries."""
        repo.create(make_team(id="t1", name="Alpha"))
        repo.create(make_team(id="t2", name="Beta"))
        repo.create(make_team(id="t3", name="Gamma"))

        results = repo.list()
        assert len(results) == 3
        ids = {e.id for e in results}
        assert ids == {"t1", "t2", "t3"}


class TestSearch:
    """AC-6/7: Team search by agent_id, name, and description."""

    def test_search_by_id_exact_match(self, repo: MongoTeamCatalogRepository) -> None:
        """Search by id returns exact match only."""
        repo.create(make_team(id="t1", name="Alpha"))
        repo.create(make_team(id="t2", name="Beta"))

        results = repo.search(TeamQuery(id="t1"))
        assert len(results) == 1
        assert results[0].id == "t1"

    def test_search_by_name_case_insensitive(self, repo: MongoTeamCatalogRepository) -> None:
        """Search by name uses case-insensitive substring matching."""
        repo.create(make_team(id="t1", name="Engineering Team"))
        repo.create(make_team(id="t2", name="Marketing Team"))
        repo.create(make_team(id="t3", name="ENGINEERING Core"))

        results = repo.search(TeamQuery(name="engineering"))
        assert len(results) == 2
        ids = {e.id for e in results}
        assert ids == {"t1", "t3"}

    def test_search_by_description_case_insensitive(self, repo: MongoTeamCatalogRepository) -> None:
        """Search by description uses case-insensitive substring matching."""
        t1 = make_team(id="t1", name="Alpha")
        t1.description = "Handles backend APIs"
        t2 = make_team(id="t2", name="Beta")
        t2.description = "Handles frontend UI"
        repo.create(t1)
        repo.create(t2)

        results = repo.search(TeamQuery(description="backend"))
        assert len(results) == 1
        assert results[0].id == "t1"

    def test_search_by_agent_id_flat_members(self, repo: MongoTeamCatalogRepository) -> None:
        """Search by agent_id finds agent at top level of members."""
        repo.create(
            make_team(
                id="t1",
                name="Team A",
                members=[
                    TeamMemberSpec(agent_id="eng-lead"),
                    TeamMemberSpec(agent_id="dev-1"),
                ],
            )
        )
        repo.create(
            make_team(
                id="t2",
                name="Team B",
                members=[TeamMemberSpec(agent_id="pm-lead")],
            )
        )

        results = repo.search(TeamQuery(agent_id="dev-1"))
        assert len(results) == 1
        assert results[0].id == "t1"

    def test_search_by_agent_id_nested_members(self, repo: MongoTeamCatalogRepository) -> None:
        """Search by agent_id finds agent buried 2+ levels deep in member tree."""
        nested_team = make_team(
            id="t1",
            name="Deep Team",
            members=[
                TeamMemberSpec(
                    agent_id="director",
                    members=[
                        TeamMemberSpec(
                            agent_id="manager",
                            members=[
                                TeamMemberSpec(agent_id="deep-worker"),
                            ],
                        ),
                    ],
                ),
            ],
        )
        repo.create(nested_team)
        repo.create(make_team(id="t2", name="Flat Team"))

        results = repo.search(TeamQuery(agent_id="deep-worker"))
        assert len(results) == 1
        assert results[0].id == "t1"

    def test_search_by_agent_id_no_match(self, repo: MongoTeamCatalogRepository) -> None:
        """Search by agent_id returns empty when no team contains the agent."""
        repo.create(make_team(id="t1", name="Team A"))

        results = repo.search(TeamQuery(agent_id="nonexistent-agent"))
        assert results == []

    def test_search_multiple_anded_fields(self, repo: MongoTeamCatalogRepository) -> None:
        """Search with multiple fields AND-ed together."""
        repo.create(
            make_team(
                id="t1",
                name="Engineering Alpha",
                members=[TeamMemberSpec(agent_id="eng-1")],
            )
        )
        repo.create(
            make_team(
                id="t2",
                name="Engineering Beta",
                members=[TeamMemberSpec(agent_id="eng-2")],
            )
        )
        repo.create(
            make_team(
                id="t3",
                name="Marketing Alpha",
                members=[TeamMemberSpec(agent_id="mkt-1")],
            )
        )

        # name contains "engineering" AND agent_id = "eng-1"
        results = repo.search(TeamQuery(name="engineering", agent_id="eng-1"))
        assert len(results) == 1
        assert results[0].id == "t1"

    def test_search_name_with_regex_special_chars(self, repo: MongoTeamCatalogRepository) -> None:
        """Search with regex special characters in name treats them literally."""
        repo.create(make_team(id="t1", name="Team (Alpha)"))
        repo.create(make_team(id="t2", name="Team Alpha"))

        results = repo.search(TeamQuery(name="(Alpha)"))
        assert len(results) == 1
        assert results[0].id == "t1"

    def test_search_no_filters_returns_all(self, repo: MongoTeamCatalogRepository) -> None:
        """Search with no filters returns all entries."""
        repo.create(make_team(id="t1", name="Alpha"))
        repo.create(make_team(id="t2", name="Beta"))

        results = repo.search(TeamQuery())
        assert len(results) == 2


class TestUpdate:
    """AC-5: MongoTeamCatalogRepository update with id-mismatch guard."""

    def test_update_existing_entry(self, repo: MongoTeamCatalogRepository) -> None:
        """Update replaces the team entry data."""
        repo.create(make_team(id="team-1", name="Old Name"))

        updated = make_team(id="team-1", name="New Name")
        repo.update("team-1", updated)

        retrieved = repo.get("team-1")
        assert retrieved is not None
        assert retrieved.name == "New Name"

    def test_update_nonexistent_raises_not_found(self, repo: MongoTeamCatalogRepository) -> None:
        """Updating a nonexistent team raises EntryNotFoundError."""
        entry = make_team(id="ghost")
        with pytest.raises(EntryNotFoundError, match="not found"):
            repo.update("ghost", entry)

    def test_update_id_mismatch_raises_validation_error(
        self, repo: MongoTeamCatalogRepository
    ) -> None:
        """Updating with mismatched team id raises CatalogValidationError."""
        repo.create(make_team(id="team-1"))
        mismatched = make_team(id="team-2")
        with pytest.raises(CatalogValidationError, match="id mismatch"):
            repo.update("team-1", mismatched)


class TestDelete:
    """AC-5: MongoTeamCatalogRepository delete operation."""

    def test_delete_existing_entry(self, repo: MongoTeamCatalogRepository) -> None:
        """Delete removes the team from the collection."""
        repo.create(make_team(id="team-1"))
        assert repo.get("team-1") is not None

        repo.delete("team-1")
        assert repo.get("team-1") is None

    def test_delete_nonexistent_raises_not_found(self, repo: MongoTeamCatalogRepository) -> None:
        """Deleting a nonexistent team raises EntryNotFoundError."""
        with pytest.raises(EntryNotFoundError, match="not found"):
            repo.delete("ghost")
