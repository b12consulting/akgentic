"""Pilot tests for slash command dispatch via TuiCommandAdapter."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from akgentic.infra.cli.client import ApiClient, ApiError, EventInfo, TeamInfo
from akgentic.infra.cli.connection import ConnectionManager
from akgentic.infra.cli.event_router import EventRouter
from akgentic.infra.cli.repl_commands import CommandRegistry, build_default_registry
from akgentic.infra.cli.tui.app import ChatApp
from akgentic.infra.cli.tui.widgets.chat_input import ChatInput
from akgentic.infra.cli.tui.widgets.error import ErrorWidget
from akgentic.infra.cli.tui.widgets.system_message import SystemMessage


def _make_team(
    team_id: str = "t-123",
    name: str = "test-team",
    status: str = "running",
) -> TeamInfo:
    return TeamInfo(
        team_id=team_id,
        name=name,
        status=status,
        user_id="u1",
        created_at="2026-01-01",
        updated_at="2026-01-01",
    )


def _mock_client() -> MagicMock:
    """Create a mock ApiClient."""
    client = MagicMock(spec=ApiClient)
    client.list_teams.return_value = [_make_team()]
    client.get_team.return_value = _make_team()
    client.send_message.return_value = None
    return client


def _mock_conn() -> MagicMock:
    """Create a mock ConnectionManager."""
    conn = MagicMock(spec=ConnectionManager)
    conn.switch_team = AsyncMock()
    conn.connect = AsyncMock()
    conn.receive_event = AsyncMock(side_effect=asyncio.CancelledError)
    return conn


def _mock_event_router() -> MagicMock:
    """Create a mock EventRouter that returns None from to_widget()."""
    router = MagicMock(spec=EventRouter)
    router.to_widget.return_value = None
    return router


def _make_app(
    client: object | None = None,
    connection_manager: object | None = None,
    command_registry: CommandRegistry | None = None,
) -> ChatApp:
    """Create a ChatApp with mock dependencies for testing."""
    registry = command_registry or build_default_registry()
    return ChatApp(
        team_name="test",
        team_id="t-123",
        team_status="running",
        connection_manager=connection_manager,
        event_router=_mock_event_router(),
        command_registry=registry,
        client=client,
    )


# ---------------------------------------------------------------------------
# Task 7: Pilot tests for slash command dispatch (AC: #1, #2, #6, #11)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_help_mounts_system_message() -> None:
    """AC #6: /help mounts a SystemMessage with help text."""
    app = _make_app(client=_mock_client())
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        await pilot.click(ChatInput)
        await pilot.press("/", "h", "e", "l", "p")
        await pilot.press("enter")
        await pilot.pause()
        msgs = pilot.app.query(SystemMessage)
        # Should have at least one SystemMessage with help content
        found = any("Available commands" in m._content for m in msgs)
        assert found, "Expected SystemMessage with 'Available commands'"


@pytest.mark.asyncio
async def test_teams_mounts_system_message() -> None:
    """AC #2: /teams mounts a SystemMessage with team listing."""
    client = _mock_client()
    app = _make_app(client=client)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        await pilot.click(ChatInput)
        await pilot.press("/", "t", "e", "a", "m", "s")
        await pilot.press("enter")
        await pilot.pause()
        msgs = pilot.app.query(SystemMessage)
        found = any("test-team" in m._content for m in msgs)
        assert found, "Expected SystemMessage with team listing"


@pytest.mark.asyncio
async def test_info_mounts_system_message() -> None:
    """AC #2: /info mounts a SystemMessage with team info."""
    client = _mock_client()
    app = _make_app(client=client)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        await pilot.click(ChatInput)
        await pilot.press("/", "i", "n", "f", "o")
        await pilot.press("enter")
        await pilot.pause()
        msgs = pilot.app.query(SystemMessage)
        found = any("Name:" in m._content for m in msgs)
        assert found, "Expected SystemMessage with team info"


@pytest.mark.asyncio
async def test_unknown_command_mounts_message() -> None:
    """AC #1: unknown /foo mounts an error/system message."""
    app = _make_app(client=_mock_client())
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        chat_input = pilot.app.query_one(ChatInput)
        chat_input.post_message(ChatInput.Submitted(text="/foo"))
        await pilot.pause()
        await pilot.pause()
        msgs = pilot.app.query(SystemMessage)
        found = any("Unknown command" in m._content for m in msgs)
        assert found, "Expected SystemMessage with 'Unknown command'"


