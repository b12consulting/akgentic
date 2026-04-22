"""Tests for ``_ensure_schema_loaded`` and ``init_db`` (AC #4, #5, #6, #7, #16).

Contains two groups:

* **Unit tests** that don't need a Postgres container — spy on
  ``Schema.default.load_toml`` to assert ``_ensure_schema_loaded`` runs the
  load exactly once, and grep the repo stubs to confirm ``init_db`` is not
  called from constructors.
* **Integration tests** that use the session-scoped ``postgres_initialized``
  fixture to exercise ``init_db`` against a real container, including an
  idempotency pass.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest

pytest.importorskip("nagra")

if TYPE_CHECKING:
    pass


class TestEnsureSchemaLoadedIdempotent:
    """AC #4: ``_ensure_schema_loaded`` performs its work exactly once."""

    def test_load_toml_called_once_across_repeated_calls(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Force a fresh guard and stub ``load_toml`` so tables never actually register.

        Without the stub, running ``_ensure_schema_loaded`` here would populate
        ``Schema.default`` globally and poison later tests. The stub lets us
        observe the call count cleanly — and because we also reset
        ``_SCHEMA_LOADED`` back to its pre-test value afterwards, the real
        schema loader in the session fixture still runs exactly once.
        """
        from nagra import Schema

        import akgentic.catalog.repositories.postgres as pg_pkg

        # Save the pre-test guard value so we can restore it exactly — monkeypatch
        # would also restore it, but we want both directions to be explicit.
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

        # Restore the original guard so later fixtures/tests see a consistent state.
        monkeypatch.setattr(pg_pkg, "_SCHEMA_LOADED", original_flag, raising=False)


class TestRepoStubsDoNotCallInitDb:
    """AC #7: No repository stub calls ``init_db`` in its constructor."""

    def test_stub_sources_contain_no_init_db_call(self) -> None:
        stub_names = ["template_repo.py", "tool_repo.py", "agent_repo.py", "team_repo.py"]
        pkg_dir = (
            Path(__file__).parents[3]
            / "src"
            / "akgentic"
            / "catalog"
            / "repositories"
            / "postgres"
        )
        for stub in stub_names:
            text = (pkg_dir / stub).read_text()
            assert "init_db(" not in text, f"{stub} must not call init_db()"
            assert "_ensure_schema_loaded" in text, (
                f"{stub} must call _ensure_schema_loaded() in __init__"
            )


class TestInitDbIntegration:
    """AC #5, #6, #16: ``init_db`` creates tables and is idempotent."""

    def test_init_db_creates_four_tables(self, postgres_initialized: str) -> None:
        from nagra import Transaction

        expected = {"template_entries", "tool_entries", "agent_entries", "team_entries"}
        with Transaction(postgres_initialized) as trn:
            cursor = trn.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'public'"
            )
            found = {row[0] for row in cursor.fetchall()}
        assert expected.issubset(found)

    def test_init_db_is_idempotent(self, postgres_initialized: str) -> None:
        from nagra import Transaction

        from akgentic.catalog.repositories.postgres import init_db

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
