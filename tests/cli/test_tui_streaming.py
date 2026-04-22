"""Pilot tests for WebSocket streaming, ThinkingIndicator, connection state, and state cleanup."""

from __future__ import annotations

import asyncio
import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from akgentic.core.actor_address import ActorAddress
from akgentic.core.messages.orchestrator import ProcessedMessage, ReceivedMessage

from akgentic.infra.cli.connection import ConnectionState
from akgentic.infra.cli.tui.app import ChatApp
from akgentic.infra.cli.tui.messages import ConnectionStateChanged
from akgentic.infra.cli.tui.widgets.agent_message import AgentMessage
from akgentic.infra.cli.tui.widgets.chat_input import ChatInput
from akgentic.infra.cli.tui.widgets.status_header import StatusHeader
from akgentic.infra.cli.tui.widgets.system_message import SystemMessage
from akgentic.infra.cli.tui.widgets.thinking import ThinkingIndicator
from akgentic.infra.cli.ws_client import WsConnectionError


class FakeAddress(ActorAddress):
    """Minimal concrete ActorAddress for test construction."""

    def __init__(self, name: str) -> None:
        self._name = name
        self._id = uuid.uuid4()

    @property
    def agent_id(self) -> uuid.UUID:
        return self._id

    @property
    def name(self) -> str:
        return self._name

    @property
    def role(self) -> str:
        return "test"

    @property
    def team_id(self) -> uuid.UUID:
        return self._id

    @property
    def squad_id(self) -> uuid.UUID | None:
        return None

    def send(self, recipient: ActorAddress, message: Any) -> None:
        pass

    def stop(self) -> None:
        pass

    def is_alive(self) -> bool:
        return True

    def handle_user_message(self) -> bool:
        return False

    def serialize(self) -> Any:
        return {"name": self._name}

    def __repr__(self) -> str:
        return f"FakeAddress({self._name})"


def _fake_sender(name: str) -> FakeAddress:
    """Create a FakeAddress with the given name."""
    return FakeAddress(name)


def _make_app(
    connection_manager: object | None = None,
    event_router: object | None = None,
) -> ChatApp:
    """Create a ChatApp with optional mock dependencies."""
    return ChatApp(
        team_name="test",
        team_id="123",
        team_status="running",
        connection_manager=connection_manager,  # type: ignore[arg-type]
        event_router=event_router,  # type: ignore[arg-type]
    )


# ---------------------------------------------------------------------------
# ThinkingIndicator tests (Task 12)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_thinking_indicator_renders() -> None:
    """ThinkingIndicator renders spinner text."""
    app = _make_app()
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        conv = pilot.app.query_one("#conversation")
        indicator = ThinkingIndicator()
        await conv.mount(indicator)
        rendered = str(indicator.render())
        assert "Agent is thinking" in rendered


@pytest.mark.asyncio
async def test_no_thinking_indicator_on_send() -> None:
    """ThinkingIndicator is NOT mounted on send -- indicator now appears via telemetry only."""
    app = _make_app()
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        await pilot.click(ChatInput)
        await pilot.press("h", "i")
        await pilot.press("enter")
        await pilot.pause()
        indicators = pilot.app.query(ThinkingIndicator)
        assert len(indicators) == 0


@pytest.mark.asyncio
async def test_no_thinking_indicator_on_slash_command() -> None:
    """ThinkingIndicator is NOT mounted for slash commands."""
    app = _make_app()
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        await pilot.click(ChatInput)
        await pilot.press("/", "h", "e", "l", "p")
        await pilot.press("enter")
        await pilot.pause()
        indicators = pilot.app.query(ThinkingIndicator)
        assert len(indicators) == 0