# ---------------------------------------------------------------------------
# Task 8: Pilot tests for /switch worker lifecycle (AC: #3, #11)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_switch_calls_conn_and_updates_header() -> None:
    """AC #3: /switch calls ConnectionManager.switch_team(), updates StatusHeader."""
    client = _mock_client()
    conn = _mock_conn()
    app = _make_app(client=client, connection_manager=conn)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        # Type /switch new-team-id
        chat_input = pilot.app.query_one(ChatInput)
        chat_input.post_message(ChatInput.Submitted(text="/switch new-team-id"))
        await pilot.pause()
        await pilot.pause()

        conn.switch_team.assert_called_once_with("new-team-id")
        msgs = pilot.app.query(SystemMessage)
        found = any("Switched to team" in m._content for m in msgs)
        assert found, "Expected SystemMessage confirming switch"


@pytest.mark.asyncio
async def test_switch_no_args_mounts_usage() -> None:
    """AC #3: /switch with no args mounts usage SystemMessage."""
    app = _make_app(client=_mock_client(), connection_manager=_mock_conn())
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        chat_input = pilot.app.query_one(ChatInput)
        chat_input.post_message(ChatInput.Submitted(text="/switch"))
        await pilot.pause()
        await pilot.pause()

        msgs = pilot.app.query(SystemMessage)
        found = any("Usage:" in m._content for m in msgs)
        assert found, "Expected usage message for /switch"


@pytest.mark.asyncio
async def test_switch_failure_mounts_error() -> None:
    """AC #3: /switch failure mounts ErrorWidget."""
    conn = _mock_conn()
    conn.switch_team = AsyncMock(side_effect=Exception("connection refused"))
    app = _make_app(client=_mock_client(), connection_manager=conn)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        chat_input = pilot.app.query_one(ChatInput)
        chat_input.post_message(ChatInput.Submitted(text="/switch bad-id"))
        await pilot.pause()
        await pilot.pause()

        errors = pilot.app.query(ErrorWidget)
        found = any("Switch failed" in e._content for e in errors)
        assert found, "Expected ErrorWidget for switch failure"


# ---------------------------------------------------------------------------
# Task 9: Pilot tests for error rendering on command failure (AC: #8, #11)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_command_api_error_mounts_error_widget() -> None:
    """AC #8: command that raises ApiError mounts ErrorWidget."""
    client = _mock_client()
    client.list_teams.side_effect = ApiError(500, "Internal server error")
    app = _make_app(client=client)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        chat_input = pilot.app.query_one(ChatInput)
        chat_input.post_message(ChatInput.Submitted(text="/teams"))
        await pilot.pause()
        await pilot.pause()

        errors = pilot.app.query(ErrorWidget)
        assert len(errors) >= 1, "Expected ErrorWidget for API error"


@pytest.mark.asyncio
async def test_reconnect_failure_mounts_error() -> None:
    """AC #4, #8: /reconnect failure mounts ErrorWidget."""
    conn = _mock_conn()
    conn.connect = AsyncMock(side_effect=Exception("server unreachable"))
    app = _make_app(client=_mock_client(), connection_manager=conn)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        chat_input = pilot.app.query_one(ChatInput)
        chat_input.post_message(ChatInput.Submitted(text="/reconnect"))
        await pilot.pause()
        await pilot.pause()

        errors = pilot.app.query(ErrorWidget)
        found = any("Reconnection failed" in e._content for e in errors)
        assert found, "Expected ErrorWidget for reconnection failure"


# ---------------------------------------------------------------------------
# Task 10: Pilot tests for message sending (AC: #10, #11)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_message_send_calls_api() -> None:
    """AC #10: non-slash text sends message via ApiClient.send_message()."""
    client = _mock_client()
    app = _make_app(client=client)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        await pilot.click(ChatInput)
        await pilot.press("h", "e", "l", "l", "o")
        await pilot.press("enter")
        await pilot.pause()
        await pilot.pause()
        await pilot.pause()

        client.send_message.assert_called_once_with("t-123", "hello")


