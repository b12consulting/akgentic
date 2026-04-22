"""Integration tests — CLI commands against a real server with real actors and LLM."""

from __future__ import annotations

import asyncio
import json
import threading
import time
from pathlib import Path
from typing import Any
from unittest.mock import patch

import httpx
import pytest
from click.testing import Result
from typer.testing import CliRunner

from akgentic.infra.cli.client import ApiClient
from akgentic.infra.cli.formatters import OutputFormat
from akgentic.infra.cli.main import app
from akgentic.infra.cli.repl import ChatSession
from akgentic.infra.cli.ws_client import WsClient

from ._helpers import (
    CATALOG_ENTRY_ID,
    POLL_INTERVAL_S,
    POLL_TIMEOUT_S,
    StubRenderer,
    create_team,
    has_llm_content,
)

pytestmark = [pytest.mark.integration, pytest.mark.llm]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def cli_runner() -> CliRunner:
    """Typer CliRunner for in-process CLI invocation."""
    return CliRunner()


def cli_invoke(
    runner: CliRunner,
    server_url: str,
    args: list[str],
    fmt: str | None = None,
) -> Result:
    """Invoke the CLI app with --server and optional --format flags."""
    cmd: list[str] = ["--server", server_url]
    if fmt is not None:
        cmd.extend(["--format", fmt])
    cmd.extend(args)
    return runner.invoke(app, cmd)


# ---------------------------------------------------------------------------
# Task 2: CLI Team Lifecycle Integration Tests (AC #1)
# ---------------------------------------------------------------------------