@pytest.mark.asyncio
async def test_thinking_indicator_removed_on_processed() -> None:
    """ThinkingIndicator removed when ProcessedMessage arrives after ReceivedMessage."""
    msg_id = uuid.uuid4()
    received = ReceivedMessage(message_id=msg_id, sender=_fake_sender("Assistant"))
    processed = ProcessedMessage(message_id=msg_id, sender=_fake_sender("Assistant"))

    mock_cm = MagicMock()
    mock_cm._on_state_change = None
    mock_cm.receive_event = AsyncMock(
        side_effect=[received, processed, WsConnectionError("done", retryable=False)]
    )

    mock_router = MagicMock()
    mock_router.to_widget = MagicMock(return_value=None)

    app = _make_app(connection_manager=mock_cm, event_router=mock_router)
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        await pilot.pause()
        await pilot.pause()

        # ThinkingIndicator should be gone after ProcessedMessage
        indicators = pilot.app.query(ThinkingIndicator)
        assert len(indicators) == 0


# ---------------------------------------------------------------------------
# stream_events worker tests (Task 11)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stream_events_mounts_widgets() -> None:
    """stream_events worker mounts AgentMessage widgets."""
    mock_cm = MagicMock()
    mock_cm._on_state_change = None
    mock_cm.receive_event = AsyncMock(
        side_effect=[
            {
                "event": {
                    "__model__": "SentMessage",
                    "sender": "bot",
                    "message": {"content": "hello"},
                }
            },
            WsConnectionError("done", retryable=False),
        ]
    )

    agent_msg = AgentMessage(sender="bot", content="hello", color="cyan")
    mock_router = MagicMock()
    mock_router.to_widget = MagicMock(return_value=agent_msg)

    app = _make_app(connection_manager=mock_cm, event_router=mock_router)
    async with app.run_test(size=(80, 24)) as pilot:
        # Wait for worker to start and process
        await pilot.pause()
        await pilot.pause()
        await pilot.pause()

        # Verify AgentMessage was mounted
        messages = pilot.app.query(AgentMessage)
        assert len(messages) >= 1


@pytest.mark.asyncio
async def test_stream_events_exits_on_ws_error() -> None:
    """Worker exits gracefully on WsConnectionError, mounts SystemMessage."""
    mock_cm = MagicMock()
    mock_cm._on_state_change = None
    mock_cm.receive_event = AsyncMock(
        side_effect=WsConnectionError("disconnected", retryable=False)
    )

    mock_router = MagicMock()
    mock_router.to_widget = MagicMock(return_value=None)

    app = _make_app(connection_manager=mock_cm, event_router=mock_router)
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        await pilot.pause()
        await pilot.pause()

        # SystemMessage "Connection lost" should be mounted
        sys_msgs = pilot.app.query(SystemMessage)
        found = any("Connection lost" in m._content for m in sys_msgs)
        assert found, "Expected 'Connection lost' SystemMessage after WsConnectionError"


@pytest.mark.asyncio
async def test_stream_events_skips_none_widgets() -> None:
    """Worker skips events where to_widget returns None."""
    mock_cm = MagicMock()
    mock_cm._on_state_change = None
    mock_cm.receive_event = AsyncMock(
        side_effect=[
            {"event": {"__model__": "UnknownEvent"}},
            WsConnectionError("done", retryable=False),
        ]
    )

    mock_router = MagicMock()
    mock_router.to_widget = MagicMock(return_value=None)

    app = _make_app(connection_manager=mock_cm, event_router=mock_router)
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        await pilot.pause()
        await pilot.pause()

        # No AgentMessage should be mounted
        messages = pilot.app.query(AgentMessage)
        assert len(messages) == 0


@pytest.mark.asyncio
async def test_no_streaming_without_deps() -> None:
    """stream_events returns immediately without connection_manager/event_router."""
    app = _make_app()
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        # No crash, app runs fine without deps
        assert pilot.app.query_one("#conversation") is not None


# ---------------------------------------------------------------------------
# Connection state propagation tests (Task 13)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_status_header_updates_on_disconnected() -> None:
    """StatusHeader updates connection indicator on DISCONNECTED."""
    app = _make_app()
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        header = pilot.app.query_one(StatusHeader)
        pilot.app.post_message(ConnectionStateChanged(ConnectionState.DISCONNECTED))
        await pilot.pause()
        assert header._connection_state == "disconnected"


@pytest.mark.asyncio
async def test_status_header_updates_on_reconnecting() -> None:
    """StatusHeader updates connection indicator on RECONNECTING."""
    app = _make_app()
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        header = pilot.app.query_one(StatusHeader)
        pilot.app.post_message(ConnectionStateChanged(ConnectionState.RECONNECTING))
        await pilot.pause()
        assert header._connection_state == "reconnecting"


