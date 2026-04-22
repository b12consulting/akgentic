"""End-to-end smoke test for the init-container script (AC #14).

Drives the script via ``python -m akgentic.team.scripts.init_db`` against
the session-scoped Testcontainers Postgres fixture. The unit tests for
exit codes 2 and 1 (and the patched success path) live alongside the
script at ``tests/scripts/test_init_db_script.py`` so they can run even
when ``nagra`` / ``testcontainers[postgres]`` are absent.
"""

from __future__ import annotations

import os
import subprocess
import sys


def test_python_dash_m_against_container_exits_zero(
    postgres_conn_string: str,
) -> None:
    """Running the script as a module against the testcontainer succeeds."""
    env = dict(os.environ)
    env["DB_CONN_STRING_PERSISTENCE"] = postgres_conn_string
    result = subprocess.run(
        [sys.executable, "-m", "akgentic.team.scripts.init_db"],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, (
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )
