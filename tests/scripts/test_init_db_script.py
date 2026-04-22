"""Tests for the ``akgentic.catalog.scripts.init_db`` init-container entrypoint.

Cover the three exit paths without requiring a real Postgres instance:

* Exit 2 when ``DB_CONN_STRING_PERSISTENCE`` is unset.
* Exit 0 when :func:`init_db` succeeds (patched).
* Exit 1 when :func:`init_db` raises (patched).
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

import akgentic.catalog.scripts.init_db as script


class TestInitDbScriptExitCodes:
    """Unit tests for :func:`akgentic.catalog.scripts.init_db.main`."""

    def test_missing_env_returns_2(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Missing env var exits with code 2 (distinct from other errors)."""
        monkeypatch.delenv("DB_CONN_STRING_PERSISTENCE", raising=False)

        assert script.main() == 2

    def test_init_db_success_returns_0(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Happy path: init_db succeeds, process exits 0."""
        monkeypatch.setenv("DB_CONN_STRING_PERSISTENCE", "postgresql://fake")

        with patch(
            "akgentic.catalog.repositories.postgres.init_db",
            return_value=None,
        ) as mock_init_db:
            assert script.main() == 0

        mock_init_db.assert_called_once_with("postgresql://fake")

    def test_init_db_failure_returns_1(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Any exception from init_db produces exit code 1."""
        monkeypatch.setenv("DB_CONN_STRING_PERSISTENCE", "postgresql://fake")

        with patch(
            "akgentic.catalog.repositories.postgres.init_db",
            side_effect=RuntimeError("connection refused"),
        ):
            assert script.main() == 1