@pytest.mark.asyncio
async def test_status_header_updates_on_connected() -> None:
    """StatusHeader restores connected state."""
    app = _make_app()
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        header = pilot.app.query_one(StatusHeader)
        # Set to disconnected first
        pilot.app.post_message(ConnectionStateChanged(ConnectionState.DISCONNECTED))
        await pilot.pause()
        assert header._connection_state == "disconnected"
        # Restore to connected
        pilot.app.post_message(ConnectionStateChanged(ConnectionState.CONNECTED))
        await pilot.pause()
        await pilot.pause()
        assert header._connection_state == "connected"


@pytest.mark.asyncio
async def test_chat_input_updates_on_disconnected() -> None:
    """ChatInput updates border_title on DISCONNECTED."""
    app = _make_app()
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        chat_input = pilot.app.query_one(ChatInput)
        pilot.app.post_message(ConnectionStateChanged(ConnectionState.DISCONNECTED))
        await pilot.pause()
        assert chat_input.border_title == "\\[disconnected] > "


@pytest.mark.asyncio
async def test_chat_input_updates_on_reconnecting() -> None:
    """ChatInput updates border_title on RECONNECTING."""
    app = _make_app()
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        chat_input = pilot.app.query_one(ChatInput)
        pilot.app.post_message(ConnectionStateChanged(ConnectionState.RECONNECTING))
        await pilot.pause()
        assert chat_input.border_title == "\\[reconnecting...] > "


@pytest.mark.asyncio
async def test_chat_input_updates_on_connected() -> None:
    """ChatInput restores default border_title on CONNECTED."""
    app = _make_app()
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        chat_input = pilot.app.query_one(ChatInput)
        # Set to disconnected first
        pilot.app.post_message(ConnectionStateChanged(ConnectionState.DISCONNECTED))
        await pilot.pause()
        assert chat_input.border_title == "\\[disconnected] > "
        # Restore to connected
        pilot.app.post_message(ConnectionStateChanged(ConnectionState.CONNECTED))
        await pilot.pause()
        await pilot.pause()
        assert chat_input.border_title == "> "


@pytest.mark.asyncio
async def test_welcome_removed_on_first_message() -> None:
    """Welcome placeholder is removed when first message is sent."""
    app = _make_app()
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        # Welcome should exist
        welcome = pilot.app.query("#welcome")
        assert len(welcome) == 1
        # Send a message
        await pilot.click(ChatInput)
        await pilot.press("h", "i")
        await pilot.press("enter")
        await pilot.pause()
        # Welcome should be removed
        welcome = pilot.app.query("#welcome")
        assert len(welcome) == 0


# ---------------------------------------------------------------------------
# Telemetry-driven ThinkingIndicator tests (Story 14.1)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_thinking_indicator_shown_on_received_message() -> None:
    """ThinkingIndicator appears when ReceivedMessage event arrives (AC #1, #2, #5)."""
    msg_id = uuid.uuid4()
    received = ReceivedMessage(message_id=msg_id, sender=_fake_sender("Assistant"))

    gate = asyncio.Event()
    call_count = 0

    async def _receive_side_effect() -> Any:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return received
        if call_count == 2:
            # Wait so the test can verify indicator is present before disconnect
            await gate.wait()
        raise WsConnectionError("done", retryable=False)

    mock_cm = MagicMock()
    mock_cm._on_state_change = None
    mock_cm.receive_event = AsyncMock(side_effect=_receive_side_effect)

    mock_router = MagicMock()
    mock_router.to_widget = MagicMock(return_value=None)

    app = _make_app(connection_manager=mock_cm, event_router=mock_router)
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        await pilot.pause()
        await pilot.pause()

        # ThinkingIndicator should be mounted after ReceivedMessage
        indicators = pilot.app.query(ThinkingIndicator)
        assert len(indicators) == 1, "ThinkingIndicator must be mounted after ReceivedMessage"
        # Pending dict should have the entry
        assert msg_id in pilot.app._pending_messages

        # Release gate to let disconnect happen
        gate.set()