class TestCliTeamLifecycle:
    """Integration tests for CLI team commands against a real server."""

    def test_cli_team_create_and_list(
        self,
        cli_runner: CliRunner,
        cli_server: str,
    ) -> None:
        """Create a team via CLI, then list teams and verify it appears."""
        result = cli_invoke(
            cli_runner,
            cli_server,
            ["team", "create", CATALOG_ENTRY_ID],
            fmt="json",
        )
        assert result.exit_code == 0, result.output
        team_data = json.loads(result.output)
        team_id = team_data["team_id"]
        try:
            assert team_data["status"] == "running"

            result_list = cli_invoke(cli_runner, cli_server, ["team", "list"])
            assert result_list.exit_code == 0, result_list.output
            assert team_id in result_list.output
        finally:
            with httpx.Client(base_url=cli_server) as c:
                c.post(f"/teams/{team_id}/stop")
                time.sleep(0.3)

    def test_cli_team_get(
        self,
        cli_runner: CliRunner,
        cli_server: str,
    ) -> None:
        """Create a team, then get its detail via CLI."""
        result = cli_invoke(
            cli_runner,
            cli_server,
            ["team", "create", CATALOG_ENTRY_ID],
            fmt="json",
        )
        assert result.exit_code == 0
        team_data = json.loads(result.output)
        team_id = team_data["team_id"]
        try:
            result_get = cli_invoke(cli_runner, cli_server, ["team", "get", team_id])
            assert result_get.exit_code == 0
            assert team_id in result_get.output
            assert "running" in result_get.output.lower()
        finally:
            with httpx.Client(base_url=cli_server) as c:
                c.post(f"/teams/{team_id}/stop")
                time.sleep(0.3)

    def test_cli_message_and_llm_response(
        self,
        cli_runner: CliRunner,
        cli_server: str,
    ) -> None:
        """Create a team via CLI, send a message, verify LLM response in events."""
        result = cli_invoke(
            cli_runner,
            cli_server,
            ["team", "create", CATALOG_ENTRY_ID],
            fmt="json",
        )
        assert result.exit_code == 0
        team_id = json.loads(result.output)["team_id"]
        try:
            result_msg = cli_invoke(
                cli_runner,
                cli_server,
                ["message", team_id, "Say hello in one word"],
            )
            assert result_msg.exit_code == 0

            with httpx.Client(base_url=cli_server) as c:
                deadline = time.monotonic() + POLL_TIMEOUT_S
                events: list[dict[str, object]] = []
                while time.monotonic() < deadline:
                    resp = c.get(f"/teams/{team_id}/events")
                    assert resp.status_code == 200
                    events = resp.json()["events"]
                    if has_llm_content(events):
                        break
                    time.sleep(POLL_INTERVAL_S)
                else:
                    pytest.fail("Timed out waiting for LLM response via CLI message")

                assert has_llm_content(events)
        finally:
            with httpx.Client(base_url=cli_server) as c:
                c.post(f"/teams/{team_id}/stop")
                time.sleep(0.3)

    def test_cli_team_delete(
        self,
        cli_runner: CliRunner,
        cli_server: str,
    ) -> None:
        """Create a team, then delete it via CLI."""
        result = cli_invoke(
            cli_runner,
            cli_server,
            ["team", "create", CATALOG_ENTRY_ID],
            fmt="json",
        )
        assert result.exit_code == 0
        team_id = json.loads(result.output)["team_id"]

        result_del = cli_invoke(cli_runner, cli_server, ["team", "delete", team_id])
        assert result_del.exit_code == 0
        assert "deleted" in result_del.output.lower()

    def test_cli_team_restore(
        self,
        cli_runner: CliRunner,
        cli_server: str,
    ) -> None:
        """Create a team, stop via REST, restore via CLI."""
        result = cli_invoke(
            cli_runner,
            cli_server,
            ["team", "create", CATALOG_ENTRY_ID],
            fmt="json",
        )
        assert result.exit_code == 0
        team_id = json.loads(result.output)["team_id"]
        try:
            with httpx.Client(base_url=cli_server) as c:
                resp = c.post(f"/teams/{team_id}/stop")
                assert resp.status_code == 204

            result_restore = cli_invoke(cli_runner, cli_server, ["team", "restore", team_id])
            assert result_restore.exit_code == 0
            assert "running" in result_restore.output.lower()
        finally:
            with httpx.Client(base_url=cli_server) as c:
                c.post(f"/teams/{team_id}/stop")
                time.sleep(0.3)

    def test_cli_team_events(
        self,
        cli_runner: CliRunner,
        cli_server: str,
    ) -> None:
        """Create team, send message, wait for LLM, then fetch events via CLI."""
        result = cli_invoke(
            cli_runner,
            cli_server,
            ["team", "create", CATALOG_ENTRY_ID],
            fmt="json",
        )
        assert result.exit_code == 0
        team_id = json.loads(result.output)["team_id"]
        try:
            with httpx.Client(base_url=cli_server) as c:
                c.post(
                    f"/teams/{team_id}/message",
                    json={"content": "Say yes"},
                )
                deadline = time.monotonic() + POLL_TIMEOUT_S
                while time.monotonic() < deadline:
                    resp = c.get(f"/teams/{team_id}/events")
                    events = resp.json()["events"]
                    if has_llm_content(events):
                        break
                    time.sleep(POLL_INTERVAL_S)

            result_events = cli_invoke(cli_runner, cli_server, ["team", "events", team_id])
            assert result_events.exit_code == 0
            output = result_events.output.strip()
            assert len(output) > 0
            assert (
                "sequence" in output.lower()
                or "timestamp" in output.lower()
                or "event" in output.lower()
            )
        finally:
            with httpx.Client(base_url=cli_server) as c:
                c.post(f"/teams/{team_id}/stop")
                time.sleep(0.3)

    def test_cli_json_format(
        self,
        cli_runner: CliRunner,
        cli_server: str,
    ) -> None:
        """Verify --format json produces valid JSON output."""
        result = cli_invoke(
            cli_runner,
            cli_server,
            ["team", "list"],
            fmt="json",
        )
        assert result.exit_code == 0
        parsed = json.loads(result.output)
        assert isinstance(parsed, list)


# ---------------------------------------------------------------------------
# Task 3: CLI Interactive Chat Integration Test (AC #2)
# ---------------------------------------------------------------------------


_StubRenderer = StubRenderer


