"""Tests for ak-infra CLI commands via typer.testing.CliRunner."""

from __future__ import annotations

import json
import re
from typing import Any
from unittest.mock import MagicMock, patch

import typer
import yaml
from typer.testing import CliRunner

from akgentic.infra.cli.main import app

from .conftest import mock_client as _shared_mock_client

runner = CliRunner()

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def _strip_ansi(text: str) -> str:
    """Remove ANSI escape codes from text."""
    return _ANSI_RE.sub("", text)


def _mock_client(**overrides: Any) -> MagicMock:
    """Build a mock ApiClient with sensible defaults."""
    return _shared_mock_client(**overrides)


def _invoke(args: list[str], mock: MagicMock | None = None) -> Any:
    """Invoke CLI with a mocked client, return the Result."""
    if mock is None:
        mock = _mock_client()
    with patch("akgentic.infra.cli.main.ApiClient", return_value=mock):
        return runner.invoke(app, args)


# -- help --


class TestHelp:
    def test_help_shows_groups(self) -> None:
        result = _invoke(["--help"])
        assert result.exit_code == 0
        output = _strip_ansi(result.output)
        assert "team" in output
        assert "workspace" in output
        assert "chat" in output
        assert "message" in output
        assert "reply" in output

    def test_help_shows_global_options(self) -> None:
        result = _invoke(["--help"])
        output = _strip_ansi(result.output)
        assert "--server" in output
        assert "--api-key" in output
        assert "--format" in output


# -- team commands --


class TestTeamList:
    def test_table_output(self) -> None:
        result = _invoke(["team", "list"])
        assert result.exit_code == 0
        assert "Team 1" in result.output
        assert "running" in result.output

    def test_json_output(self) -> None:
        result = _invoke(["--format", "json", "team", "list"])
        assert result.exit_code == 0
        parsed = json.loads(result.output)
        assert isinstance(parsed, list)
        assert parsed[0]["team_id"] == "t1"

    def test_yaml_output(self) -> None:
        result = _invoke(["--format", "yaml", "team", "list"])
        assert result.exit_code == 0
        parsed = yaml.safe_load(result.output)
        assert isinstance(parsed, list)


class TestTeamGet:
    def test_shows_detail(self) -> None:
        result = _invoke(["team", "get", "t1"])
        assert result.exit_code == 0
        assert "Team 1" in result.output

    def test_json_output(self) -> None:
        result = _invoke(["--format", "json", "team", "get", "t1"])
        parsed = json.loads(result.output)
        assert parsed["team_id"] == "t1"


class TestTeamCreate:
    def test_creates_team(self) -> None:
        mock = _mock_client()
        result = _invoke(["team", "create", "my-catalog-entry"], mock)
        assert result.exit_code == 0
        mock.create_team.assert_called_once_with("my-catalog-entry")
        assert "New Team" in result.output


class TestTeamDelete:
    def test_deletes_team(self) -> None:
        mock = _mock_client()
        result = _invoke(["team", "delete", "t1"], mock)
        assert result.exit_code == 0
        mock.delete_team.assert_called_once_with("t1")
        assert "deleted" in result.output.lower()

    def test_json_output(self) -> None:
        mock = _mock_client()
        result = _invoke(["--format", "json", "team", "delete", "t1"], mock)
        assert result.exit_code == 0
        parsed = json.loads(result.output)
        assert parsed["status"] == "deleted"


class TestTeamRestore:
    def test_restores_team(self) -> None:
        mock = _mock_client()
        result = _invoke(["team", "restore", "t1"], mock)
        assert result.exit_code == 0
        mock.restore_team.assert_called_once_with("t1")
        assert "Team 1" in result.output


class TestTeamEvents:
    def test_shows_events(self) -> None:
        result = _invoke(["team", "events", "t1"])
        assert result.exit_code == 0
        assert "started" in result.output


# -- message / reply --