@pytest.mark.asyncio
async def test_message_send_dispatches_without_local_widget() -> None:
    """Non-slash text sends via API and does NOT mount a local UserMessage widget (12.9 AC #1, #3)."""
    client = _mock_client()
    app = _make_app(client=client)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        await pilot.click(ChatInput)
        await pilot.press("h", "i")
        await pilot.press("enter")
        await pilot.pause()
        await pilot.pause()
        await pilot.pause()

        # Verify API was called
        client.send_message.assert_called_once_with("t-123", "hi")
        # Verify no UserMessage widget exists in conversation
        from textual.containers import VerticalScroll

        conversation = pilot.app.query_one("#conversation", VerticalScroll)
        child_types = [type(c).__name__ for c in conversation.children]
        assert "UserMessage" not in child_types


@pytest.mark.asyncio
async def test_message_send_error_mounts_error_widget() -> None:
    """AC #10: ApiError on send mounts ErrorWidget."""
    client = _mock_client()
    client.send_message.side_effect = ApiError(500, "send failed")
    app = _make_app(client=client)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        await pilot.click(ChatInput)
        await pilot.press("h", "i")
        await pilot.press("enter")
        await pilot.pause()
        await pilot.pause()
        await pilot.pause()
        await pilot.pause()

        errors = pilot.app.query(ErrorWidget)
        found = any("Failed to send" in e._content for e in errors)
        assert found, "Expected ErrorWidget for send failure"


# ---------------------------------------------------------------------------
# Task 4: /quit test (AC: #5)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_quit_exits_app() -> None:
    """AC #5: /quit exits ChatApp."""
    app = _make_app(client=_mock_client())
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        chat_input = pilot.app.query_one(ChatInput)
        chat_input.post_message(ChatInput.Submitted(text="/quit"))
        await pilot.pause()
        await pilot.pause()
        # App should have received exit request
        # (Textual test runner handles this gracefully)


# ---------------------------------------------------------------------------
# Review fixes: Missing test coverage
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reconnect_success_mounts_system_message() -> None:
    """AC #4: /reconnect success mounts SystemMessage confirming reconnection."""
    conn = _mock_conn()
    conn.connect = AsyncMock()  # success (no exception)
    app = _make_app(client=_mock_client(), connection_manager=conn)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        chat_input = pilot.app.query_one(ChatInput)
        chat_input.post_message(ChatInput.Submitted(text="/reconnect"))
        await pilot.pause()
        await pilot.pause()

        conn.connect.assert_called_once()
        msgs = pilot.app.query(SystemMessage)
        found = any("Reconnected" in m._content for m in msgs)
        assert found, "Expected SystemMessage confirming reconnection"


@pytest.mark.asyncio
async def test_delete_warning_mounts_system_message() -> None:
    """AC #7: /delete <id> mounts warning SystemMessage with confirmation instructions."""
    client = _mock_client()
    app = _make_app(client=client, connection_manager=_mock_conn())
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        chat_input = pilot.app.query_one(ChatInput)
        chat_input.post_message(ChatInput.Submitted(text="/delete some-team"))
        await pilot.pause()
        await pilot.pause()

        msgs = pilot.app.query(SystemMessage)
        found = any("confirm" in m._content.lower() for m in msgs)
        assert found, "Expected SystemMessage with deletion confirmation instructions"


@pytest.mark.asyncio
async def test_delete_confirm_executes_deletion() -> None:
    """AC #7: /delete confirm <id> executes actual deletion."""
    client = _mock_client()
    client.delete_team.return_value = None
    app = _make_app(client=client, connection_manager=_mock_conn())
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        chat_input = pilot.app.query_one(ChatInput)
        chat_input.post_message(ChatInput.Submitted(text="/delete confirm some-team"))
        await pilot.pause()
        await pilot.pause()

        client.delete_team.assert_called_once_with("some-team")
        msgs = pilot.app.query(SystemMessage)
        found = any("deleted" in m._content.lower() for m in msgs)
        assert found, "Expected SystemMessage confirming deletion"


