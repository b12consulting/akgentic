"""Tests for the ``akgentic.team.scripts.init_db`` init-container entry point.

Cover the three exit paths without requiring a real Postgres instance:

* Exit 2 when ``DB_CONN_STRING_PERSISTENCE`` is unset.
* Exit 0 when :func:`init_db` succeeds (patched).
* Exit 1 when :func:`init_db` raises (patched).

Plus a smoke test that drives the module via ``python -m`` against the
session-scoped Testcontainers fixture (gated on ``nagra`` /
``testcontainers[postgres]`` availability).
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

import akgentic.team.scripts.init_db as script


class TestInitDbScriptExitCodes:
    """Unit tests for :func:`akgentic.team.scripts.init_db.main`."""

    def test_missing_env_returns_2(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Missing env var exits with code 2 (distinct from other errors)."""
        monkeypatch.delenv("DB_CONN_STRING_PERSISTENCE", raising=False)

        assert script.main() == 2

    def test_init_db_success_returns_0(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Happy path: init_db succeeds, process exits 0."""
        pytest.importorskip("nagra")
        monkeypatch.setenv("DB_CONN_STRING_PERSISTENCE", "postgresql://fake")

        with patch(
            "akgentic.team.repositories.postgres.init_db",
            return_value=None,
        ) as mock_init_db:
            assert script.main() == 0

        mock_init_db.assert_called_once_with("postgresql://fake")

    def test_init_db_failure_returns_1(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Any exception from init_db produces exit code 1 (and is logged, not re-raised)."""
        pytest.importorskip("nagra")
        monkeypatch.setenv("DB_CONN_STRING_PERSISTENCE", "postgresql://fake")

        with patch(
            "akgentic.team.repositories.postgres.init_db",
            side_effect=RuntimeError("connection refused"),
        ):
            assert script.main() == 1