@pytest.mark.asyncio
async def test_thinking_indicator_hidden_on_processed_message() -> None:
    """ThinkingIndicator appears on ReceivedMessage, disappears on ProcessedMessage."""
    msg_id = uuid.uuid4()
    received = ReceivedMessage(message_id=msg_id, sender=_fake_sender("Assistant"))
    processed = ProcessedMessage(message_id=msg_id, sender=_fake_sender("Assistant"))

    gate = asyncio.Event()
    call_count = 0

    async def _receive_side_effect() -> Any:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return received
        if call_count == 2:
            # Wait for test to verify indicator is present
            await gate.wait()
            return processed
        raise WsConnectionError("done", retryable=False)

    mock_cm = MagicMock()
    mock_cm._on_state_change = None
    mock_cm.receive_event = AsyncMock(side_effect=_receive_side_effect)

    mock_router = MagicMock()
    mock_router.to_widget = MagicMock(return_value=None)

    app = _make_app(connection_manager=mock_cm, event_router=mock_router)
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        await pilot.pause()
        await pilot.pause()

        # ThinkingIndicator should be present after ReceivedMessage
        indicators = pilot.app.query(ThinkingIndicator)
        assert len(indicators) == 1

        # Release gate to deliver ProcessedMessage
        gate.set()
        await pilot.pause()
        await pilot.pause()
        await pilot.pause()

        # ThinkingIndicator should be gone
        indicators = pilot.app.query(ThinkingIndicator)
        assert len(indicators) == 0


@pytest.mark.asyncio
async def test_thinking_indicator_stays_for_multi_agent() -> None:
    """Indicator stays while any agent is still processing (AC #7, #8)."""
    msg_id_1 = uuid.uuid4()
    msg_id_2 = uuid.uuid4()
    received_1 = ReceivedMessage(message_id=msg_id_1, sender=_fake_sender("Assistant"))
    received_2 = ReceivedMessage(message_id=msg_id_2, sender=_fake_sender("Expert"))
    processed_1 = ProcessedMessage(message_id=msg_id_1, sender=_fake_sender("Assistant"))

    gate = asyncio.Event()
    call_count = 0

    async def _receive_side_effect() -> Any:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return received_1
        if call_count == 2:
            return received_2
        if call_count == 3:
            await gate.wait()
            return processed_1
        raise WsConnectionError("done", retryable=False)

    mock_cm = MagicMock()
    mock_cm._on_state_change = None
    mock_cm.receive_event = AsyncMock(side_effect=_receive_side_effect)

    mock_router = MagicMock()
    mock_router.to_widget = MagicMock(return_value=None)

    app = _make_app(connection_manager=mock_cm, event_router=mock_router)
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        await pilot.pause()
        await pilot.pause()

        # Both agents received -- indicator should be present
        indicators = pilot.app.query(ThinkingIndicator)
        assert len(indicators) == 1
        # Both agents should be pending
        assert len(pilot.app._pending_messages) == 2

        # Process first agent -- indicator should STAY (one still pending)
        gate.set()
        await pilot.pause()
        await pilot.pause()
        await pilot.pause()

        # After first ProcessedMessage + disconnect, pending should be reduced
        # The WsConnectionError handler removes the indicator widget but
        # the dict should have had msg_id_1 removed before disconnect
        assert msg_id_1 not in pilot.app._pending_messages, (
            "Processed agent's message_id must be removed from pending"
        )