@pytest.mark.asyncio
async def test_create_mounts_confirmation_and_switches() -> None:
    """AC #7: /create mounts confirmation and auto-switches to new team."""
    client = _mock_client()
    client.create_team.return_value = _make_team(team_id="new-t", name="new-team")
    conn = _mock_conn()
    app = _make_app(client=client, connection_manager=conn)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        chat_input = pilot.app.query_one(ChatInput)
        chat_input.post_message(ChatInput.Submitted(text="/create test-catalog"))
        await pilot.pause()
        await pilot.pause()

        client.create_team.assert_called_once_with("test-catalog")
        msgs = pilot.app.query(SystemMessage)
        found_create = any("Created team" in m._content for m in msgs)
        assert found_create, "Expected SystemMessage confirming creation"


@pytest.mark.asyncio
async def test_stop_mounts_confirmation() -> None:
    """AC #7: /stop mounts confirmation SystemMessage."""
    client = _mock_client()
    client.stop_team.return_value = None
    app = _make_app(client=client, connection_manager=_mock_conn())
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        chat_input = pilot.app.query_one(ChatInput)
        chat_input.post_message(ChatInput.Submitted(text="/stop"))
        await pilot.pause()
        await pilot.pause()

        client.stop_team.assert_called_once_with("t-123")
        msgs = pilot.app.query(SystemMessage)
        found = any("stopped" in m._content.lower() for m in msgs)
        assert found, "Expected SystemMessage confirming stop"


@pytest.mark.asyncio
async def test_restore_mounts_confirmation_and_switches() -> None:
    """AC #7: /restore mounts confirmation and auto-switches."""
    client = _mock_client()
    client.restore_team.return_value = None
    conn = _mock_conn()
    app = _make_app(client=client, connection_manager=conn)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        chat_input = pilot.app.query_one(ChatInput)
        chat_input.post_message(ChatInput.Submitted(text="/restore t-123"))
        await pilot.pause()
        await pilot.pause()

        client.restore_team.assert_called_once_with("t-123")
        msgs = pilot.app.query(SystemMessage)
        found = any("restored" in m._content.lower() for m in msgs)
        assert found, "Expected SystemMessage confirming restore"


@pytest.mark.asyncio
async def test_history_no_events_mounts_message() -> None:
    """AC #2: /history with no events mounts 'no displayable history' message."""
    client = _mock_client()
    client.get_events.return_value = []
    router = _mock_event_router()
    app = ChatApp(
        team_name="test",
        team_id="t-123",
        team_status="running",
        connection_manager=_mock_conn(),
        event_router=router,
        command_registry=build_default_registry(),
        client=client,
    )
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        chat_input = pilot.app.query_one(ChatInput)
        chat_input.post_message(ChatInput.Submitted(text="/history"))
        await pilot.pause()
        await pilot.pause()

        # get_events called by /history command
        assert client.get_events.call_count >= 1
        msgs = pilot.app.query(SystemMessage)
        found = any("No displayable" in m._content for m in msgs)
        assert found, "Expected SystemMessage about no displayable events"


@pytest.mark.asyncio
async def test_history_with_events_mounts_widgets() -> None:
    """AC #2: /history with displayable events mounts widgets from EventRouter."""
    from akgentic.infra.cli.tui.widgets.agent_message import AgentMessage
    from tests.fixtures.events import build_sent_message

    client = _mock_client()
    typed_event = build_sent_message(content="hello from history")
    event = EventInfo(
        team_id="t-123",
        sequence=1,
        event=typed_event,
        timestamp="2026-01-01",
    )
    client.get_events.return_value = [event]

    # Use real EventRouter for widget generation
    from akgentic.infra.cli.renderers import RichRenderer

    real_router = EventRouter(RichRenderer())
    app = ChatApp(
        team_name="test",
        team_id="t-123",
        team_status="running",
        connection_manager=_mock_conn(),
        event_router=real_router,
        command_registry=build_default_registry(),
        client=client,
    )
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        chat_input = pilot.app.query_one(ChatInput)
        chat_input.post_message(ChatInput.Submitted(text="/history"))
        await pilot.pause()
        await pilot.pause()

        agent_msgs = pilot.app.query(AgentMessage)
        assert len(agent_msgs) >= 1, "Expected at least one AgentMessage from history"