class TestMessage:
    def test_sends_message(self) -> None:
        mock = _mock_client()
        result = _invoke(["message", "t1", "hello world"], mock)
        assert result.exit_code == 0
        mock.send_message.assert_called_once_with("t1", "hello world")
        assert "sent" in result.output.lower()

    def test_json_output(self) -> None:
        mock = _mock_client()
        result = _invoke(["--format", "json", "message", "t1", "hello"], mock)
        assert result.exit_code == 0
        parsed = json.loads(result.output)
        assert parsed["status"] == "sent"


class TestReply:
    def test_sends_reply(self) -> None:
        mock = _mock_client()
        result = _invoke(["reply", "t1", "yes", "--message-id", "msg-42"], mock)
        assert result.exit_code == 0
        mock.human_input.assert_called_once_with("t1", "yes", "msg-42")
        assert "sent" in result.output.lower()


# -- chat --


class TestChat:
    def test_chat_no_args_launches_team_select(self) -> None:
        mock = _mock_client()
        with patch("akgentic.infra.cli.main.ChatApp") as mock_app_cls:
            mock_app_cls.return_value.run.return_value = None
            result = _invoke(["chat"], mock)
        assert result.exit_code == 0
        mock_app_cls.assert_called_once()
        call_kwargs = mock_app_cls.call_args.kwargs
        assert call_kwargs["client"] is mock
        assert "connection_manager" in call_kwargs
        assert "event_router" in call_kwargs
        assert "command_registry" in call_kwargs
        mock_app_cls.return_value.run.assert_called_once()

    def test_chat_launches_tui(self) -> None:
        mock = _mock_client()
        with patch("akgentic.infra.cli.main.ChatApp") as mock_app_cls:
            mock_app_cls.return_value.run.return_value = None
            result = _invoke(["chat", "t1"], mock)
        assert result.exit_code == 0
        mock.get_team.assert_called_once_with("t1")
        mock_app_cls.assert_called_once()
        mock_app_cls.return_value.run.assert_called_once()

    def test_chat_create_flag_no_team_id(self) -> None:
        mock = _mock_client()
        with patch("akgentic.infra.cli.main.ChatApp") as mock_app_cls:
            mock_app_cls.return_value.run.return_value = None
            result = _invoke(["chat", "--create", "my-catalog"], mock)
        assert result.exit_code == 0
        mock.create_team.assert_called_once_with("my-catalog")


# -- workspace --


class TestWorkspaceTree:
    def test_shows_tree(self) -> None:
        result = _invoke(["workspace", "tree", "t1"])
        assert result.exit_code == 0
        assert "docs" in result.output
        assert "readme.md" in result.output

    def test_json_output(self) -> None:
        result = _invoke(["--format", "json", "workspace", "tree", "t1"])
        parsed = json.loads(result.output)
        assert "entries" in parsed

    def test_yaml_output(self) -> None:
        result = _invoke(["--format", "yaml", "workspace", "tree", "t1"])
        parsed = yaml.safe_load(result.output)
        assert "entries" in parsed


class TestWorkspaceRead:
    def test_shows_content(self) -> None:
        result = _invoke(["workspace", "read", "t1", "readme.md"])
        assert result.exit_code == 0
        assert "file content" in result.output

    def test_binary_file(self) -> None:
        mock = _mock_client()
        mock.workspace_read.return_value = bytes(range(256))
        result = _invoke(["workspace", "read", "t1", "binary.bin"], mock)
        assert "binary" in result.output.lower() or "bytes" in result.output.lower()


class TestWorkspaceUpload:
    def test_uploads_file(self, tmp_path: Any) -> None:
        f = tmp_path / "test.txt"
        f.write_text("hello")
        mock = _mock_client()
        result = _invoke(["workspace", "upload", "t1", str(f)], mock)
        assert result.exit_code == 0
        assert "readme.md" in result.output or "Uploaded" in result.output

    def test_missing_file(self) -> None:
        result = _invoke(["workspace", "upload", "t1", "/nonexistent/file.txt"])
        assert result.exit_code != 0


# -- error cases --


class TestErrorCases:
    def test_404_response(self) -> None:
        mock = _mock_client()
        mock.get_team.side_effect = typer.Exit(code=1)
        result = _invoke(["team", "get", "missing"], mock)
        assert result.exit_code != 0