class TestCliChat:
    """Integration tests for the interactive chat REPL."""

    def test_cli_chat_session_lifecycle(
        self,
        cli_server: str,
        integration_client: Any,
    ) -> None:
        """Exercise ChatSession directly: connect, receive event, exit cleanly."""
        from fastapi.testclient import TestClient

        assert isinstance(integration_client, TestClient)
        team_id = create_team(integration_client)

        api_client = ApiClient(base_url=cli_server)
        ws = WsClient(
            base_url=cli_server,
            team_id=team_id,
        )

        # Track rendered events to verify WebSocket delivery
        renderer = _StubRenderer()

        session = ChatSession(
            api_client,
            ws,
            team_id,
            OutputFormat.table,
            server_url=cli_server,
            renderer=renderer,
        )

        # Stub stdin: send a message, wait for LLM to respond, then /quit.
        # After /quit the stub must block (not StopIteration) so the session
        # exits cleanly through the /quit break rather than an exception.
        def _mock_read_input(_prompt: str) -> str:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return "Say hello in one word"
            if call_count == 2:
                return "/quit"
            # After /quit: block until cancelled (session should have exited)
            threading.Event().wait(timeout=30)
            return "/quit"

        call_count = 0

        async def _run_session() -> None:
            with patch(
                "akgentic.infra.cli.repl._read_input",
                side_effect=_mock_read_input,
            ):
                await asyncio.wait_for(session.run(), timeout=30)

        try:
            asyncio.run(_run_session())
        except TimeoutError:
            pass  # session may not exit instantly after /quit

        # Cleanup — MUST run before assertion to avoid actor system hang
        api_client.close()
        integration_client.post(f"/teams/{team_id}/stop")
        time.sleep(0.5)

        # Verify at least one WebSocket event was rendered (AC #2)
        assert len(renderer.agent_messages) > 0, (
            "ChatSession received no WebSocket events — expected at least one agent message"
        )

    def test_cli_chat_create_shortcut(
        self,
        cli_runner: CliRunner,
        cli_server: str,
    ) -> None:
        """Verify --create flag creates a team before starting chat.

        Since chat runs an interactive REPL, mock asyncio.run to capture the session
        setup and verify team creation occurred.
        """
        created_team_ids: list[str] = []

        def _mock_asyncio_run(coro: Any, **kwargs: Any) -> None:
            # The chat command creates the team via REST before asyncio.run.
            # We just don't want to actually enter the REPL.
            coro.close()

        with patch("akgentic.infra.cli.main.asyncio.run", side_effect=_mock_asyncio_run):
            result = cli_invoke(
                cli_runner,
                cli_server,
                ["chat", "--create", CATALOG_ENTRY_ID],
            )

        assert result.exit_code == 0
        assert "Created team" in result.output

        # Extract team_id from "Created team <id>" output
        for line in result.output.splitlines():
            if "Created team" in line:
                team_id = line.split("Created team")[-1].strip()
                created_team_ids.append(team_id)

        # Cleanup created teams
        for tid in created_team_ids:
            with httpx.Client(base_url=cli_server) as c:
                c.post(f"/teams/{tid}/stop")
                time.sleep(0.3)


# ---------------------------------------------------------------------------
# Task 4: CLI Workspace Integration Test (AC #3)
# ---------------------------------------------------------------------------


class TestCliWorkspace:
    """Integration tests for CLI workspace commands."""

    def test_cli_workspace_upload_tree_read(
        self,
        cli_runner: CliRunner,
        cli_server: str,
        tmp_path: Path,
    ) -> None:
        """Upload a file, list workspace tree, read file back via CLI."""
        # Create a team via CLI
        result = cli_invoke(
            cli_runner,
            cli_server,
            ["team", "create", CATALOG_ENTRY_ID],
            fmt="json",
        )
        assert result.exit_code == 0
        team_id = json.loads(result.output)["team_id"]

        try:
            # Write a temp file
            test_file = tmp_path / "hello.txt"
            test_content = "Hello from CLI integration test!"
            test_file.write_text(test_content)

            # Upload via CLI
            result_upload = cli_invoke(
                cli_runner,
                cli_server,
                ["workspace", "upload", team_id, str(test_file)],
            )
            assert result_upload.exit_code == 0
            assert "Uploaded" in result_upload.output

            # Tree via CLI
            result_tree = cli_invoke(
                cli_runner,
                cli_server,
                ["workspace", "tree", team_id],
            )
            assert result_tree.exit_code == 0
            assert "hello.txt" in result_tree.output

            # Read via CLI
            result_read = cli_invoke(
                cli_runner,
                cli_server,
                ["workspace", "read", team_id, "hello.txt"],
            )
            assert result_read.exit_code == 0
            assert test_content in result_read.output
        finally:
            with httpx.Client(base_url=cli_server) as c:
                c.post(f"/teams/{team_id}/stop")
                time.sleep(0.3)