@pytest.mark.asyncio
async def test_thinking_indicator_always_below_response() -> None:
    """Indicator re-mounts below the response widget during remove-and-remount (AC #7)."""
    msg_id = uuid.uuid4()
    received = ReceivedMessage(message_id=msg_id, sender=_fake_sender("Assistant"))

    gate = asyncio.Event()
    call_count = 0

    async def _receive_side_effect() -> Any:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return received
        if call_count == 2:
            await gate.wait()
            # Return a SentMessage-like dict that the router will turn into a widget
            return MagicMock()  # Will be passed to to_widget
        raise WsConnectionError("done", retryable=False)

    mock_cm = MagicMock()
    mock_cm._on_state_change = None
    mock_cm.receive_event = AsyncMock(side_effect=_receive_side_effect)

    agent_msg = AgentMessage(sender="bot", content="hello", color="cyan")
    mock_router = MagicMock()
    mock_router.to_widget = MagicMock(return_value=agent_msg)

    app = _make_app(connection_manager=mock_cm, event_router=mock_router)
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        await pilot.pause()
        await pilot.pause()

        # Indicator should be present after ReceivedMessage
        indicators = pilot.app.query(ThinkingIndicator)
        assert len(indicators) == 1

        # Release gate to deliver the response event
        gate.set()
        await pilot.pause()
        await pilot.pause()
        await pilot.pause()

        # Indicator should be the last child in conversation (re-mounted after response)
        conv = pilot.app.query_one("#conversation")
        children = list(conv.children)
        indicator_indices = [
            i for i, c in enumerate(children) if isinstance(c, ThinkingIndicator)
        ]
        agent_msg_indices = [
            i for i, c in enumerate(children) if isinstance(c, AgentMessage)
        ]
        assert agent_msg_indices, "AgentMessage must be mounted after response event"
        # The indicator may have been removed by the disconnect handler,
        # but if present it must be below the response widget
        if indicator_indices:
            assert indicator_indices[-1] > agent_msg_indices[-1], (
                "ThinkingIndicator should be below the response widget"
            )


@pytest.mark.asyncio
async def test_no_indicator_for_fast_processing() -> None:
    """Rapid ReceivedMessage + ProcessedMessage leaves no indicator visible (AC #5, #6)."""
    msg_id = uuid.uuid4()
    received = ReceivedMessage(message_id=msg_id, sender=_fake_sender("Assistant"))
    processed = ProcessedMessage(message_id=msg_id, sender=_fake_sender("Assistant"))

    mock_cm = MagicMock()
    mock_cm._on_state_change = None
    mock_cm.receive_event = AsyncMock(
        side_effect=[received, processed, WsConnectionError("done", retryable=False)]
    )

    mock_router = MagicMock()
    mock_router.to_widget = MagicMock(return_value=None)

    app = _make_app(connection_manager=mock_cm, event_router=mock_router)
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        await pilot.pause()
        await pilot.pause()

        # No indicator should be visible after fast processing
        indicators = pilot.app.query(ThinkingIndicator)
        assert len(indicators) == 0
        # Pending dict should be empty
        assert len(pilot.app._pending_messages) == 0


# ---------------------------------------------------------------------------
# ThinkingIndicator widget unit tests (Story 14.1 - Task 7)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_thinking_indicator_format_no_agents() -> None:
    """ThinkingIndicator defaults to 'Agent is thinking...' with no agents set."""
    app = _make_app()
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        indicator = ThinkingIndicator()
        assert indicator._format_agent_names() == "Agent is thinking..."


@pytest.mark.asyncio
async def test_thinking_indicator_format_single_agent() -> None:
    """ThinkingIndicator shows '@Agent is thinking...' for one agent (AC #4)."""
    app = _make_app()
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        indicator = ThinkingIndicator()
        indicator.update_agents(["Assistant"])
        assert indicator._format_agent_names() == "@Assistant is thinking..."


@pytest.mark.asyncio
async def test_thinking_indicator_format_two_agents() -> None:
    """ThinkingIndicator shows '@A and @B are thinking...' for two agents (AC #4)."""
    app = _make_app()
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        indicator = ThinkingIndicator()
        indicator.update_agents(["Assistant", "Expert"])
        assert indicator._format_agent_names() == "@Assistant and @Expert are thinking..."


@pytest.mark.asyncio
async def test_thinking_indicator_format_three_agents() -> None:
    """ThinkingIndicator shows '@A, @B and @C are thinking...' for three agents (AC #4)."""
    app = _make_app()
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        indicator = ThinkingIndicator()
        indicator.update_agents(["Assistant", "Expert", "Manager"])
        result = indicator._format_agent_names()
        assert result == "@Assistant, @Expert and @Manager are thinking..."


