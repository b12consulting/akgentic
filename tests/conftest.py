"""Shared test factory functions and in-memory mock repositories.

Factory functions are plain functions (not fixtures) — they are pure constructors
with no state. Fixtures are only for stateful objects that benefit from pytest
lifecycle management.

Session-scoped Postgres testcontainer fixtures (``postgres_container``,
``postgres_conn_string``) live here so they can be reused across subdirectories
(``tests/api/``, ``tests/cli/``, ``tests/repositories/postgres/``) with one
container per pytest session. The fixtures are only registered when both
``nagra`` and ``testcontainers.postgres`` are importable; tests that depend on
them gate themselves with ``pytest.importorskip`` at module level.
"""

from __future__ import annotations

import builtins
import importlib.util
from collections.abc import Iterator
from typing import TYPE_CHECKING

import pytest

from akgentic.catalog.models.agent import AgentEntry
from akgentic.catalog.models.team import TeamEntry, TeamMemberSpec
from akgentic.catalog.models.template import TemplateEntry
from akgentic.catalog.models.tool import ToolEntry
from akgentic.catalog.repositories.base import ToolCatalogRepository

if TYPE_CHECKING:
    from akgentic.catalog.models.queries import ToolQuery

_list = builtins.list  # Avoids shadowing by Pydantic 'list' fields


# --- Postgres testcontainer fixtures (conditional registration) ---

_POSTGRES_AVAILABLE = (
    importlib.util.find_spec("nagra") is not None
    and importlib.util.find_spec("testcontainers.postgres") is not None
)


def _to_nagra_conn_string(sqlalchemy_url: str) -> str:
    """Strip the SQLAlchemy driver suffix from a testcontainers URL.

    ``testcontainers`` emits URLs like
    ``postgresql+psycopg2://user:pw@host:port/db``. Nagra's ``Transaction``
    wraps a psycopg / libpq connection, which accepts the standard
    ``postgresql://`` scheme without the driver suffix. Strip the driver so
    the URL is portable regardless of Nagra's current psycopg binding.
    """
    if "+" in sqlalchemy_url.split("://", 1)[0]:
        scheme, rest = sqlalchemy_url.split("://", 1)
        scheme = scheme.split("+", 1)[0]
        return f"{scheme}://{rest}"
    return sqlalchemy_url


if _POSTGRES_AVAILABLE:
    from testcontainers.postgres import PostgresContainer  # noqa: E402

    @pytest.fixture(scope="session")
    def postgres_container() -> Iterator[PostgresContainer]:
        """Start a single postgres:16-alpine container for the test session."""
        with PostgresContainer("postgres:16-alpine") as container:
            yield container

    @pytest.fixture(scope="session")
    def postgres_conn_string(postgres_container: PostgresContainer) -> str:
        """Nagra-compatible connection string derived from the session container."""
        raw_url = postgres_container.get_connection_url()
        return _to_nagra_conn_string(raw_url)

    @pytest.fixture(scope="session")
    def postgres_initialized(postgres_conn_string: str) -> str:
        """Run ``init_db`` exactly once against the session container."""
        from akgentic.catalog.repositories.postgres import init_db

        init_db(postgres_conn_string)
        return postgres_conn_string


# --- Factory Functions ---


def make_template(
    id: str = "sys-prompt",
    template: str = "You are {role}. {instructions}",
) -> TemplateEntry:
    """Create a TemplateEntry for testing."""
    return TemplateEntry(id=id, template=template)


def make_tool(
    id: str = "search-1",
    tool_class: str = "akgentic.tool.search.SearchTool",
    name: str = "search",
    description: str = "Search the web",
) -> ToolEntry:
    """Create a ToolEntry for testing with configurable tool metadata."""
    return ToolEntry(
        id=id,
        tool_class=tool_class,
        tool={"name": name, "description": description},
    )


def make_agent(
    id: str = "agent-1",
    name: str = "test-agent",
    role: str = "engineer",
    tool_ids: _list[str] | None = None,
    template_ref: str | None = None,
    params: dict[str, str] | None = None,
    routes_to: _list[str] | None = None,
) -> AgentEntry:
    """Create an AgentEntry for testing with optional tool/template/route config."""
    prompt: dict[str, str | dict[str, str]] = {}
    if template_ref is not None:
        prompt["template"] = template_ref
        if params is not None:
            prompt["params"] = params
    config: dict[str, str | dict[str, str | dict[str, str]]] = {"name": name, "role": role}
    if prompt:
        config["prompt"] = prompt
    return AgentEntry(
        id=id,
        tool_ids=tool_ids or [],
        card={
            "description": "test agent",
            "skills": ["coding"],
            "agent_class": "akgentic.agent.BaseAgent",
            "config": config,
            "routes_to": routes_to or [],
        },
    )


def make_team(
    id: str = "team-1",
    name: str = "Test Team",
    entry_point: str = "agent-1",
    members: _list[TeamMemberSpec] | None = None,
    agent_profiles: _list[str] | None = None,
    message_types: _list[str] | None = None,
) -> TeamEntry:
    """Create a TeamEntry for testing with optional members and config."""
    default_members = members or [TeamMemberSpec(agent_id="agent-1")]
    return TeamEntry(
        id=id,
        name=name,
        entry_point=entry_point,
        message_types=message_types or ["akgentic.core.messages.UserMessage"],
        members=default_members,
        agent_profiles=agent_profiles or [],
    )


# --- In-Memory Mock Repositories (migrated from helpers.py) ---


class InMemoryToolCatalogRepository(ToolCatalogRepository):
    """Simple in-memory repository for testing service logic."""

    def __init__(self) -> None:
        self._entries: dict[str, ToolEntry] = {}

    def create(self, tool_entry: ToolEntry) -> str:
        self._entries[tool_entry.id] = tool_entry
        return tool_entry.id

    def get(self, id: str) -> ToolEntry | None:
        return self._entries.get(id)

    def list(self) -> _list[ToolEntry]:
        return _list(self._entries.values())

    def search(self, query: ToolQuery) -> _list[ToolEntry]:
        results = self.list()
        if query.id is not None:
            results = [e for e in results if e.id == query.id]
        if query.tool_class is not None:
            results = [e for e in results if e.tool_class == query.tool_class]
        return results

    def update(self, id: str, tool_entry: ToolEntry) -> None:
        self._entries[id] = tool_entry

    def delete(self, id: str) -> None:
        del self._entries[id]


class MockAgentCatalogRepository:
    """Mock repository that returns a fixed list of AgentEntry objects."""

    def __init__(self, entries: _list[AgentEntry]) -> None:
        self._entries = entries

    def list(self) -> _list[AgentEntry]:
        return self._entries


class MockAgentCatalog:
    """Mock agent catalog with .repository.list() for delete protection."""

    def __init__(self, entries: _list[AgentEntry]) -> None:
        self._repository = MockAgentCatalogRepository(entries)

    @property
    def repository(self) -> MockAgentCatalogRepository:
        return self._repository
