"""CLI round-trip + validation-diagnostic tests for the Postgres backend.

Exercises ``ak-catalog --backend=postgres ...`` via
:class:`typer.testing.CliRunner`. Three orthogonal channels are verified:

* Flag channel (``--postgres-conn-string``).
* Env-var channel (``DB_CONN_STRING_PERSISTENCE``).
* Missing-both → exit 2 with the pinned diagnostic message.

Skip discipline: ``typer`` is guarded via ``pytest.importorskip`` inside
each test body (parity with ``tests/v2/test_cli_foundation.py``). The
shared ``postgres_clean_dsn`` fixture in ``tests/conftest.py`` skips
cleanly when the ``[postgres]`` extra or Docker is absent — except for
the "missing flag + env var" test, which exercises an exit-code-2 guard
that has NO Docker dependency and runs unconditionally.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

_TEAM_TYPE = "akgentic.team.models.TeamCard"


def _team_payload() -> dict[str, Any]:
    return {
        "name": "team",
        "description": "",
        "entry_point": {
            "card": {
                "description": "",
                "skills": [],
                "agent_class": "akgentic.core.agent.Akgent",
                "config": {"name": "entry", "role": "entry"},
            },
            "headcount": 1,
            "members": [],
        },
        "members": [],
        "agent_profiles": [],
    }


def _write_team_yaml(path: Path, namespace: str, entry_id: str = "team") -> Path:
    """Write a minimal single-entry YAML file usable with ``ak-catalog team create``."""
    data = {
        "id": entry_id,
        "kind": "team",
        "namespace": namespace,
        "model_type": _TEAM_TYPE,
        "payload": _team_payload(),
    }
    path.write_text(yaml.safe_dump(data, sort_keys=False))
    return path


def test_postgres_flag_channel_round_trip(postgres_clean_dsn: str, tmp_path: Path) -> None:
    """AC29 flag channel: --backend=postgres --postgres-conn-string=<dsn> → round-trip."""
    pytest.importorskip("typer")
    from typer.testing import CliRunner

    from akgentic.catalog.cli import main as cli_main

    runner = CliRunner()

    # Initial list → empty on a fresh DB.
    result = runner.invoke(
        cli_main.app,
        [
            "--backend",
            "postgres",
            "--postgres-conn-string",
            postgres_clean_dsn,
            "--format",
            "json",
            "team",
            "list",
            "--namespace",
            "ns-cli",
        ],
    )
    assert result.exit_code == 0, result.stderr
    import json

    assert json.loads(result.stdout) == []

    # Create a team via YAML file.
    team_yaml = _write_team_yaml(tmp_path / "team.yaml", namespace="ns-cli")
    result = runner.invoke(
        cli_main.app,
        [
            "--backend",
            "postgres",
            "--postgres-conn-string",
            postgres_clean_dsn,
            "--format",
            "json",
            "team",
            "create",
            str(team_yaml),
        ],
    )
    assert result.exit_code == 0, result.stderr

    # Get the team back.
    result = runner.invoke(
        cli_main.app,
        [
            "--backend",
            "postgres",
            "--postgres-conn-string",
            postgres_clean_dsn,
            "--format",
            "json",
            "team",
            "get",
            "team",
            "--namespace",
            "ns-cli",
        ],
    )
    assert result.exit_code == 0, result.stderr
    # The ``get`` verb emits a Rich table header followed by a JSON payload on
    # stdout — the ``_render_entry`` function mixes both. We only need to
    # confirm the team id surfaces in the stdout to pin the round-trip shape.
    assert "team" in result.stdout
    assert "ns-cli" in result.stdout

    # Delete the team.
    result = runner.invoke(
        cli_main.app,
        [
            "--backend",
            "postgres",
            "--postgres-conn-string",
            postgres_clean_dsn,
            "team",
            "delete",
            "team",
            "--namespace",
            "ns-cli",
        ],
    )
    assert result.exit_code == 0, result.stderr

    # Final list → empty again.
    result = runner.invoke(
        cli_main.app,
        [
            "--backend",
            "postgres",
            "--postgres-conn-string",
            postgres_clean_dsn,
            "--format",
            "json",
            "team",
            "list",
            "--namespace",
            "ns-cli",
        ],
    )
    assert result.exit_code == 0, result.stderr
    assert json.loads(result.stdout) == []


def test_postgres_env_var_channel(postgres_clean_dsn: str) -> None:
    """AC29 env-var channel: DB_CONN_STRING_PERSISTENCE → exit 0 happy path."""
    pytest.importorskip("typer")
    from typer.testing import CliRunner

    from akgentic.catalog.cli import main as cli_main

    runner = CliRunner()
    result = runner.invoke(
        cli_main.app,
        [
            "--backend",
            "postgres",
            "--format",
            "json",
            "team",
            "list",
            "--namespace",
            "ns-env",
        ],
        env={"DB_CONN_STRING_PERSISTENCE": postgres_clean_dsn},
    )
    assert result.exit_code == 0, result.stderr
    import json

    assert json.loads(result.stdout) == []


def test_postgres_missing_conn_string_exits_2(monkeypatch: pytest.MonkeyPatch) -> None:
    """AC10 / AC29 validation: no flag + no env var → exit 2 + pinned stderr."""
    pytest.importorskip("typer")
    from typer.testing import CliRunner

    from akgentic.catalog.cli import main as cli_main

    # Explicitly clear the env var so a developer's shell can't hide the failure.
    monkeypatch.delenv("DB_CONN_STRING_PERSISTENCE", raising=False)

    runner = CliRunner()
    result = runner.invoke(
        cli_main.app,
        ["--backend", "postgres", "team", "list", "--namespace", "foo"],
    )
    assert result.exit_code == 2
    assert "--postgres-conn-string is required when --backend=postgres" in result.stderr
