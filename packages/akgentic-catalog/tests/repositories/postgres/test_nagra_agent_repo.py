"""Behavioural tests for :class:`NagraAgentCatalogRepository`.

Mirrors the Mongo suite at ``tests/repositories/mongo/test_mongo_agent_repo.py``
so that the Nagra backend honours the same CRUD and search contract. Every
test requests the per-test truncation fixture ``postgres_clean_tables`` from
the sibling ``conftest.py`` — which also carries the ``pytest.importorskip``
that makes the whole module skip cleanly when the Postgres extras are absent.

Story 15.3 AC coverage:
- Round-trip + hydration: ACs #4, #6, #7, #28
- Predicate builders: ACs #10, #11, #12, #13, #14, #15, #29, #30
- Error paths: ACs #5, #8, #9, #31
- Fixture wiring: ACs #32, #33
"""

from __future__ import annotations

import pytest

pytest.importorskip("nagra")
pytest.importorskip("testcontainers.postgres")

from akgentic.catalog.models.agent import AgentEntry  # noqa: E402
from akgentic.catalog.models.errors import (  # noqa: E402
    CatalogValidationError,
    EntryNotFoundError,
)
from akgentic.catalog.models.queries import AgentQuery  # noqa: E402
from akgentic.catalog.repositories.postgres.agent_repo import (  # noqa: E402
    NagraAgentCatalogRepository,
)
from tests.conftest import make_agent  # noqa: E402


def _agent(
    id: str,
    role: str = "engineer",
    description: str = "test agent",
    skills: list[str] | None = None,
    name: str = "test-agent",
) -> AgentEntry:
    """Build an ``AgentEntry`` with per-field overrides for the card payload."""
    return AgentEntry(
        id=id,
        tool_ids=[],
        card={
            "role": role,
            "description": description,
            "skills": skills if skills is not None else ["coding"],
            "agent_class": "akgentic.agent.BaseAgent",
            "config": {"name": name},
            "routes_to": [],
        },
    )


@pytest.fixture
def repo(postgres_clean_tables: str) -> NagraAgentCatalogRepository:
    """Fresh repo backed by the per-test-truncated Postgres container."""
    return NagraAgentCatalogRepository(postgres_clean_tables)


class TestCreateAndGet:
    """create + get round-trip and duplicate-id handling."""

    def test_create_and_get_round_trip(self, repo: NagraAgentCatalogRepository) -> None:
        # Round-trip a non-trivial AgentEntry through JSONB — AC #28.
        entry = _agent(
            id="a1",
            role="Manager",
            description="Oversees the research team",
            skills=["research", "summarize", "coding"],
            name="manager-bot",
        )
        assert repo.create(entry) == "a1"

        retrieved = repo.get("a1")
        assert retrieved is not None
        assert retrieved.id == "a1"
        assert retrieved.card.role == "Manager"
        assert retrieved.card.description == "Oversees the research team"
        assert retrieved.card.skills == ["research", "summarize", "coding"]
        assert retrieved.card.config.name == "manager-bot"

    def test_create_duplicate_raises_validation_error(
        self, repo: NagraAgentCatalogRepository
    ) -> None:
        # AC #5.
        entry = make_agent(id="dup")
        repo.create(entry)

        with pytest.raises(CatalogValidationError, match="already exists"):
            repo.create(entry)

    def test_get_nonexistent_returns_none(self, repo: NagraAgentCatalogRepository) -> None:
        # AC #6 miss branch + AC #31.
        assert repo.get("ghost") is None


class TestList:
    """list() enumerates all rows — AC #7."""

    def test_list_empty(self, repo: NagraAgentCatalogRepository) -> None:
        assert repo.list() == []

    def test_list_returns_all_entries(self, repo: NagraAgentCatalogRepository) -> None:
        repo.create(make_agent(id="a1"))
        repo.create(make_agent(id="a2"))
        repo.create(make_agent(id="a3"))

        results = repo.list()
        assert len(results) == 3
        assert {e.id for e in results} == {"a1", "a2", "a3"}


class TestSearch:
    """search() predicate builders behave like the Mongo backend."""

    def test_search_no_filters_returns_all(self, repo: NagraAgentCatalogRepository) -> None:
        # AC #10.
        repo.create(make_agent(id="a1"))
        repo.create(make_agent(id="a2"))
        assert len(repo.search(AgentQuery())) == 2

    def test_search_by_id_hit_and_miss(self, repo: NagraAgentCatalogRepository) -> None:
        # AC #11.
        repo.create(make_agent(id="a1"))
        repo.create(make_agent(id="a2"))

        hit = repo.search(AgentQuery(id="a1"))
        assert len(hit) == 1
        assert hit[0].id == "a1"

        miss = repo.search(AgentQuery(id="missing"))
        assert miss == []

    def test_search_by_role_exact_match(self, repo: NagraAgentCatalogRepository) -> None:
        # AC #12.
        repo.create(_agent(id="a1", role="engineer"))
        repo.create(_agent(id="a2", role="Manager"))
        repo.create(_agent(id="a3", role="writer"))

        results = repo.search(AgentQuery(role="Manager"))
        assert len(results) == 1
        assert results[0].id == "a2"

    def test_search_by_description_case_insensitive(
        self, repo: NagraAgentCatalogRepository
    ) -> None:
        # AC #14.
        repo.create(_agent(id="a1", description="Builds REST APIs"))
        repo.create(_agent(id="a2", description="Builds CLI tools"))

        results = repo.search(AgentQuery(description="rest"))
        assert len(results) == 1
        assert results[0].id == "a1"

    def test_search_description_with_wildcard_chars_literal(
        self, repo: NagraAgentCatalogRepository
    ) -> None:
        # ILIKE metacharacter escape — matches Mongo's re.escape semantics.
        repo.create(_agent(id="a1", description="rate = 50% off"))
        repo.create(_agent(id="a2", description="rate = 50 off"))

        results = repo.search(AgentQuery(description="50%"))
        assert len(results) == 1
        assert results[0].id == "a1"

    def test_search_multiple_anded_fields(self, repo: NagraAgentCatalogRepository) -> None:
        # AC #15.
        repo.create(_agent(id="a1", role="engineer", skills=["research"]))
        repo.create(_agent(id="a2", role="engineer", skills=["writing"]))
        repo.create(_agent(id="a3", role="Manager", skills=["research"]))

        results = repo.search(AgentQuery(role="engineer", skills=["research"]))
        assert len(results) == 1
        assert results[0].id == "a1"


class TestSearchSkills:
    """Dedicated coverage of the ``?|`` ANY-match predicate — AC #13, #30."""

    def test_skills_any_match_single_skill(self, repo: NagraAgentCatalogRepository) -> None:
        # Hit: agent has ["research", "code"], query skills=["research"] → match.
        repo.create(_agent(id="a1", skills=["research", "code"]))
        repo.create(_agent(id="a2", skills=["writing"]))

        results = repo.search(AgentQuery(skills=["research"]))
        assert len(results) == 1
        assert results[0].id == "a1"

    def test_skills_any_match_not_all(self, repo: NagraAgentCatalogRepository) -> None:
        # Hit (ANY not ALL): agent has ["research"], query
        # skills=["research", "summarize"] → match.
        repo.create(_agent(id="a1", skills=["research"]))
        repo.create(_agent(id="a2", skills=["unrelated"]))

        results = repo.search(AgentQuery(skills=["research", "summarize"]))
        assert len(results) == 1
        assert results[0].id == "a1"

    def test_skills_miss(self, repo: NagraAgentCatalogRepository) -> None:
        # Miss: agent has ["unrelated"], query skills=["research"] → no match.
        repo.create(_agent(id="a1", skills=["unrelated"]))

        results = repo.search(AgentQuery(skills=["research"]))
        assert results == []

    def test_skills_empty_agent_skill_list_miss(
        self, repo: NagraAgentCatalogRepository
    ) -> None:
        # Edge: agent with empty skills list does NOT match any non-empty query.
        repo.create(_agent(id="a1", skills=[]))

        results = repo.search(AgentQuery(skills=["research"]))
        assert results == []

    def test_skills_multi_query_matches_multiple(
        self, repo: NagraAgentCatalogRepository
    ) -> None:
        repo.create(_agent(id="a1", skills=["research", "code"]))
        repo.create(_agent(id="a2", skills=["writing"]))
        repo.create(_agent(id="a3", skills=["testing", "writing"]))

        results = repo.search(AgentQuery(skills=["writing", "testing"]))
        assert {e.id for e in results} == {"a2", "a3"}


class TestUpdate:
    """update() replaces rows and enforces id contract — AC #8."""

    def test_update_existing_entry(self, repo: NagraAgentCatalogRepository) -> None:
        repo.create(make_agent(id="a1", name="old"))

        updated = make_agent(id="a1", name="new")
        repo.update("a1", updated)

        retrieved = repo.get("a1")
        assert retrieved is not None
        assert retrieved.card.config.name == "new"

    def test_update_nonexistent_raises_not_found(
        self, repo: NagraAgentCatalogRepository
    ) -> None:
        # AC #31.
        with pytest.raises(EntryNotFoundError, match="not found"):
            repo.update("ghost", make_agent(id="ghost"))

    def test_update_id_mismatch_raises_validation_error(
        self, repo: NagraAgentCatalogRepository
    ) -> None:
        # AC #31.
        repo.create(make_agent(id="a1"))
        mismatched = make_agent(id="a2")
        with pytest.raises(CatalogValidationError, match="id mismatch"):
            repo.update("a1", mismatched)


class TestDelete:
    """delete() removes rows and surfaces EntryNotFoundError — AC #9."""

    def test_delete_existing_entry(self, repo: NagraAgentCatalogRepository) -> None:
        repo.create(make_agent(id="a1"))
        assert repo.get("a1") is not None

        repo.delete("a1")
        assert repo.get("a1") is None

    def test_delete_nonexistent_raises_not_found(
        self, repo: NagraAgentCatalogRepository
    ) -> None:
        # AC #31.
        with pytest.raises(EntryNotFoundError, match="not found"):
            repo.delete("ghost")
