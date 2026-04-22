"""Behavioural tests for :class:`NagraTeamCatalogRepository`.

Mirrors the Mongo suite at ``tests/repositories/mongo/test_mongo_team_repo.py``
so that the Nagra backend honours the same CRUD and search contract. Every
test requests the per-test truncation fixture ``postgres_clean_tables`` from
the sibling ``conftest.py`` — which also carries the ``pytest.importorskip``
that makes the whole module skip cleanly when the Postgres extras are absent.

Story 15.3 AC coverage:
- Round-trip (with a nested member tree): ACs #16, #28
- Predicate builders: ACs #17, #18, #19, #20, #22, #29
- ``agent_id`` post-filter, including nested-member case: AC #21
- Error paths: AC #31
- Fixture wiring: ACs #32, #33
"""

from __future__ import annotations

import pytest

pytest.importorskip("nagra")
pytest.importorskip("testcontainers.postgres")

from akgentic.catalog.models.errors import (  # noqa: E402
    CatalogValidationError,
    EntryNotFoundError,
)
from akgentic.catalog.models.queries import TeamQuery  # noqa: E402
from akgentic.catalog.models.team import TeamMemberSpec  # noqa: E402
from akgentic.catalog.repositories.postgres.team_repo import (  # noqa: E402
    NagraTeamCatalogRepository,
)
from tests.conftest import make_team  # noqa: E402


@pytest.fixture
def repo(postgres_clean_tables: str) -> NagraTeamCatalogRepository:
    """Fresh repo backed by the per-test-truncated Postgres container."""
    return NagraTeamCatalogRepository(postgres_clean_tables)


class TestCreateAndGet:
    """create + get round-trip with a multi-level member tree — AC #16, #28."""

    def test_create_and_get_round_trip_with_nested_members(
        self, repo: NagraTeamCatalogRepository
    ) -> None:
        nested = make_team(
            id="t1",
            name="Deep Team",
            entry_point="director",
            members=[
                TeamMemberSpec(
                    agent_id="director",
                    headcount=1,
                    members=[
                        TeamMemberSpec(
                            agent_id="manager",
                            headcount=2,
                            members=[TeamMemberSpec(agent_id="deep-worker")],
                        ),
                    ],
                ),
            ],
        )
        assert repo.create(nested) == "t1"

        retrieved = repo.get("t1")
        assert retrieved is not None
        assert retrieved.id == "t1"
        assert retrieved.name == "Deep Team"
        # Hydrated tree deep-equals the original.
        assert retrieved.members == nested.members
        assert retrieved.members[0].members[0].headcount == 2
        assert retrieved.members[0].members[0].members[0].agent_id == "deep-worker"

    def test_create_duplicate_raises_validation_error(
        self, repo: NagraTeamCatalogRepository
    ) -> None:
        repo.create(make_team(id="dup"))
        with pytest.raises(CatalogValidationError, match="already exists"):
            repo.create(make_team(id="dup"))

    def test_get_nonexistent_returns_none(self, repo: NagraTeamCatalogRepository) -> None:
        assert repo.get("ghost") is None


class TestList:
    """list() enumerates all rows."""

    def test_list_empty(self, repo: NagraTeamCatalogRepository) -> None:
        assert repo.list() == []

    def test_list_returns_all_entries(self, repo: NagraTeamCatalogRepository) -> None:
        repo.create(make_team(id="t1", name="Alpha"))
        repo.create(make_team(id="t2", name="Beta"))
        repo.create(make_team(id="t3", name="Gamma"))
        assert {e.id for e in repo.list()} == {"t1", "t2", "t3"}


class TestSearch:
    """search() predicate builders behave like the Mongo backend."""

    def test_search_no_filters_returns_all(self, repo: NagraTeamCatalogRepository) -> None:
        # AC #17.
        repo.create(make_team(id="t1"))
        repo.create(make_team(id="t2"))
        assert len(repo.search(TeamQuery())) == 2

    def test_search_by_id(self, repo: NagraTeamCatalogRepository) -> None:
        # AC #18.
        repo.create(make_team(id="t1"))
        repo.create(make_team(id="t2"))

        results = repo.search(TeamQuery(id="t1"))
        assert len(results) == 1
        assert results[0].id == "t1"

        assert repo.search(TeamQuery(id="missing")) == []

    def test_search_by_name_case_insensitive(
        self, repo: NagraTeamCatalogRepository
    ) -> None:
        # AC #19.
        repo.create(make_team(id="t1", name="Engineering Team"))
        repo.create(make_team(id="t2", name="Marketing Team"))
        repo.create(make_team(id="t3", name="ENGINEERING Core"))

        results = repo.search(TeamQuery(name="engineering"))
        assert {e.id for e in results} == {"t1", "t3"}

    def test_search_by_name_with_wildcard_chars_literal(
        self, repo: NagraTeamCatalogRepository
    ) -> None:
        # ILIKE metacharacter escape — parity with Mongo re.escape.
        repo.create(make_team(id="t1", name="Team (Alpha)"))
        repo.create(make_team(id="t2", name="Team Alpha"))

        results = repo.search(TeamQuery(name="(Alpha)"))
        assert len(results) == 1
        assert results[0].id == "t1"

    def test_search_by_description_case_insensitive(
        self, repo: NagraTeamCatalogRepository
    ) -> None:
        # AC #20.
        t1 = make_team(id="t1", name="Alpha")
        t1.description = "Handles backend APIs"
        t2 = make_team(id="t2", name="Beta")
        t2.description = "Handles frontend UI"
        repo.create(t1)
        repo.create(t2)

        results = repo.search(TeamQuery(description="backend"))
        assert len(results) == 1
        assert results[0].id == "t1"


