"""Behavioural tests for ILIKE predicate literal-wildcard escaping.

Exercises the Mongo-parity semantics introduced by Story 15.5: user-supplied
``%`` and ``_`` characters in ``ToolQuery.name`` / ``ToolQuery.description``
MUST match literally rather than act as SQL wildcards. Tests run end-to-end
through :class:`NagraToolCatalogRepository` against the session-scoped
testcontainer — no assertions on the generated SQL string shape, so future
SQL refactors do not break the tests.
"""

from __future__ import annotations

import pytest

pytest.importorskip("nagra")
pytest.importorskip("testcontainers.postgres")

from akgentic.catalog.models.queries import ToolQuery  # noqa: E402
from akgentic.catalog.repositories.postgres.tool_repo import (  # noqa: E402
    NagraToolCatalogRepository,
)
from tests.conftest import make_tool  # noqa: E402


@pytest.fixture
def repo(postgres_clean_tables: str) -> NagraToolCatalogRepository:
    """Fresh repo backed by the per-test-truncated Postgres container."""
    return NagraToolCatalogRepository(postgres_clean_tables)


def test_tool_name_literal_percent_wildcard_is_escaped(
    repo: NagraToolCatalogRepository,
) -> None:
    """A literal ``%`` in the query matches a literal ``%`` in the tool name."""
    repo.create(make_tool(id="t1", name="100%_fast"))
    repo.create(make_tool(id="t2", name="100x_fast"))
    repo.create(make_tool(id="t3", name="plain"))

    # Literal percent in query: only t1 matches
    results = repo.search(ToolQuery(name="100%"))
    assert {e.id for e in results} == {"t1"}


def test_tool_name_literal_underscore_wildcard_is_escaped(
    repo: NagraToolCatalogRepository,
) -> None:
    """A literal ``_`` in the query matches a literal ``_`` in the tool name."""
    repo.create(make_tool(id="t1", name="my_tool"))
    repo.create(make_tool(id="t2", name="myxtool"))
    repo.create(make_tool(id="t3", name="unrelated"))

    results = repo.search(ToolQuery(name="my_tool"))
    assert {e.id for e in results} == {"t1"}


def test_tool_description_literal_percent_wildcard_is_escaped(
    repo: NagraToolCatalogRepository,
) -> None:
    """A literal ``%`` in the description query matches only a literal ``%``."""
    repo.create(
        make_tool(id="t1", name="a", description="rate: 100% success"),
    )
    repo.create(
        make_tool(id="t2", name="b", description="rate: 100x success"),
    )
    repo.create(
        make_tool(id="t3", name="c", description="something entirely different"),
    )

    results = repo.search(ToolQuery(description="100%"))
    assert {e.id for e in results} == {"t1"}


def test_tool_description_literal_underscore_wildcard_is_escaped(
    repo: NagraToolCatalogRepository,
) -> None:
    """A literal ``_`` in the description query matches only a literal ``_``."""
    repo.create(
        make_tool(id="t1", name="a", description="uses my_tool internally"),
    )
    repo.create(
        make_tool(id="t2", name="b", description="uses myxtool internally"),
    )
    repo.create(
        make_tool(id="t3", name="c", description="plain text only"),
    )

    results = repo.search(ToolQuery(description="my_tool"))
    assert {e.id for e in results} == {"t1"}


def test_tool_name_happy_path_substring(
    repo: NagraToolCatalogRepository,
) -> None:
    """Regression: plain wildcard-free substring search still matches everywhere."""
    repo.create(make_tool(id="t1", name="json-parser"))
    repo.create(make_tool(id="t2", name="JSON Formatter"))
    repo.create(make_tool(id="t3", name="csv-reader"))

    results = repo.search(ToolQuery(name="json"))
    assert {e.id for e in results} == {"t1", "t2"}


def test_tool_description_happy_path_substring(
    repo: NagraToolCatalogRepository,
) -> None:
    """Regression: plain wildcard-free description search still matches case-insensitively."""
    repo.create(make_tool(id="t1", name="a", description="Search the web for data"))
    repo.create(make_tool(id="t2", name="b", description="Perform calculations"))
    repo.create(make_tool(id="t3", name="c", description="Deep WEB crawler"))

    results = repo.search(ToolQuery(description="web"))
    assert {e.id for e in results} == {"t1", "t3"}
