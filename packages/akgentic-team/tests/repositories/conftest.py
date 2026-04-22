"""Test fixtures for team repository tests.

Hosts both the parametrized ``event_store`` fixture used by the shared
``TestEventStoreContract`` suite and the per-backend fixtures it composes
(``mongo_store``, ``postgres_clean_tables``). The per-backend fixtures
live here — rather than in subdirectory conftests — because pytest's
fixture lookup only walks leaf-to-root; a parametrized fixture at this
level cannot see fixtures defined deeper in the tree. The bespoke
per-backend test modules under ``mongo/`` and ``postgres/`` continue to
use these same fixtures via normal conftest inheritance.

Skip semantics mirror the per-backend behaviour:

* ``yaml`` always runs (pure stdlib + Pydantic).
* ``mongo`` requires ``pymongo`` and ``mongomock`` (both in the ``dev``
  extra); the per-test ``mongo_store`` fixture builds a fresh mongomock
  database so no truncation is needed.
* ``postgres`` requires ``nagra``, ``psycopg``, and
  ``testcontainers[postgres]``. The session-scoped container is started
  once and the three tables are truncated between tests by
  ``postgres_clean_tables``.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

if TYPE_CHECKING:
    from akgentic.team.ports import EventStore
    from akgentic.team.repositories.mongo import MongoEventStore


# --- Mongo fixtures (lifted from tests/repositories/mongo/conftest.py) --


@pytest.fixture
def mongo_client() -> Any:
    """Create a mongomock client for testing."""
    pytest.importorskip("mongomock")
    import mongomock

    return mongomock.MongoClient()


@pytest.fixture
def mongo_db(mongo_client: Any) -> Any:
    """Create a test database from the mongomock client."""
    return mongo_client["test_akgentic_team"]


@pytest.fixture
def mongo_store(mongo_db: Any) -> MongoEventStore:
    """Create a MongoEventStore backed by a mongomock database."""
    pytest.importorskip("pymongo")
    from akgentic.team.repositories.mongo import MongoEventStore

    return MongoEventStore(mongo_db)


# --- Postgres fixtures (lifted from tests/repositories/postgres/conftest.py) --


def _to_nagra_conn_string(sqlalchemy_url: str) -> str:
    """Strip the SQLAlchemy driver suffix from a testcontainers URL."""
    if "+" in sqlalchemy_url.split("://", 1)[0]:
        scheme, rest = sqlalchemy_url.split("://", 1)
        scheme = scheme.split("+", 1)[0]
        return f"{scheme}://{rest}"
    return sqlalchemy_url


@pytest.fixture(scope="session")
def postgres_container() -> Iterator[Any]:
    """Start a single ``postgres:16-alpine`` container for the test session."""
    pytest.importorskip("testcontainers.postgres")
    from testcontainers.postgres import PostgresContainer

    with PostgresContainer("postgres:16-alpine") as container:
        yield container


@pytest.fixture(scope="session")
def postgres_conn_string(postgres_container: Any) -> str:
    """Nagra-compatible connection string derived from the session container."""
    raw_url = postgres_container.get_connection_url()
    return _to_nagra_conn_string(raw_url)


@pytest.fixture(scope="session")
def postgres_initialized(postgres_conn_string: str) -> str:
    """Run :func:`init_db` exactly once against the session container."""
    pytest.importorskip("nagra")
    from akgentic.team.repositories.postgres import init_db

    init_db(postgres_conn_string)
    return postgres_conn_string


@pytest.fixture
def postgres_clean_tables(postgres_initialized: str) -> Iterator[str]:
    """Truncate the three team event-store tables between tests."""
    from nagra import Transaction  # type: ignore[import-untyped]

    yield postgres_initialized
    with Transaction(postgres_initialized) as trn:
        trn.execute("TRUNCATE team_process_entries, event_entries, agent_state_entries")


# --- Parametrized contract fixture --------------------------------------


@pytest.fixture(params=["yaml", "mongo", "postgres"])
def event_store(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[EventStore]:
    """Yield a fresh ``EventStore`` for each backend parameter.

    The fixture id matches the parameter string (``yaml`` / ``mongo`` /
    ``postgres``) so pytest output identifies the backend at a glance.
    Each branch yields a clean store per test.
    """
    backend = request.param
    if backend == "yaml":
        from akgentic.team.repositories.yaml import YamlEventStore

        yield YamlEventStore(tmp_path)
        return

    if backend == "mongo":
        pytest.importorskip("pymongo")
        pytest.importorskip("mongomock")
        store = request.getfixturevalue("mongo_store")
        yield store
        return

    if backend == "postgres":
        pytest.importorskip("nagra")
        pytest.importorskip("psycopg")
        pytest.importorskip("testcontainers.postgres")
        from akgentic.team.repositories.postgres import NagraEventStore

        conn = request.getfixturevalue("postgres_clean_tables")
        yield NagraEventStore(conn)
        return

    msg = f"Unknown event_store backend: {backend}"
    raise ValueError(msg)