class TestSearchAgentIdPostFilter:
    """Dedicated coverage of the ``agent_id`` Python post-filter — AC #21."""

    def test_search_by_agent_id_flat_members(
        self, repo: NagraTeamCatalogRepository
    ) -> None:
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

    def test_search_by_agent_id_nested_members_two_levels_deep(
        self, repo: NagraTeamCatalogRepository
    ) -> None:
        # The must-not-miss-nested invariant — a top-level-only containment
        # predicate would miss this.
        nested = make_team(
            id="t1",
            name="Deep Team",
            entry_point="director",
            members=[
                TeamMemberSpec(
                    agent_id="director",
                    members=[
                        TeamMemberSpec(
                            agent_id="manager",
                            members=[TeamMemberSpec(agent_id="deep-worker")],
                        ),
                    ],
                ),
            ],
        )
        repo.create(nested)
        repo.create(make_team(id="t2", name="Flat Team"))

        results = repo.search(TeamQuery(agent_id="deep-worker"))
        assert len(results) == 1
        assert results[0].id == "t1"

    def test_search_by_agent_id_no_match(self, repo: NagraTeamCatalogRepository) -> None:
        repo.create(make_team(id="t1"))
        assert repo.search(TeamQuery(agent_id="nonexistent-agent")) == []


class TestSearchMultiField:
    """Server-side predicates AND'd, then post-filter applied — AC #22."""

    def test_multi_field_and_combined_with_agent_id_post_filter(
        self, repo: NagraTeamCatalogRepository
    ) -> None:
        # Server-side narrows to teams whose name contains "engineering";
        # agent_id post-filter then narrows further.
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
                members=[TeamMemberSpec(agent_id="eng-1")],
            )
        )

        results = repo.search(TeamQuery(name="engineering", agent_id="eng-1"))
        assert len(results) == 1
        assert results[0].id == "t1"

    def test_multi_server_side_fields_only(
        self, repo: NagraTeamCatalogRepository
    ) -> None:
        t1 = make_team(id="t1", name="Alpha")
        t1.description = "Handles backend"
        t2 = make_team(id="t2", name="Alpha Other")
        t2.description = "Handles frontend"
        repo.create(t1)
        repo.create(t2)

        results = repo.search(TeamQuery(name="Alpha", description="backend"))
        assert len(results) == 1
        assert results[0].id == "t1"


class TestUpdate:
    """update() replaces rows and enforces id contract."""

    def test_update_existing_entry(self, repo: NagraTeamCatalogRepository) -> None:
        repo.create(make_team(id="t1", name="Old"))

        updated = make_team(id="t1", name="New")
        repo.update("t1", updated)

        retrieved = repo.get("t1")
        assert retrieved is not None
        assert retrieved.name == "New"

    def test_update_nonexistent_raises_not_found(
        self, repo: NagraTeamCatalogRepository
    ) -> None:
        with pytest.raises(EntryNotFoundError, match="not found"):
            repo.update("ghost", make_team(id="ghost"))

    def test_update_id_mismatch_raises_validation_error(
        self, repo: NagraTeamCatalogRepository
    ) -> None:
        repo.create(make_team(id="t1"))
        with pytest.raises(CatalogValidationError, match="id mismatch"):
            repo.update("t1", make_team(id="t2"))


class TestDelete:
    """delete() removes rows and surfaces EntryNotFoundError."""

    def test_delete_existing_entry(self, repo: NagraTeamCatalogRepository) -> None:
        repo.create(make_team(id="t1"))
        assert repo.get("t1") is not None

        repo.delete("t1")
        assert repo.get("t1") is None

    def test_delete_nonexistent_raises_not_found(
        self, repo: NagraTeamCatalogRepository
    ) -> None:
        with pytest.raises(EntryNotFoundError, match="not found"):
            repo.delete("ghost")
