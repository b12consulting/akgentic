"""Fixtures for Postgres (Nagra) repository tests.

The session-scoped ``postgres_container`` / ``postgres_conn_string`` /
``postgres_initialized`` fixtures live in the package-root
``tests/conftest.py`` so that ``tests/api/`` and ``tests/cli/`` can share
the same container with this directory — one container per pytest session,
regardless of which subdirectory triggered it. See issue #104 for the lift
rationale.

This module only adds the per-test ``postgres_clean_tables`` fixture that
truncates the four catalog tables between tests, and skips the whole
directory cleanly via ``pytest.importorskip`` when the ``[postgres]`` extra
is absent.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

pytest.importorskip("nagra")
pytest.importorskip("testcontainers.postgres")


@pytest.fixture
def postgres_clean_tables(postgres_initialized: str) -> Iterator[str]:
    """Truncate the four catalog tables between tests."""
    from nagra import Transaction

    yield postgres_initialized
    with Transaction(postgres_initialized) as trn:
        trn.execute(
            "TRUNCATE template_entries, tool_entries, agent_entries, team_entries"
        )
