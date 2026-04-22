"""Tests for main.py top-level error boundary."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
import typer

from akgentic.infra.cli.client import ApiError


@pytest.fixture()
def _mock_state() -> MagicMock:
    """Patch _state with a mock client and server config."""
    with patch("akgentic.infra.cli.main._state") as state:
        state.client = MagicMock()
        state.server = "http://localhost:8000"
        state.api_key = None
        state.fmt = MagicMock()
        yield state


class TestChatErrorBoundary:
    """Test the top-level error boundary in chat()."""

    def test_api_error_renders_error_and_exits(
        self, _mock_state: MagicMock
    ) -> None:
        """ApiError during get_team() renders error and exits cleanly."""
        error = ApiError(500, "internal server error")
        with (
            patch("akgentic.infra.cli.main.RichRenderer") as mock_renderer_cls,
            patch("akgentic.infra.cli.main.ChatApp") as mock_app_cls,
        ):
            renderer_instance = mock_renderer_cls.return_value
            _mock_state.client.get_team.side_effect = error
            mock_app_cls.return_value.run.return_value = None

            from akgentic.infra.cli.main import chat

            with pytest.raises(typer.Exit):
                chat(team_id="t1")

            renderer_instance.render_error.assert_called_once()
            call_args = renderer_instance.render_error.call_args[0][0]
            assert "Server error" in call_args
            _mock_state.client.close.assert_called_once()

    def test_generic_exception_renders_error_and_exits(
        self, _mock_state: MagicMock
    ) -> None:
        """Generic Exception during app.run() renders error and exits cleanly."""
        error = RuntimeError("something broke")
        with (
            patch("akgentic.infra.cli.main.RichRenderer") as mock_renderer_cls,
            patch("akgentic.infra.cli.main.ChatApp") as mock_app_cls,
        ):
            renderer_instance = mock_renderer_cls.return_value
            _mock_state.client.get_team.return_value = MagicMock(
                name="test", status="running"
            )
            mock_app_cls.return_value.run.side_effect = error

            from akgentic.infra.cli.main import chat

            with pytest.raises(typer.Exit):
                chat(team_id="t1")

            renderer_instance.render_error.assert_called_once()
            call_args = renderer_instance.render_error.call_args[0][0]
            assert "Unexpected error" in call_args
            _mock_state.client.close.assert_called_once()

    def test_keyboard_interrupt_exits_cleanly(
        self, _mock_state: MagicMock
    ) -> None:
        """KeyboardInterrupt during app.run() exits without error message."""
        with (
            patch("akgentic.infra.cli.main.RichRenderer") as mock_renderer_cls,
            patch("akgentic.infra.cli.main.ChatApp") as mock_app_cls,
        ):
            renderer_instance = mock_renderer_cls.return_value
            _mock_state.client.get_team.return_value = MagicMock(
                name="test", status="running"
            )
            mock_app_cls.return_value.run.side_effect = KeyboardInterrupt

            from akgentic.infra.cli.main import chat

            chat(team_id="t1")

            renderer_instance.render_error.assert_not_called()
            _mock_state.client.close.assert_called_once()

    def test_client_close_called_on_success(
        self, _mock_state: MagicMock
    ) -> None:
        """client.close() is called even on successful exit."""
        with (
            patch("akgentic.infra.cli.main.RichRenderer"),
            patch("akgentic.infra.cli.main.ChatApp") as mock_app_cls,
        ):
            _mock_state.client.get_team.return_value = MagicMock(
                name="test", status="running"
            )
            mock_app_cls.return_value.run.return_value = None

            from akgentic.infra.cli.main import chat

            chat(team_id="t1")
            _mock_state.client.close.assert_called_once()
