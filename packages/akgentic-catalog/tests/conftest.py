"""Shared test configuration for akgentic-catalog.

Defines session-scoped infrastructure fixtures used across the
``tests/api/``, ``tests/cli/``, ``tests/scripts/``, and
``tests/repositories/`` sub-suites. In particular, the Postgres DSN
fixture lives here so every sub-suite shares ONE live database per
pytest session (start-up is expensive; repeated TRUNCATE is cheap).

Two DSN sources are supported, in priority order:

1. The ``DB_CONN_STRING_PERSISTENCE`` environment variable — CI runs use
   a service container via this channel (the GitHub Actions ``services:``
   block exposes Postgres on ``localhost:5432``). Tests skip the
   testcontainers import path entirely when the env var is set.
2. A local ``testcontainers.postgres.PostgresContainer`` — default for
   developer machines where Docker is running but no external Postgres
   is configured.

Skip-clean discipline: the fixture skips cleanly when the
``[postgres]`` extra is absent (``nagra`` / ``psycopg`` unimportable) OR
when neither channel is viable (no env var AND Docker unavailable).

Implements ADR-011 §"Wiring surface" — the shared fixture lives at the
package-level conftest so every sub-suite can consume it. Navigation-only
reference.
"""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest


@pytest.fixture(scope="session")
def postgres_dsn() -> Iterator[str]:
    """Yield a Postgres DSN backed by one live database per session.

    Skips cleanly when:

    * ``nagra`` / ``psycopg`` are not installed (the ``[postgres]`` extra
      is absent).
    * No ``DB_CONN_STRING_PERSISTENCE`` env var is set AND
      ``testcontainers.postgres`` is unavailable or Docker itself is not
      running.

    Strips the ``+psycopg2`` / ``+psycopg`` driver suffix from the DSN
    so Nagra (which accepts only plain libpq DSNs) works uniformly
    across both source channels.

    Applies the schema once per session via ``init_db`` — every sub-suite
    consuming this fixture finds an empty-but-initialised
    ``catalog_entries`` table. Per-test isolation is the consumer's
    concern (see :func:`postgres_clean_dsn` below).
    """
    pytest.importorskip("nagra")
    pytest.importorskip("psycopg")

    env_dsn = os.environ.get("DB_CONN_STRING_PERSISTENCE")
    if env_dsn:
        dsn = _normalise_dsn(env_dsn)
        _apply_schema(dsn)
        yield dsn
        return

    pg_module = pytest.importorskip("testcontainers.postgres")
    try:
        container = pg_module.PostgresContainer("postgres:16-alpine")
        container.start()
    except Exception as exc:  # pragma: no cover — infra-dependent branch
        pytest.skip(f"Docker unavailable for testcontainers.postgres: {exc}")

    try:
        dsn = _normalise_dsn(container.get_connection_url())
        _apply_schema(dsn)
        yield dsn
    finally:
        container.stop()


def _normalise_dsn(dsn: str) -> str:
    """Strip SQLAlchemy-style driver tokens — Nagra wants a bare libpq DSN."""
    dsn = dsn.replace("postgresql+psycopg2://", "postgresql://")
    dsn = dsn.replace("postgresql+psycopg://", "postgresql://")
    return dsn


def _apply_schema(dsn: str) -> None:
    """Run ``init_db`` against ``dsn`` — idempotent, safe to re-run."""
    from akgentic.catalog.repositories.postgres import PostgresCatalogConfig, init_db

    init_db(PostgresCatalogConfig(connection_string=dsn))


@pytest.fixture
def postgres_clean_dsn(postgres_dsn: str) -> str:
    """Truncate ``catalog_entries`` before each test; return the shared DSN.

    Uses a direct psycopg connection (outside Nagra) so the truncate is a
    focused maintenance op rather than a transaction with broader intent.
    """
    import psycopg

    with psycopg.connect(postgres_dsn) as conn, conn.cursor() as cur:
        cur.execute("TRUNCATE catalog_entries")
    return postgres_dsn