@pytest.mark.asyncio
async def test_thinking_indicator_format_strips_at_prefix() -> None:
    """ThinkingIndicator strips leading @ before formatting (AC #4)."""
    app = _make_app()
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        indicator = ThinkingIndicator()
        indicator.update_agents(["@Assistant"])
        assert indicator._format_agent_names() == "@Assistant is thinking..."


# ---------------------------------------------------------------------------
# Clear thinking state on disconnect and team switch tests (Story 14.2)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_team_switch_clears_thinking_state() -> None:
    """_clear_conversation() clears _pending_messages and ThinkingIndicator (AC #1, #4)."""
    msg_id = uuid.uuid4()
    received = ReceivedMessage(message_id=msg_id, sender=_fake_sender("Assistant"))

    gate = asyncio.Event()
    call_count = 0

    async def _receive_side_effect() -> Any:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return received
        # Block until gate is released so test can verify intermediate state
        await gate.wait()
        raise WsConnectionError("done", retryable=False)

    mock_cm = MagicMock()
    mock_cm._on_state_change = None
    mock_cm.receive_event = AsyncMock(side_effect=_receive_side_effect)

    mock_router = MagicMock()
    mock_router.to_widget = MagicMock(return_value=None)

    app = _make_app(connection_manager=mock_cm, event_router=mock_router)
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        await pilot.pause()
        await pilot.pause()

        # Indicator should be mounted and pending dict non-empty
        indicators = pilot.app.query(ThinkingIndicator)
        assert len(indicators) == 1, "ThinkingIndicator must be present before clear"
        assert msg_id in pilot.app._pending_messages

        # Simulate team switch: call _clear_conversation() directly
        pilot.app._clear_conversation()
        await pilot.pause()

        # Pending messages must be cleared
        assert len(pilot.app._pending_messages) == 0, (
            "_pending_messages must be empty after _clear_conversation"
        )
        # ThinkingIndicator widget must be removed (remove_children destroys all)
        indicators = pilot.app.query(ThinkingIndicator)
        assert len(indicators) == 0, (
            "ThinkingIndicator must be removed after _clear_conversation"
        )

        # Release gate to let stream worker finish cleanly
        gate.set()


@pytest.mark.asyncio
async def test_disconnect_clears_thinking_state() -> None:
    """Disconnect clears _pending_messages and ThinkingIndicator (AC #2, #3, #4)."""
    msg_id = uuid.uuid4()
    received = ReceivedMessage(message_id=msg_id, sender=_fake_sender("Assistant"))

    gate = asyncio.Event()
    call_count = 0

    async def _receive_side_effect() -> Any:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return received
        # Wait for test to verify indicator is present, then disconnect
        await gate.wait()
        raise WsConnectionError("disconnected", retryable=False)

    mock_cm = MagicMock()
    mock_cm._on_state_change = None
    mock_cm.receive_event = AsyncMock(side_effect=_receive_side_effect)

    mock_router = MagicMock()
    mock_router.to_widget = MagicMock(return_value=None)

    app = _make_app(connection_manager=mock_cm, event_router=mock_router)
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        await pilot.pause()
        await pilot.pause()

        # Indicator should be present and pending dict non-empty
        indicators = pilot.app.query(ThinkingIndicator)
        assert len(indicators) == 1, "ThinkingIndicator must be present before disconnect"
        assert len(pilot.app._pending_messages) > 0

        # Release gate to trigger WsConnectionError (disconnect)
        gate.set()
        await pilot.pause()
        await pilot.pause()
        await pilot.pause()

        # Pending messages must be cleared by disconnect handler
        assert len(pilot.app._pending_messages) == 0, (
            "_pending_messages must be empty after disconnect"
        )
        # ThinkingIndicator widget must be removed
        indicators = pilot.app.query(ThinkingIndicator)
        assert len(indicators) == 0, (
            "ThinkingIndicator must be removed after disconnect"
        )
        # "Connection lost" SystemMessage must be mounted (confirms disconnect path)
        sys_msgs = pilot.app.query(SystemMessage)
        found = any("Connection lost" in m._content for m in sys_msgs)
        assert found, "Expected 'Connection lost' SystemMessage after disconnect"
