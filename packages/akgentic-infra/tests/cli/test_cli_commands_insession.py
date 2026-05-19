"""Tests for in-session slash command registry, handlers, and ChatSession integration."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from akgentic.infra.cli.client import (
    ApiError,
    CatalogTeamInfo,
    EventInfo,
    TeamInfo,
    WorkspaceEntry,
    WorkspaceTreeInfo,
    WorkspaceUploadInfo,
)
from akgentic.infra.cli.repl_commands import (
    CommandRegistry,
    _agents_handler,
    _catalog_handler,
    _create_handler,
    _delete_handler,
    _events_handler,
    _files_handler,
    _help_handler,
    _history_handler,
    _info_handler,
    _read_handler,
    _reconnect_handler,
    _restore_handler,
    _stop_handler,
    _switch_handler,
    _teams_handler,
    _upload_handler,
    build_default_registry,
)
from akgentic.infra.cli.ws_client import WsConnectionError
from tests.fixtures.events import (
    _make_proxy,
    build_start_message,
    make_sent_message,
)

from .conftest import captured_renderer as _captured_renderer
from .conftest import make_session as _make_session

_PROMPT_PATH = "prompt_toolkit.PromptSession.prompt"

# -- Helpers --

def _mock_client(**overrides: Any) -> MagicMock:
    """Build a mock ApiClient with in-session-specific defaults."""
    mock = MagicMock()
    mock.get_events.return_value = []
    mock.send_message.return_value = None
    mock.get_team.return_value = TeamInfo(
        team_id="t1",
        name="Test Team",
        status="running",
        user_id="user-1",
        created_at="2026-01-01T00:00:00",
        updated_at="2026-01-01T00:00:00",
    )
    mock.workspace_tree.return_value = WorkspaceTreeInfo(
        team_id="t1",
        path="/",
        entries=[
            WorkspaceEntry(name="docs", is_dir=True, size=0),
            WorkspaceEntry(name="readme.md", is_dir=False, size=42),
        ],
    )
    mock.workspace_read.return_value = b"file content here"
    mock.workspace_upload.return_value = WorkspaceUploadInfo(path="test.txt", size=5)
    mock.stop_team.return_value = None
    mock.delete_team.return_value = None
    mock.list_catalog_teams.return_value = []
    mock.restore_team.return_value = TeamInfo(
        team_id="t1",
        name="Test Team",
        status="running",
        user_id="user-1",
        created_at="2026-01-01T00:00:00",
        updated_at="2026-01-01T00:00:00",
    )
    for k, v in overrides.items():
        setattr(mock, k, v)
    return mock


# =============================================================================
# Task 8.1: Test CommandRegistry
# =============================================================================


class TestCommandRegistryDispatch:
    """Test dispatch returns True for registered commands, False for non-commands."""

    async def test_dispatch_returns_true_for_registered_command(self) -> None:
        registry = CommandRegistry()
        handler = AsyncMock()
        registry.register("test", handler, "Test command", "/test")
        session = _make_session()

        result = await registry.dispatch("/test", session)

        assert result is True
        handler.assert_called_once_with("", session)

    async def test_dispatch_returns_false_for_non_command(self) -> None:
        registry = CommandRegistry()
        session = _make_session()

        result = await registry.dispatch("hello world", session)

        assert result is False

    async def test_dispatch_passes_args_to_handler(self) -> None:
        registry = CommandRegistry()
        handler = AsyncMock()
        registry.register("test", handler, "Test", "/test <arg>")
        session = _make_session()

        await registry.dispatch("/test some args here", session)

        handler.assert_called_once_with("some args here", session)

    async def test_unknown_command_prints_error_returns_true(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        registry = CommandRegistry()
        session = _make_session()

        result = await registry.dispatch("/xxx", session)

        assert result is True
        out = capsys.readouterr().out
        assert "Unknown command: /xxx" in out
        assert "/help" in out

    async def test_dispatch_strips_whitespace(self) -> None:
        registry = CommandRegistry()
        handler = AsyncMock()
        registry.register("test", handler, "Test", "/test")
        session = _make_session()

        result = await registry.dispatch("  /test  ", session)

        assert result is True
        handler.assert_called_once()


class TestHelpCommand:
    """Test /help lists all registered commands."""

    async def test_help_lists_all_commands(self, capsys: pytest.CaptureFixture[str]) -> None:
        session = _make_session()
        registry = build_default_registry()
        session.command_registry = registry

        await _help_handler("", session)

        out = capsys.readouterr().out
        assert "/teams" in out
        assert "/create" in out
        assert "/delete" in out
        assert "/info" in out
        assert "/events" in out
        assert "/agents" in out
        assert "/history" in out
        assert "/files" in out
        assert "/read" in out
        assert "/upload" in out
        assert "/stop" in out
        assert "/restore" in out
        assert "/switch" in out
        assert "/help" in out
        assert "/status" not in out


# =============================================================================
# Task 8.2: Test each command handler in isolation
# =============================================================================


class TestTeamsHandler:
    async def test_lists_teams(self, capsys: pytest.CaptureFixture[str]) -> None:
        client = _mock_client()
        client.list_teams.return_value = [
            TeamInfo(
                team_id="t1", name="Team A", status="running",
                user_id="u1", created_at="2026-01-01", updated_at="2026-01-01",
            ),
            TeamInfo(
                team_id="t2", name="Team B", status="stopped",
                user_id="u1", created_at="2026-01-01", updated_at="2026-01-01",
            ),
        ]
        session = _make_session(client=client)

        await _teams_handler("", session)

        out = capsys.readouterr().out
        assert "Team A" in out
        assert "Team B" in out
        assert "running" in out
        assert "stopped" in out

    async def test_highlights_current_team(self, capsys: pytest.CaptureFixture[str]) -> None:
        client = _mock_client()
        client.list_teams.return_value = [
            TeamInfo(
                team_id="t1", name="Team A", status="running",
                user_id="u1", created_at="2026-01-01", updated_at="2026-01-01",
            ),
            TeamInfo(
                team_id="t2", name="Team B", status="stopped",
                user_id="u1", created_at="2026-01-01", updated_at="2026-01-01",
            ),
        ]
        session = _make_session(client=client)

        await _teams_handler("", session)

        out = capsys.readouterr().out
        lines = out.strip().split("\n")
        # The line with t1 should have (current) marker
        t1_line = [line for line in lines if "t1" in line][0]
        assert "(current)" in t1_line
        t2_line = [line for line in lines if "t2" in line][0]
        assert "(current)" not in t2_line

    async def test_empty_teams_list(self, capsys: pytest.CaptureFixture[str]) -> None:
        client = _mock_client()
        client.list_teams.return_value = []
        session = _make_session(client=client)

        await _teams_handler("", session)

        out = capsys.readouterr().out
        assert "No teams found" in out

    async def test_handles_api_error(self) -> None:
        client = _mock_client()
        client.list_teams.side_effect = ApiError(500, "test error")
        renderer, buf = _captured_renderer()
        session = _make_session(client=client, renderer=renderer)

        await _teams_handler("", session)

        assert "Error" in buf.getvalue()


class TestCreateHandler:
    async def test_creates_and_switches(self, capsys: pytest.CaptureFixture[str]) -> None:
        client = _mock_client()
        client.create_team.return_value = TeamInfo(
            team_id="new-t", name="New Team", status="running",
            user_id="u1", created_at="2026-01-01", updated_at="2026-01-01",
        )
        session = _make_session(client=client)
        session._receive_task = asyncio.create_task(asyncio.sleep(100))

        await _create_handler("my-catalog-entry", session)

        out = capsys.readouterr().out
        assert "Created team: New Team" in out
        assert "new-t" in out
        client.create_team.assert_called_once_with("my-catalog-entry")
        # Should have auto-switched via conn.switch_team
        assert session.team_id == "new-t"
        session.conn.switch_team.assert_called_once_with("new-t")

    async def test_missing_arg_shows_usage(self, capsys: pytest.CaptureFixture[str]) -> None:
        session = _make_session()

        await _create_handler("", session)

        out = capsys.readouterr().out
        assert "Usage" in out

    async def test_handles_api_error(self) -> None:
        client = _mock_client()
        client.create_team.side_effect = ApiError(500, "test error")
        renderer, buf = _captured_renderer()
        session = _make_session(client=client, renderer=renderer)

        await _create_handler("my-entry", session)

        assert "Error" in buf.getvalue()


class TestDeleteHandler:
    async def test_delete_confirms_and_proceeds(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        client = _mock_client()
        session = _make_session(client=client)

        with patch("akgentic.infra.cli.repl_commands.builtins.input", return_value="y"):
            await _delete_handler("", session)

        out = capsys.readouterr().out
        assert "deleted" in out.lower()
        client.delete_team.assert_called_once_with("t1")

    async def test_delete_aborts_on_no(self, capsys: pytest.CaptureFixture[str]) -> None:
        client = _mock_client()
        session = _make_session(client=client)

        with patch("akgentic.infra.cli.repl_commands.builtins.input", return_value="n"):
            await _delete_handler("", session)

        out = capsys.readouterr().out
        assert "Aborted" in out
        client.delete_team.assert_not_called()

    async def test_defaults_to_current_team(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        client = _mock_client()
        session = _make_session(client=client)

        with patch("akgentic.infra.cli.repl_commands.builtins.input", return_value="y"):
            await _delete_handler("", session)

        client.get_team.assert_called_once_with("t1")
        client.delete_team.assert_called_once_with("t1")

    async def test_delete_current_team_shows_switch_msg(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        client = _mock_client()
        session = _make_session(client=client)

        with patch("akgentic.infra.cli.repl_commands.builtins.input", return_value="y"):
            await _delete_handler("", session)

        out = capsys.readouterr().out
        assert "/switch" in out or "/teams" in out

    async def test_handles_api_error(self) -> None:
        client = _mock_client()
        client.get_team.side_effect = ApiError(500, "test error")
        renderer, buf = _captured_renderer()
        session = _make_session(client=client, renderer=renderer)

        await _delete_handler("some-id", session)

        assert "Error" in buf.getvalue()


class TestInfoHandler:
    async def test_info_current_team(self, capsys: pytest.CaptureFixture[str]) -> None:
        client = _mock_client()
        session = _make_session(client=client)

        await _info_handler("", session)

        out = capsys.readouterr().out
        assert "Test Team" in out
        assert "running" in out
        assert "user-1" in out
        client.get_team.assert_called_once_with("t1")

    async def test_info_specific_team(self, capsys: pytest.CaptureFixture[str]) -> None:
        client = _mock_client()
        session = _make_session(client=client)

        await _info_handler("t2", session)

        client.get_team.assert_called_once_with("t2")

    async def test_handles_api_error(self) -> None:
        client = _mock_client()
        client.get_team.side_effect = ApiError(500, "test error")
        renderer, buf = _captured_renderer()
        session = _make_session(client=client, renderer=renderer)

        await _info_handler("", session)

        assert "Error" in buf.getvalue()


class TestEventsHandler:
    async def test_default_limit(self, capsys: pytest.CaptureFixture[str]) -> None:
        client = _mock_client()
        events = [
            EventInfo(
                team_id="t1", sequence=i,
                event={"type": f"event-{i}"}, timestamp="2026-01-01T00:00:00",
            )
            for i in range(25)
        ]
        client.get_events.return_value = events
        session = _make_session(client=client)

        await _events_handler("", session)

        out = capsys.readouterr().out
        # Default limit 20, so first 5 should be missing
        assert "event-0" not in out
        assert "event-4" not in out
        assert "event-5" in out
        assert "event-24" in out

    async def test_custom_limit(self, capsys: pytest.CaptureFixture[str]) -> None:
        client = _mock_client()
        events = [
            EventInfo(
                team_id="t1", sequence=i,
                event={"type": f"event-{i}"}, timestamp="2026-01-01T00:00:00",
            )
            for i in range(25)
        ]
        client.get_events.return_value = events
        session = _make_session(client=client)

        await _events_handler("10", session)

        out = capsys.readouterr().out
        assert "event-14" not in out
        assert "event-15" in out
        assert "event-24" in out

    async def test_invalid_limit(self, capsys: pytest.CaptureFixture[str]) -> None:
        session = _make_session()

        await _events_handler("abc", session)

        out = capsys.readouterr().out
        assert "Usage" in out

    async def test_zero_limit(self, capsys: pytest.CaptureFixture[str]) -> None:
        session = _make_session()

        await _events_handler("0", session)

        out = capsys.readouterr().out
        assert "Usage" in out

    async def test_negative_limit(self, capsys: pytest.CaptureFixture[str]) -> None:
        session = _make_session()

        await _events_handler("-3", session)

        out = capsys.readouterr().out
        assert "Usage" in out

    async def test_no_events(self, capsys: pytest.CaptureFixture[str]) -> None:
        client = _mock_client()
        client.get_events.return_value = []
        session = _make_session(client=client)

        await _events_handler("", session)

        out = capsys.readouterr().out
        # No events = no output (no crash)
        assert out == ""

    async def test_handles_api_error(self) -> None:
        client = _mock_client()
        client.get_events.side_effect = ApiError(500, "test error")
        renderer, buf = _captured_renderer()
        session = _make_session(client=client, renderer=renderer)

        await _events_handler("", session)

        assert "Error" in buf.getvalue()


class TestAgentsHandler:
    async def test_shows_agents_from_events(self, capsys: pytest.CaptureFixture[str]) -> None:
        from akgentic.core.agent_config import BaseConfig

        client = _mock_client()
        typed_start = build_start_message(
            config=BaseConfig(name="@Manager", role="Manager"),
            sender=_make_proxy(name="@Manager", role="Manager"),
        )
        client.get_events.return_value = [
            EventInfo(
                team_id="t1",
                sequence=1,
                timestamp="2026-01-01T00:00:00",
                event=typed_start,
            ),
        ]
        session = _make_session(client=client)

        await _agents_handler("", session)

        out = capsys.readouterr().out
        assert "@Manager" in out
        assert "Manager" in out

    async def test_no_agents_found(self, capsys: pytest.CaptureFixture[str]) -> None:
        client = _mock_client()
        session = _make_session(client=client)

        await _agents_handler("", session)

        out = capsys.readouterr().out
        assert "No agents found" in out

    async def test_handles_api_error(self) -> None:
        client = _mock_client()
        client.get_events.side_effect = ApiError(500, "test error")
        renderer, buf = _captured_renderer()
        session = _make_session(client=client, renderer=renderer)

        await _agents_handler("", session)

        assert "Error" in buf.getvalue()


class TestHistoryHandler:
    async def test_shows_history_default_limit(self) -> None:
        events = [
            EventInfo(
                team_id="t1",
                sequence=i,
                event=make_sent_message(content=f"msg-{i}"),
                timestamp="2026-01-01T00:00:00",
            )
            for i in range(25)
        ]
        client = _mock_client()
        client.get_events.return_value = events
        renderer, buf = _captured_renderer()
        session = _make_session(client=client, renderer=renderer)

        await _history_handler("", session)

        out = buf.getvalue()
        # Default limit 20, so first 5 should be missing
        assert "msg-0" not in out
        assert "msg-4" not in out
        assert "msg-5" in out
        assert "msg-24" in out

    async def test_custom_limit(self) -> None:
        events = [
            EventInfo(
                team_id="t1",
                sequence=i,
                event=make_sent_message(content=f"msg-{i}"),
                timestamp="2026-01-01T00:00:00",
            )
            for i in range(25)
        ]
        client = _mock_client()
        client.get_events.return_value = events
        renderer, buf = _captured_renderer()
        session = _make_session(client=client, renderer=renderer)

        await _history_handler("10", session)

        out = buf.getvalue()
        assert "msg-14" not in out
        assert "msg-15" in out
        assert "msg-24" in out

    async def test_invalid_limit(self, capsys: pytest.CaptureFixture[str]) -> None:
        session = _make_session()

        await _history_handler("abc", session)

        out = capsys.readouterr().out
        assert "Usage" in out

    async def test_zero_limit(self, capsys: pytest.CaptureFixture[str]) -> None:
        session = _make_session()

        await _history_handler("0", session)

        out = capsys.readouterr().out
        assert "Usage" in out

    async def test_negative_limit(self, capsys: pytest.CaptureFixture[str]) -> None:
        session = _make_session()

        await _history_handler("-5", session)

        out = capsys.readouterr().out
        assert "Usage" in out

    async def test_filters_non_displayable(self) -> None:
        events = [
            EventInfo(
                team_id="t1",
                sequence=1,
                # StateChangedMessage has no factory -- acceptable per story notes
                event={"__model__": "StateChangedMessage", "state": "running"},
                timestamp="2026-01-01T00:00:00",
            ),
            EventInfo(
                team_id="t1",
                sequence=2,
                event=make_sent_message(content="visible"),
                timestamp="2026-01-01T00:00:00",
            ),
        ]
        client = _mock_client()
        client.get_events.return_value = events
        renderer, buf = _captured_renderer()
        session = _make_session(client=client, renderer=renderer)

        await _history_handler("", session)

        out = buf.getvalue()
        assert "visible" in out
        assert "StateChanged" not in out

    async def test_handles_api_error(self) -> None:
        client = _mock_client()
        client.get_events.side_effect = ApiError(500, "test error")
        renderer, buf = _captured_renderer()
        session = _make_session(client=client, renderer=renderer)

        await _history_handler("", session)

        assert "Error" in buf.getvalue()


class TestFilesHandler:
    async def test_shows_file_tree(self, capsys: pytest.CaptureFixture[str]) -> None:
        client = _mock_client()
        session = _make_session(client=client)

        await _files_handler("", session)

        out = capsys.readouterr().out
        assert "docs" in out
        assert "readme.md" in out
        assert "42 bytes" in out

    async def test_empty_workspace(self, capsys: pytest.CaptureFixture[str]) -> None:
        client = _mock_client()
        client.workspace_tree.return_value = WorkspaceTreeInfo(team_id="t1", path="/", entries=[])
        session = _make_session(client=client)

        await _files_handler("", session)

        out = capsys.readouterr().out
        assert "empty" in out.lower()

    async def test_handles_api_error(self) -> None:
        client = _mock_client()
        client.workspace_tree.side_effect = ApiError(500, "test error")
        renderer, buf = _captured_renderer()
        session = _make_session(client=client, renderer=renderer)

        await _files_handler("", session)

        assert "Error" in buf.getvalue()


class TestReadHandler:
    async def test_reads_file(self, capsys: pytest.CaptureFixture[str]) -> None:
        client = _mock_client()
        session = _make_session(client=client)

        await _read_handler("readme.md", session)

        out = capsys.readouterr().out
        assert "file content here" in out

    async def test_no_arg_shows_usage(self, capsys: pytest.CaptureFixture[str]) -> None:
        session = _make_session()

        await _read_handler("", session)

        out = capsys.readouterr().out
        assert "Usage" in out

    async def test_binary_file(self, capsys: pytest.CaptureFixture[str]) -> None:
        client = _mock_client()
        client.workspace_read.return_value = bytes(range(256))
        session = _make_session(client=client)

        await _read_handler("binary.bin", session)

        out = capsys.readouterr().out
        assert "binary" in out.lower() or "bytes" in out.lower()

    async def test_handles_api_error(self) -> None:
        client = _mock_client()
        client.workspace_read.side_effect = ApiError(500, "test error")
        renderer, buf = _captured_renderer()
        session = _make_session(client=client, renderer=renderer)

        await _read_handler("test.txt", session)

        assert "Error" in buf.getvalue()


class TestUploadHandler:
    async def test_uploads_file(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        f = tmp_path / "test.txt"
        f.write_text("hello")
        client = _mock_client()
        session = _make_session(client=client)

        await _upload_handler(str(f), session)

        out = capsys.readouterr().out
        assert "Uploaded" in out
        client.workspace_upload.assert_called_once()

    async def test_no_arg_shows_usage(self, capsys: pytest.CaptureFixture[str]) -> None:
        session = _make_session()

        await _upload_handler("", session)

        out = capsys.readouterr().out
        assert "Usage" in out

    async def test_missing_file(self, capsys: pytest.CaptureFixture[str]) -> None:
        session = _make_session()

        await _upload_handler("/nonexistent/file.txt", session)

        out = capsys.readouterr().out
        assert "Error" in out or "not a file" in out

    async def test_handles_api_error(self, tmp_path: Path) -> None:
        f = tmp_path / "test.txt"
        f.write_text("hello")
        client = _mock_client()
        client.workspace_upload.side_effect = ApiError(500, "test error")
        renderer, buf = _captured_renderer()
        session = _make_session(client=client, renderer=renderer)

        await _upload_handler(str(f), session)

        assert "Error" in buf.getvalue()


class TestStopHandler:
    async def test_stops_team(self, capsys: pytest.CaptureFixture[str]) -> None:
        client = _mock_client()
        session = _make_session(client=client)

        await _stop_handler("", session)

        out = capsys.readouterr().out
        assert "Team t1 stopped." in out
        client.stop_team.assert_called_once_with("t1")
        client.delete_team.assert_not_called()

    async def test_handles_api_error(self) -> None:
        client = _mock_client()
        client.stop_team.side_effect = ApiError(500, "test error")
        renderer, buf = _captured_renderer()
        session = _make_session(client=client, renderer=renderer)

        await _stop_handler("", session)

        assert "Error" in buf.getvalue()


class TestRestoreHandler:
    async def test_restores_current_team_no_arg(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        client = _mock_client()
        session = _make_session(client=client)
        session._receive_task = asyncio.create_task(asyncio.sleep(100))

        await _restore_handler("", session)

        out = capsys.readouterr().out
        assert "Team t1 restored. Live events resumed." in out
        client.restore_team.assert_called_once_with("t1")
        # WebSocket reconnected via conn.switch_team for the same team
        session.conn.switch_team.assert_called_once_with("t1")
        assert session.team_id == "t1"

    async def test_restores_different_team_and_switches(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        client = _mock_client()
        session = _make_session(client=client)
        session._receive_task = asyncio.create_task(asyncio.sleep(100))

        await _restore_handler("t2", session)

        out = capsys.readouterr().out
        assert "Team t2 restored. Live events resumed." in out
        client.restore_team.assert_called_once_with("t2")
        # Should have auto-switched via conn.switch_team
        session.conn.switch_team.assert_called_once_with("t2")
        assert session.team_id == "t2"

    async def test_restores_same_team_id_reconnects_ws(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        client = _mock_client()
        session = _make_session(client=client)
        session._receive_task = asyncio.create_task(asyncio.sleep(100))

        await _restore_handler("t1", session)

        out = capsys.readouterr().out
        assert "Team t1 restored. Live events resumed." in out
        client.restore_team.assert_called_once_with("t1")
        # WebSocket reconnected even for same team
        session.conn.switch_team.assert_called_once_with("t1")
        assert session.team_id == "t1"

    async def test_handles_api_error(self) -> None:
        client = _mock_client()
        client.restore_team.side_effect = ApiError(500, "test error")
        renderer, buf = _captured_renderer()
        session = _make_session(client=client, renderer=renderer)

        await _restore_handler("", session)

        assert "Error" in buf.getvalue()


class TestSwitchHandler:
    async def test_switches_team(self, capsys: pytest.CaptureFixture[str]) -> None:
        client = _mock_client()
        session = _make_session(client=client)
        session._receive_task = asyncio.create_task(asyncio.sleep(100))

        await _switch_handler("t2", session)

        session.conn.switch_team.assert_called_once_with("t2")
        assert session.team_id == "t2"
        # After switch, team info should be refreshed
        assert session._state.team_name == "Test Team"
        assert session._state.team_status == "running"

    async def test_no_arg_shows_usage(self, capsys: pytest.CaptureFixture[str]) -> None:
        session = _make_session()

        await _switch_handler("", session)

        out = capsys.readouterr().out
        assert "Usage" in out

    async def test_switch_fails_old_connection_untouched(self) -> None:
        client = _mock_client()
        renderer, buf = _captured_renderer()
        session = _make_session(client=client, renderer=renderer)
        session.conn.switch_team = AsyncMock(
            side_effect=WsConnectionError("test error")
        )

        await _switch_handler("bad-team", session)

        assert "Switch failed" in buf.getvalue()
        assert session.team_id == "t1"  # unchanged


# =============================================================================
# Task 8.3: Test integration with ChatSession._input_loop
# =============================================================================


class TestChatSessionCommandIntegration:
    async def test_info_dispatched_not_sent(self, capsys: pytest.CaptureFixture[str]) -> None:
        client = _mock_client()
        session = _make_session(client=client)

        with patch(
            _PROMPT_PATH,
            side_effect=["/info", "/quit"],
        ):
            await session.run()

        # /info should have triggered get_team, not send_message
        # get_team is called twice: once for the header bar, once for /info
        assert client.get_team.call_count == 2
        client.send_message.assert_not_called()

    async def test_regular_text_sent_as_message(self, capsys: pytest.CaptureFixture[str]) -> None:
        client = _mock_client()
        session = _make_session(client=client)

        with patch(
            _PROMPT_PATH,
            side_effect=["hello there", "/quit"],
        ):
            await session.run()

        client.send_message.assert_called_once_with("t1", "hello there")

    async def test_quit_still_exits(self, capsys: pytest.CaptureFixture[str]) -> None:
        client = _mock_client()
        session = _make_session(client=client)

        with patch(
            _PROMPT_PATH,
            side_effect=["/quit"],
        ):
            await session.run()

        out = capsys.readouterr().out
        assert "Session closed." in out

    async def test_unknown_slash_command_not_sent(self, capsys: pytest.CaptureFixture[str]) -> None:
        client = _mock_client()
        session = _make_session(client=client)

        with patch(
            _PROMPT_PATH,
            side_effect=["/unknown", "/quit"],
        ):
            await session.run()

        out = capsys.readouterr().out
        assert "Unknown command: /unknown" in out
        client.send_message.assert_not_called()


# =============================================================================
# Story 10.3: Catalog browsing tests
# =============================================================================


class TestCatalogHandler:
    async def test_catalog_handler_lists_entries(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        client = _mock_client()
        client.list_catalog_teams.return_value = [
            CatalogTeamInfo(
                id="research-team",
                name="Research Team",
                description="Multi-agent research and analysis team",
            ),
            CatalogTeamInfo(
                id="code-review",
                name="Code Review",
                description="Automated code review with expert agents",
            ),
        ]
        session = _make_session(client=client)

        await _catalog_handler("", session)

        out = capsys.readouterr().out
        assert "Available team templates:" in out
        assert "research-team" in out
        assert "Research Team" in out
        assert "Multi-agent research and analysis team" in out
        assert "code-review" in out
        assert "Code Review" in out
        assert "Automated code review with expert agents" in out

    async def test_catalog_handler_empty(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        client = _mock_client()
        client.list_catalog_teams.return_value = []
        session = _make_session(client=client)

        await _catalog_handler("", session)

        out = capsys.readouterr().out
        assert "No team templates found." in out

    async def test_catalog_handler_error(self) -> None:
        client = _mock_client()
        client.list_catalog_teams.side_effect = ApiError(500, "test error")
        renderer, buf = _captured_renderer()
        session = _make_session(client=client, renderer=renderer)

        await _catalog_handler("", session)

        assert "Error fetching catalog" in buf.getvalue()


class TestListCatalogTeamsClient:
    def test_list_catalog_teams_parses_response(self) -> None:
        """Verify list_catalog_teams parses JSON array into CatalogTeamInfo list."""
        from akgentic.infra.cli.client import ApiClient

        mock_response = MagicMock()
        mock_response.json.return_value = [
            {
                "id": "team",
                "kind": "team",
                "namespace": "research-team",
                "model_type": "akgentic.team.models.TeamCard",
                "description": "Multi-agent research and analysis team",
                "payload": {"name": "Research Team"},
            },
        ]
        mock_response.is_success = True

        client = ApiClient.__new__(ApiClient)
        client._client = MagicMock()
        client._client.request.return_value = mock_response

        result = client.list_catalog_teams()

        assert len(result) == 1
        assert result[0].id == "research-team"
        assert result[0].name == "Research Team"
        assert result[0].description == "Multi-agent research and analysis team"


# =============================================================================
# Task 8.4: Verify build_default_registry has all commands
# =============================================================================


class TestBuildDefaultRegistry:
    def test_all_commands_registered(self) -> None:
        registry = build_default_registry()
        expected = {
            "help",
            "teams",
            "catalog",
            "create",
            "delete",
            "info",
            "events",
            "agents",
            "history",
            "files",
            "read",
            "upload",
            "stop",
            "restore",
            "switch",
            "reconnect",
            "quit",
        }
        assert set(registry.commands.keys()) == expected

    def test_status_not_registered(self) -> None:
        registry = build_default_registry()
        assert "status" not in registry.commands

    def test_restore_usage_updated(self) -> None:
        registry = build_default_registry()
        assert "[team_id]" in registry.commands["restore"].usage

    def test_reconnect_registered(self) -> None:
        registry = build_default_registry()
        assert "reconnect" in registry.commands


# =============================================================================
# Story 11.3: /reconnect command tests
# =============================================================================


class TestReconnectHandler:
    async def test_successful_reconnect(self) -> None:
        renderer, buf = _captured_renderer()
        session = _make_session(renderer=renderer)

        await _reconnect_handler("", session)

        session.conn.connect.assert_called_once()
        out = buf.getvalue()
        assert "Reconnecting" in out
        assert "Connected" in out

    async def test_reconnect_failure(self) -> None:
        renderer, buf = _captured_renderer()
        session = _make_session(renderer=renderer)
        session.conn.connect = AsyncMock(
            side_effect=WsConnectionError("server down", retryable=False)
        )

        await _reconnect_handler("", session)

        out = buf.getvalue()
        assert "Reconnecting" in out
        assert "Reconnection failed" in out
        assert "server down" in out


# =============================================================================
# Story 11.3: render_connection_status tests
# =============================================================================


class TestRenderConnectionStatus:
    def test_connected_green(self) -> None:
        renderer, buf = _captured_renderer()
        renderer.render_connection_status("connected")
        out = buf.getvalue()
        assert "Connected" in out

    def test_reconnecting_yellow(self) -> None:
        renderer, buf = _captured_renderer()
        renderer.render_connection_status("reconnecting")
        out = buf.getvalue()
        assert "Reconnecting..." in out

    def test_disconnected_red(self) -> None:
        renderer, buf = _captured_renderer()
        renderer.render_connection_status("disconnected")
        out = buf.getvalue()
        assert "Disconnected" in out

    def test_unknown_status(self) -> None:
        renderer, buf = _captured_renderer()
        renderer.render_connection_status("custom-state")
        out = buf.getvalue()
        assert "custom-state" in out
