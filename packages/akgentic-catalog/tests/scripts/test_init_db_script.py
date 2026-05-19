"""Exit-code matrix for ``akgentic.catalog.scripts.init_db.main()``.

AC30 pins three exit codes on the runnable init-container entrypoint:

* ``0`` — happy path (``DB_CONN_STRING_PERSISTENCE`` set to a reachable
  DSN, ``init_db`` applied the schema). The test asserts the
  ``catalog_entries`` table exists via a direct psycopg connection.
* ``2`` — ``DB_CONN_STRING_PERSISTENCE`` missing or empty. No Docker
  dependency — runs unconditionally.
* ``1`` — ``DB_CONN_STRING_PERSISTENCE`` well-formed but unreachable
  (e.g. port 1 on loopback). The test grep's ``Traceback`` on stderr to
  confirm the broad ``except Exception`` path ran. This test is UNIT —
  does NOT need Docker (it simulates the failure with a known-unreachable
  DSN).

The happy-path test depends on the shared ``postgres_clean_dsn`` fixture
from ``tests/conftest.py`` — Dockerless runs SKIP cleanly. Exit-code 1 +
2 tests are unconditional.
"""

from __future__ import annotations

import pytest

from akgentic.catalog.scripts import init_db as init_db_script


def test_main_exits_0_on_success(
    postgres_clean_dsn: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """AC14: happy-path DSN → exit 0 + ``catalog_entries`` table exists."""
    monkeypatch.setenv("DB_CONN_STRING_PERSISTENCE", postgres_clean_dsn)

    with pytest.raises(SystemExit) as exc_info:
        init_db_script.main()
    assert exc_info.value.code == 0

    # Verify the schema is live via a direct psycopg connection.
    import psycopg

    with psycopg.connect(postgres_clean_dsn) as conn, conn.cursor() as cur:
        cur.execute("SELECT to_regclass('public.catalog_entries')")
        row = cur.fetchone()
        assert row is not None
        assert row[0] is not None, (
            "catalog_entries table missing after init_db main() — "
            "init_db was not invoked on the happy path."
        )


def test_main_exits_2_when_env_var_missing(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """AC15: missing ``DB_CONN_STRING_PERSISTENCE`` → exit 2 + pinned diagnostic."""
    monkeypatch.delenv("DB_CONN_STRING_PERSISTENCE", raising=False)

    with pytest.raises(SystemExit) as exc_info:
        init_db_script.main()
    assert exc_info.value.code == 2

    captured = capsys.readouterr()
    assert "DB_CONN_STRING_PERSISTENCE environment variable is required" in captured.err


def test_main_exits_2_when_env_var_empty(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """AC15: empty ``DB_CONN_STRING_PERSISTENCE`` is treated as missing (exit 2)."""
    monkeypatch.setenv("DB_CONN_STRING_PERSISTENCE", "")

    with pytest.raises(SystemExit) as exc_info:
        init_db_script.main()
    assert exc_info.value.code == 2

    captured = capsys.readouterr()
    assert "DB_CONN_STRING_PERSISTENCE environment variable is required" in captured.err


def test_main_exits_1_on_unreachable_dsn(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """AC16: valid-scheme-but-unreachable DSN → exit 1 + stderr traceback."""
    # These guards make the test reachable only when [postgres] is present;
    # without nagra we cannot distinguish "missing extra" from the infra
    # failure the test targets.
    pytest.importorskip("nagra")
    pytest.importorskip("psycopg")

    # Port 1 on loopback is reserved (tcpmux) — connection refused or
    # permission denied either way, which is what we want to trigger the
    # broad except path in main().
    unreachable_dsn = "postgresql://postgres:wrong@127.0.0.1:1/nonexistent_db"
    monkeypatch.setenv("DB_CONN_STRING_PERSISTENCE", unreachable_dsn)

    with pytest.raises(SystemExit) as exc_info:
        init_db_script.main()
    assert exc_info.value.code == 1

    captured = capsys.readouterr()
    # traceback.print_exc() writes "Traceback (most recent call last):" to stderr.
    assert "Traceback" in captured.err
