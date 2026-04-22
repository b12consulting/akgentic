"""Integration tests for the CLI ``--backend postgres`` dispatch.

Skips cleanly when the ``[postgres]`` extra is absent. Uses the session-scoped
Postgres testcontainer fixtures defined in the package-root
``tests/conftest.py``.
"""

from __future__ import annotations

import pytest

pytest.importorskip("nagra")
pytest.importorskip("testcontainers.postgres")

from typer.testing import CliRunner  # noqa: E402

from akgentic.catalog.cli.main import app  # noqa: E402
from akgentic.catalog.repositories.postgres import (  # noqa: E402
    NagraTemplateCatalogRepository,
    init_db,
)
from tests.conftest import make_template  # noqa: E402

runner = CliRunner()


def _truncate(conn_string: str) -> None:
    """Truncate the four catalog tables on the shared session container."""
    from nagra import Transaction

    with Transaction(conn_string) as trn:
        trn.execute(
            "TRUNCATE template_entries, tool_entries, agent_entries, team_entries"
        )


def test_cli_backend_postgres_template_list_end_to_end(
    postgres_conn_string: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC #13 / AC #15: CLI dispatches to Nagra repos and returns seeded rows."""
    # Prevent the env-var fallback from leaking into this test.
    monkeypatch.delenv("DB_CONN_STRING_PERSISTENCE", raising=False)

    init_db(postgres_conn_string)
    _truncate(postgres_conn_string)

    NagraTemplateCatalogRepository(postgres_conn_string).create(
        make_template(id="cli-pg-e2e", template="Hi {name}")
    )

    result = runner.invoke(
        app,
        [
            "--backend",
            "postgres",
            "--postgres-conn-string",
            postgres_conn_string,
            "--format",
            "json",
            "template",
            "list",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "cli-pg-e2e" in result.stdout


def test_cli_backend_postgres_missing_conn_string_exits_non_zero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC #9 / AC #15: missing conn string -> exit 1 with operator-facing message."""
    monkeypatch.delenv("DB_CONN_STRING_PERSISTENCE", raising=False)

    result = runner.invoke(
        app,
        ["--backend", "postgres", "template", "list"],
    )
    assert result.exit_code == 1
    combined = result.stdout + result.output
    assert "--postgres-conn-string" in combined
    assert "DB_CONN_STRING_PERSISTENCE" in combined


def test_cli_backend_postgres_env_var_fallback(
    postgres_conn_string: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC #9 / AC #15: ``DB_CONN_STRING_PERSISTENCE`` env var binds to Typer."""
    init_db(postgres_conn_string)
    _truncate(postgres_conn_string)

    NagraTemplateCatalogRepository(postgres_conn_string).create(
        make_template(id="cli-pg-env", template="Env {name}")
    )

    monkeypatch.setenv("DB_CONN_STRING_PERSISTENCE", postgres_conn_string)
    result = runner.invoke(
        app,
        [
            "--backend",
            "postgres",
            "--format",
            "json",
            "template",
            "list",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "cli-pg-env" in result.stdout
