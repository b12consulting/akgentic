"""CLI E2E tests — real running server, real CLI binary (Story 9.8, AC #14-#20).

Tests invoke ``ak-infra`` via subprocess.run and verify output.
All tests hit a live server via the CLI's --server flag.
"""

from __future__ import annotations

import re
import subprocess
import time
from typing import Any

import httpx
import pytest

from tests.e2e.conftest import (
    CATALOG_ENTRY_ID,
    create_team,
    delete_team,
    has_manager_response,
    poll_until,
)

pytestmark = [pytest.mark.e2e]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _run_cli(
    args: list[str],
    server_url: str,
    timeout: float = 30.0,
) -> subprocess.CompletedProcess[str]:
    """Run ak-infra CLI command with --server flag."""
    cmd = ["ak-infra", "--server", server_url] + args
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _extract_team_id_from_output(output: str) -> str | None:
    """Extract a UUID from CLI output."""
    # Look for UUID pattern
    match = re.search(
        r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
        output,
        re.IGNORECASE,
    )
    return match.group(0) if match else None


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_e2e_cli_team_list(e2e_server_ready: str) -> None:
    """AC #14: ak-infra team list produces output."""
    result = _run_cli(["team", "list"], e2e_server_ready)
    assert result.returncode == 0, f"CLI failed: {result.stderr}"


def test_e2e_cli_team_create(
    e2e_server_ready: str,
    e2e_http_client: httpx.Client,
) -> None:
    """AC #15: ak-infra team create produces team output with ID."""
    team_id: str | None = None
    try:
        result = _run_cli(["team", "create", CATALOG_ENTRY_ID], e2e_server_ready)
        assert result.returncode == 0, f"CLI failed: {result.stderr}"

        # Extract team_id from output
        team_id = _extract_team_id_from_output(result.stdout)
        assert team_id is not None, f"No team ID found in output: {result.stdout}"
    finally:
        if team_id:
            delete_team(e2e_http_client, team_id)


def test_e2e_cli_team_get(
    e2e_server_ready: str,
    e2e_http_client: httpx.Client,
) -> None:
    """AC #16: ak-infra team get shows team details."""
    team_id: str | None = None
    try:
        team_id = create_team(e2e_http_client)
        result = _run_cli(["team", "get", team_id], e2e_server_ready)
        assert result.returncode == 0, f"CLI failed: {result.stderr}"
        # Output should contain the team_id
        assert team_id in result.stdout, f"team_id not in output: {result.stdout}"
    finally:
        if team_id:
            delete_team(e2e_http_client, team_id)


def test_e2e_cli_message(
    e2e_server_ready: str,
    e2e_http_client: httpx.Client,
) -> None:
    """AC #17: ak-infra message sends a message."""
    team_id: str | None = None
    try:
        team_id = create_team(e2e_http_client)
        result = _run_cli(["message", team_id, "hello from CLI"], e2e_server_ready)
        assert result.returncode == 0, f"CLI failed: {result.stderr}"
    finally:
        if team_id:
            delete_team(e2e_http_client, team_id)


def test_e2e_cli_team_events(
    e2e_server_ready: str,
    e2e_http_client: httpx.Client,
) -> None:
    """AC #18: ak-infra team events shows event data."""
    team_id: str | None = None
    try:
        team_id = create_team(e2e_http_client)

        # Send message and wait for response via REST
        e2e_http_client.post(f"/teams/{team_id}/message", json={"content": "hello"})

        def _check() -> bool:
            resp = e2e_http_client.get(f"/teams/{team_id}/events")
            events: list[dict[str, Any]] = resp.json()["events"]
            return has_manager_response(events)

        poll_until(
            _check, timeout=60.0, interval=1.0, message="Timed out waiting for manager response"
        )

        result = _run_cli(["team", "events", team_id], e2e_server_ready)
        assert result.returncode == 0, f"CLI failed: {result.stderr}"
        # Output should contain event data
        assert len(result.stdout.strip()) > 0, "Events output should not be empty"
    finally:
        if team_id:
            delete_team(e2e_http_client, team_id)


def test_e2e_cli_slash_commands(
    e2e_server_ready: str,
    e2e_http_client: httpx.Client,
) -> None:
    """AC #19: Verify slash command handlers work with real data.

    Interactive REPL testing via subprocess is impractical (prompt_toolkit).
    Instead, we test the underlying command handler functions directly with
    real server data, which validates the same code paths.
    """
    import asyncio
    from types import SimpleNamespace

    from akgentic.infra.cli.client import ApiClient
    from akgentic.infra.cli.repl_commands import (
        _agents_handler,
        _files_handler,
        _history_handler,
        _info_handler,
    )

    team_id: str | None = None
    try:
        team_id = create_team(e2e_http_client)

        # Create a real ApiClient pointing to the test server
        api_client = ApiClient(base_url=e2e_server_ready)

        # Create a minimal session-like object with real client (no mocks).
        # Using SimpleNamespace as a duck-typed stand-in for ChatSession.
        session = SimpleNamespace(
            client=api_client,
            team_id=team_id,
            _render_event=lambda ev: None,
        )

        # /info (replaces removed /status)
        asyncio.run(_info_handler("", session))  # type: ignore[arg-type]

        # /agents — wait briefly for StartMessage events
        time.sleep(2)
        asyncio.run(_agents_handler("", session))  # type: ignore[arg-type]

        # /files
        asyncio.run(_files_handler("", session))  # type: ignore[arg-type]

        # /history
        asyncio.run(_history_handler("", session))  # type: ignore[arg-type]

        api_client.close()
    finally:
        if team_id:
            delete_team(e2e_http_client, team_id)


def test_e2e_cli_team_delete(
    e2e_server_ready: str,
    e2e_http_client: httpx.Client,
) -> None:
    """AC #20: ak-infra team delete removes the team."""
    team_id: str | None = None
    team_id = create_team(e2e_http_client)
    try:
        result = _run_cli(["team", "delete", team_id], e2e_server_ready)
        assert result.returncode == 0, f"CLI failed: {result.stderr}"

        # Verify team is gone via REST
        resp = e2e_http_client.get(f"/teams/{team_id}")
        assert resp.status_code == 404
        team_id = None  # Already deleted
    finally:
        if team_id:
            delete_team(e2e_http_client, team_id)
