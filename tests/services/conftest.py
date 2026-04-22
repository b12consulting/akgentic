"""Service-layer test configuration with in-memory repositories and catalog fixtures.

Provides pre-wired catalog fixtures with dependency injection for service tests.
"""

from __future__ import annotations

import builtins
from typing import TYPE_CHECKING

import pytest

from akgentic.catalog.models.agent import AgentEntry
from akgentic.catalog.models.team import TeamEntry
from akgentic.catalog.models.template import TemplateEntry
from akgentic.catalog.repositories.base import (
    AgentCatalogRepository,
    TeamCatalogRepository,
    TemplateCatalogRepository,
)
from akgentic.catalog.services.template_catalog import TemplateCatalog
from akgentic.catalog.services.tool_catalog import ToolCatalog
from tests.conftest import InMemoryToolCatalogRepository

if TYPE_CHECKING:
    from akgentic.catalog.models.queries import AgentQuery, TeamQuery, TemplateQuery

_list = builtins.list  # Avoids shadowing by Pydantic 'list' fields


# --- In-Memory Repository Implementations ---


class InMemoryTemplateCatalogRepository(TemplateCatalogRepository):
    """In-memory template repository with query filtering support."""

    def __init__(self) -> None:
        self._entries: dict[str, TemplateEntry] = {}

    def create(self, template_entry: TemplateEntry) -> str:
        self._entries[template_entry.id] = template_entry
        return template_entry.id

    def get(self, id: str) -> TemplateEntry | None:
        return self._entries.get(id)

    def list(self) -> _list[TemplateEntry]:
        return _list(self._entries.values())

    def search(self, query: TemplateQuery) -> _list[TemplateEntry]:
        results = self.list()
        if query.id is not None:
            results = [e for e in results if e.id == query.id]
        if query.placeholder is not None:
            results = [e for e in results if query.placeholder in e.placeholders]
        return results

    def update(self, id: str, template_entry: TemplateEntry) -> None:
        self._entries[id] = template_entry

    def delete(self, id: str) -> None:
        del self._entries[id]


class InMemoryAgentCatalogRepository(AgentCatalogRepository):
    """In-memory agent repository for testing service logic."""

    def __init__(self) -> None:
        self._entries: dict[str, AgentEntry] = {}

    def create(self, agent_entry: AgentEntry) -> str:
        self._entries[agent_entry.id] = agent_entry
        return agent_entry.id

    def get(self, id: str) -> AgentEntry | None:
        return self._entries.get(id)

    def list(self) -> _list[AgentEntry]:
        return _list(self._entries.values())

    def search(self, query: AgentQuery) -> _list[AgentEntry]:
        results = self.list()
        if query.id is not None:
            results = [e for e in results if e.id == query.id]
        if query.role is not None:
            results = [e for e in results if e.card.role == query.role]
        if query.skills is not None:
            results = [
                e for e in results if any(s in e.card.skills for s in query.skills)
            ]
        if query.description is not None:
            results = [
                e
                for e in results
                if query.description.lower() in e.card.description.lower()
            ]
        return results

    def update(self, id: str, agent_entry: AgentEntry) -> None:
        self._entries[id] = agent_entry

    def delete(self, id: str) -> None:
        del self._entries[id]


class InMemoryTeamCatalogRepository(TeamCatalogRepository):
    """In-memory team repository for testing service logic."""

    def __init__(self) -> None:
        self._entries: dict[str, TeamEntry] = {}

    def create(self, team_entry: TeamEntry) -> str:
        self._entries[team_entry.id] = team_entry
        return team_entry.id

    def get(self, id: str) -> TeamEntry | None:
        return self._entries.get(id)

    def list(self) -> _list[TeamEntry]:
        return _list(self._entries.values())

    def search(self, query: TeamQuery) -> _list[TeamEntry]:
        from akgentic.catalog.models.team import agent_in_members

        results = self.list()
        if query.id is not None:
            results = [e for e in results if e.id == query.id]
        if query.name is not None:
            results = [
                e for e in results if query.name.lower() in e.name.lower()
            ]
        if query.description is not None:
            results = [
                e
                for e in results
                if query.description.lower() in e.description.lower()
            ]
        if query.agent_id is not None:
            results = [
                e for e in results if agent_in_members(query.agent_id, e.members)
            ]
        return results

    def update(self, id: str, team_entry: TeamEntry) -> None:
        self._entries[id] = team_entry

    def delete(self, id: str) -> None:
        del self._entries[id]


class MockTeamCatalog:
    """Mock team catalog satisfying _TeamCatalogProtocol (has list() method)."""

    def __init__(self, teams: _list[TeamEntry]) -> None:
        self._teams = _list(teams)

    def list(self) -> _list[TeamEntry]:
        return self._teams


# --- Shared Fixtures ---


@pytest.fixture
def template_repo() -> InMemoryTemplateCatalogRepository:
    """Empty in-memory template repository."""
    return InMemoryTemplateCatalogRepository()


@pytest.fixture
def tool_repo() -> InMemoryToolCatalogRepository:
    """Empty in-memory tool repository."""
    return InMemoryToolCatalogRepository()


@pytest.fixture
def agent_repo() -> InMemoryAgentCatalogRepository:
    """Empty in-memory agent repository."""
    return InMemoryAgentCatalogRepository()


@pytest.fixture
def team_repo() -> InMemoryTeamCatalogRepository:
    """Empty in-memory team repository."""
    return InMemoryTeamCatalogRepository()


@pytest.fixture
def template_catalog(
    template_repo: InMemoryTemplateCatalogRepository,
) -> TemplateCatalog:
    """TemplateCatalog wired to in-memory repository."""
    return TemplateCatalog(repository=template_repo)


@pytest.fixture
def tool_catalog(tool_repo: InMemoryToolCatalogRepository) -> ToolCatalog:
    """ToolCatalog wired to in-memory repository."""
    return ToolCatalog(repository=tool_repo)
