"""Tests for ``_ensure_schema_loaded`` and ``init_db`` (AC #4, #5, #6, #7, #18).

Two groups:

* **Unit tests** that don't need a Postgres container — spy on
  ``Schema.default.load_toml`` to assert ``_ensure_schema_loaded`` runs the
  load exactly once, and read the event-store stub to confirm ``init_db``
  is not called from its constructor.
* **Integration tests** that use the session-scoped ``postgres_initialized``
  fixture to exercise ``init_db`` against a real container, including an
  idempotency pass.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("nagra")


class TestEnsureSchemaLoadedIdempotent:
    """AC #4: ``_ensure_schema_loaded`` performs its work exactly once."""

    def test_load_toml_called_once_across_repeated_calls(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Spy ``load_toml`` and assert it fires exactly once across N calls.

        Stubbing ``load_toml`` keeps ``Schema.default`` clean so the spy
        does not poison later tests / fixtures. We also reset the
        ``_SCHEMA_LOADED`` guard back to its pre-test value so the real
        loader still runs exactly once when the session fixture wakes up.
        """
        from nagra import Schema

        import akgentic.team.repositories.postgres as pg_pkg

        original_flag = pg_pkg._SCHEMA_LOADED
        monkeypatch.setattr(pg_pkg, "_SCHEMA_LOADED", False, raising=False)

        call_count = {"n": 0}

        def stub_load(path: Path) -> object:
            call_count["n"] += 1
            return None

        monkeypatch.setattr(Schema.default, "load_toml", stub_load)

        pg_pkg._ensure_schema_loaded()
        pg_pkg._ensure_schema_loaded()
        pg_pkg._ensure_schema_loaded()

        assert call_count["n"] == 1

        monkeypatch.setattr(pg_pkg, "_SCHEMA_LOADED", original_flag, raising=False)


class TestEventStoreStubDoesNotCallInitDb:
    """AC #7: ``NagraEventStore.__init__`` must NOT call ``init_db``."""

    def test_event_store_source_does_not_call_init_db(self) -> None:
        stub_path = (
            Path(__file__).parents[3]
            / "src"
            / "akgentic"
            / "team"
            / "repositories"
            / "postgres"
            / "event_store.py"
        )
        text = stub_path.read_text()
        assert "init_db(" not in text, (
            "event_store.py must not call init_db() — operators run it"
        )
        assert "_ensure_schema_loaded" in text, (
            "event_store.py must call _ensure_schema_loaded() in __init__"
        )


class TestInitDbIntegration:
    """AC #5, #6, #18: ``init_db`` creates tables and is idempotent."""

    def test_init_db_creates_three_tables(self, postgres_initialized: str) -> None:
        from nagra import Transaction

        expected = {"team_process_entries", "event_entries", "agent_state_entries"}
        with Transaction(postgres_initialized) as trn:
            cursor = trn.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'public'"
            )
            found = {row[0] for row in cursor.fetchall()}
        assert expected.issubset(found)

    def test_init_db_is_idempotent(self, postgres_initialized: str) -> None:
        from nagra import Transaction

        from akgentic.team.repositories.postgres import init_db

        with Transaction(postgres_initialized) as trn:
            cursor = trn.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'public'"
            )
            before = {row[0] for row in cursor.fetchall()}

        # Second call must not raise and must not change the table set.
        init_db(postgres_initialized)

        with Transaction(postgres_initialized) as trn:
            cursor = trn.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'public'"
            )
            after = {row[0] for row in cursor.fetchall()}

        assert before == after
