"""Tests for the ``akgentic.team.scripts.init_mongo`` init-container entry point.

Cover the exit paths without requiring a real MongoDB server:

* Exit 2 when ``MONGO_URI`` or ``MONGO_DB`` is unset or empty — each checked
  independently, with both variables cleared first so an ambient value cannot
  mask the case under test.
* Exit 0 when :func:`ensure_indexes` succeeds against a ``mongomock`` client.
* Exit 1 when the Mongo backend is unavailable or :func:`ensure_indexes` raises.

Plus a ``python -m`` subprocess check that exercises the module path, the
``__main__`` guard, and ``main()``'s return value reaching ``sys.exit`` — none
of which the in-process tests touch. It drives the exit-2 path, so it needs no
Mongo server either.
"""

from __future__ import annotations

import os
import subprocess
import sys
from unittest.mock import MagicMock, patch

import pytest

import akgentic.team.scripts.init_mongo as script


class TestInitMongoScriptExitCodes:
    """Unit tests for :func:`akgentic.team.scripts.init_mongo.main`."""

    def test_missing_uri_returns_2(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Missing ``MONGO_URI`` exits with code 2 (distinct from other errors)."""
        monkeypatch.delenv("MONGO_URI", raising=False)
        monkeypatch.delenv("MONGO_DB", raising=False)
        monkeypatch.setenv("MONGO_DB", "akgentic")

        assert script.main() == 2

    def test_missing_db_returns_2(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Missing ``MONGO_DB`` exits 2 even when ``MONGO_URI`` is set."""
        monkeypatch.delenv("MONGO_URI", raising=False)
        monkeypatch.delenv("MONGO_DB", raising=False)
        monkeypatch.setenv("MONGO_URI", "mongodb://fake")

        assert script.main() == 2

    @pytest.mark.parametrize("blank_var", ["MONGO_URI", "MONGO_DB"])
    def test_empty_value_returns_2(
        self, monkeypatch: pytest.MonkeyPatch, blank_var: str
    ) -> None:
        """An empty variable is treated as unset — same exit code 2."""
        monkeypatch.setenv("MONGO_URI", "mongodb://fake")
        monkeypatch.setenv("MONGO_DB", "akgentic")
        monkeypatch.setenv(blank_var, "")

        assert script.main() == 2

    def test_success_returns_0_and_creates_both_indexes(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Happy path: both teams indexes land on the configured database."""
        pytest.importorskip("pymongo")
        mongomock = pytest.importorskip("mongomock")
        monkeypatch.setenv("MONGO_URI", "mongodb://fake")
        monkeypatch.setenv("MONGO_DB", "akgentic")
        monkeypatch.delenv("MONGO_TEAMS_COLLECTION", raising=False)
        client = mongomock.MongoClient()

        with patch("pymongo.MongoClient", return_value=client):
            assert script.main() == 0

        info = client["akgentic"]["teams"].index_information()
        assert info["teams_user_id_idx"]["key"] == [("user_id", 1)]
        assert info["teams_status_idx"]["key"] == [("status", 1)]

    def test_ensure_indexes_failure_returns_1(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Any exception from ensure_indexes exits 1 (logged, not re-raised)."""
        pytest.importorskip("pymongo")
        mongomock = pytest.importorskip("mongomock")
        monkeypatch.setenv("MONGO_URI", "mongodb://fake")
        monkeypatch.setenv("MONGO_DB", "akgentic")

        with (
            patch("pymongo.MongoClient", return_value=mongomock.MongoClient()),
            patch(
                "akgentic.team.repositories.mongo.ensure_indexes",
                side_effect=RuntimeError("connection refused"),
            ),
        ):
            assert script.main() == 1

    def test_unreachable_server_returns_1(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A server that cannot be reached exits 1, not a false success.

        :func:`ensure_indexes` swallows every ``PyMongoError`` by design — a
        rejected spec must never stop a process from starting — and
        ``MongoClient`` connects lazily, so without a reachability check a
        refused or authentication-rejected server logs two warnings and lets
        this script report success having created nothing. That is the worst
        outcome for the only provisioning path a deployment which set
        ``MONGO_TEAM_AUTO_INDEX=0`` has left: the operator sees a green init
        container while the teams indexes never appear.
        """
        pytest.importorskip("pymongo")
        from pymongo.errors import ServerSelectionTimeoutError

        monkeypatch.setenv("MONGO_URI", "mongodb://unreachable")
        monkeypatch.setenv("MONGO_DB", "akgentic")
        client = MagicMock()
        client.__enter__.return_value = client
        client.admin.command.side_effect = ServerSelectionTimeoutError("no servers")

        with patch("pymongo.MongoClient", return_value=client):
            assert script.main() == 1

    def test_unavailable_backend_returns_1(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A missing ``[mongo]`` extra exits 1 rather than raising a traceback.

        Relies on the ``ensure_indexes`` import living inside ``main()`` — a
        module-scope import would make this path unreachable.
        """
        monkeypatch.setenv("MONGO_URI", "mongodb://fake")
        monkeypatch.setenv("MONGO_DB", "akgentic")

        with patch.dict(sys.modules, {"pymongo": None}):
            assert script.main() == 1


class TestInitMongoScriptModulePath:
    """The module is runnable as ``python -m akgentic.team.scripts.init_mongo``."""

    def test_python_dash_m_without_env_exits_2(self) -> None:
        """Driving the documented command with no env produces exit code 2.

        The only test that proves the module path resolves, that the
        ``__main__`` guard exists, and that ``main()``'s return value is handed
        to ``sys.exit``. Every other test calls ``main()`` in-process.
        """
        env = dict(os.environ)
        env.pop("MONGO_URI", None)
        env.pop("MONGO_DB", None)
        result = subprocess.run(
            [sys.executable, "-m", "akgentic.team.scripts.init_mongo"],
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )

        assert result.returncode == 2, (
            f"stdout={result.stdout!r} stderr={result.stderr!r}"
        )