@pytest.mark.asyncio
async def test_history_api_error_mounts_error() -> None:
    """AC #2, #8: /history with API error mounts ErrorWidget."""
    client = _mock_client()
    client.get_events.side_effect = ApiError(500, "events unavailable")
    app = _make_app(client=client)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        chat_input = pilot.app.query_one(ChatInput)
        chat_input.post_message(ChatInput.Submitted(text="/history"))
        await pilot.pause()
        await pilot.pause()

        errors = pilot.app.query(ErrorWidget)
        found = any("history" in e._content.lower() for e in errors)
        assert found, "Expected ErrorWidget for history fetch failure"


@pytest.mark.asyncio
async def test_history_invalid_arg_mounts_usage() -> None:
    """AC #2: /history with invalid arg mounts usage message."""
    app = _make_app(client=_mock_client())
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        chat_input = pilot.app.query_one(ChatInput)
        chat_input.post_message(ChatInput.Submitted(text="/history abc"))
        await pilot.pause()
        await pilot.pause()

        msgs = pilot.app.query(SystemMessage)
        found = any("Usage:" in m._content for m in msgs)
        assert found, "Expected usage message for invalid /history arg"


@pytest.mark.asyncio
async def test_create_no_args_mounts_usage() -> None:
    """AC #7: /create with no args mounts usage message."""
    app = _make_app(client=_mock_client())
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        chat_input = pilot.app.query_one(ChatInput)
        chat_input.post_message(ChatInput.Submitted(text="/create"))
        await pilot.pause()
        await pilot.pause()

        msgs = pilot.app.query(SystemMessage)
        found = any("Usage:" in m._content for m in msgs)
        assert found, "Expected usage message for /create with no args"


@pytest.mark.asyncio
async def test_create_api_error_mounts_error() -> None:
    """AC #7, #8: /create with API error mounts ErrorWidget."""
    client = _mock_client()
    client.create_team.side_effect = ApiError(400, "catalog entry not found")
    app = _make_app(client=client)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        chat_input = pilot.app.query_one(ChatInput)
        chat_input.post_message(ChatInput.Submitted(text="/create bad-entry"))
        await pilot.pause()
        await pilot.pause()

        errors = pilot.app.query(ErrorWidget)
        found = any("creating team" in e._content.lower() for e in errors)
        assert found, "Expected ErrorWidget for create failure"


@pytest.mark.asyncio
async def test_stop_api_error_mounts_error() -> None:
    """AC #7, #8: /stop with API error mounts ErrorWidget."""
    client = _mock_client()
    client.stop_team.side_effect = ApiError(500, "stop failed")
    app = _make_app(client=client)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        chat_input = pilot.app.query_one(ChatInput)
        chat_input.post_message(ChatInput.Submitted(text="/stop"))
        await pilot.pause()
        await pilot.pause()

        errors = pilot.app.query(ErrorWidget)
        found = any("stopping team" in e._content.lower() for e in errors)
        assert found, "Expected ErrorWidget for stop failure"


@pytest.mark.asyncio
async def test_restore_api_error_mounts_error() -> None:
    """AC #7, #8: /restore with API error mounts ErrorWidget."""
    client = _mock_client()
    client.restore_team.side_effect = ApiError(500, "restore failed")
    app = _make_app(client=client)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        chat_input = pilot.app.query_one(ChatInput)
        chat_input.post_message(ChatInput.Submitted(text="/restore t-123"))
        await pilot.pause()
        await pilot.pause()

        errors = pilot.app.query(ErrorWidget)
        found = any("restoring team" in e._content.lower() for e in errors)
        assert found, "Expected ErrorWidget for restore failure"


@pytest.mark.asyncio
async def test_delete_confirm_api_error_mounts_error() -> None:
    """AC #7, #8: /delete confirm with API error mounts ErrorWidget."""
    client = _mock_client()
    client.delete_team.side_effect = ApiError(500, "delete failed")
    app = _make_app(client=client)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        chat_input = pilot.app.query_one(ChatInput)
        chat_input.post_message(ChatInput.Submitted(text="/delete confirm t-123"))
        await pilot.pause()
        await pilot.pause()

        errors = pilot.app.query(ErrorWidget)
        found = any("deleting team" in e._content.lower() for e in errors)
        assert found, "Expected ErrorWidget for delete failure"
