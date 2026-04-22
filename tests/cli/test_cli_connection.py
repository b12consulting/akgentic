"""Tests for ConnectionManager with auto-reconnect."""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import websockets.exceptions

from akgentic.infra.cli.connection import ConnectionManager, ConnectionState
from akgentic.infra.cli.ws_client import WsConnectionError

_WS_CLIENT_PATH = "akgentic.infra.cli.connection.WsClient"


class TestConnectionStateEnum:
    """AC #1: ConnectionState enum values."""

    def test_all_states_exist(self) -> None:
        assert ConnectionState.CONNECTING.value == "connecting"
        assert ConnectionState.CONNECTED.value == "connected"
        assert ConnectionState.RECONNECTING.value == "reconnecting"
        assert ConnectionState.DISCONNECTED.value == "disconnected"

    def test_four_members(self) -> None:
        assert len(ConnectionState) == 4


class TestConnectionManagerInit:
    """AC #2: ConnectionManager construction."""

    def test_initial_state_is_disconnected(self) -> None:
        cm = ConnectionManager(server_url="http://localhost:8000", team_id="t1")
        assert cm.state == ConnectionState.DISCONNECTED
        assert cm.team_id == "t1"

    def test_default_max_retries(self) -> None:
        cm = ConnectionManager(server_url="http://localhost:8000", team_id="t1")
        assert cm._max_retries == 10


class TestConnect:
    """AC #2: connect() method."""

    async def test_connect_success(self) -> None:
        """AC #2, #4: Successful connect transitions DISCONNECTED -> CONNECTING -> CONNECTED."""
        states: list[ConnectionState] = []

        def on_change(s: ConnectionState) -> None:
            states.append(s)

        mock_ws = AsyncMock()
        mock_ws.connect = AsyncMock(return_value=mock_ws)

        with patch(_WS_CLIENT_PATH, return_value=mock_ws):
            cm = ConnectionManager(
                server_url="http://localhost:8000",
                team_id="t1",
                on_state_change=on_change,
            )
            await cm.connect()

        assert cm.state == ConnectionState.CONNECTED
        assert states == [ConnectionState.CONNECTING, ConnectionState.CONNECTED]
        assert cm._last_event_time > 0

    async def test_connect_failure(self) -> None:
        """AC #4: Failed connect transitions DISCONNECTED -> CONNECTING -> DISCONNECTED."""
        states: list[ConnectionState] = []

        def on_change(s: ConnectionState) -> None:
            states.append(s)

        mock_ws = AsyncMock()
        mock_ws.connect = AsyncMock(side_effect=WsConnectionError("refused"))

        with patch(_WS_CLIENT_PATH, return_value=mock_ws):
            cm = ConnectionManager(
                server_url="http://localhost:8000",
                team_id="t1",
                on_state_change=on_change,
            )
            with pytest.raises(WsConnectionError):
                await cm.connect()

        assert cm.state == ConnectionState.DISCONNECTED
        assert states == [ConnectionState.CONNECTING, ConnectionState.DISCONNECTED]


class TestReconnect:
    """AC #3: Exponential backoff reconnection."""

    async def test_exponential_backoff_sequence(self) -> None:
        """AC #3: Verify delay sequence 1, 2, 4, 8, 16, 30, 30, ..."""
        mock_ws = AsyncMock()
        # Fail 7 times, then succeed
        effects: list[Exception | AsyncMock] = [
            WsConnectionError("fail") for _ in range(7)
        ]
        effects.append(AsyncMock())  # success on 8th

        call_idx = 0

        async def connect_side_effect() -> AsyncMock:
            nonlocal call_idx
            effect = effects[call_idx]
            call_idx += 1
            if isinstance(effect, Exception):
                raise effect
            return effect

        mock_ws.connect = AsyncMock(side_effect=connect_side_effect)

        sleep_calls: list[float] = []

        async def mock_sleep(delay: float) -> None:
            sleep_calls.append(delay)

        with (
            patch(_WS_CLIENT_PATH, return_value=mock_ws),
            patch("akgentic.infra.cli.connection.asyncio.sleep", side_effect=mock_sleep),
        ):
            cm = ConnectionManager(
                server_url="http://localhost:8000",
                team_id="t1",
                max_retries=10,
            )
            cm._ws_client = mock_ws  # pretend we were connected
            await cm._reconnect()

        # 7 failures before success, so 7 sleeps
        assert sleep_calls == [1, 2, 4, 8, 16, 30, 30]
        assert cm.state == ConnectionState.CONNECTED

    async def test_reconnect_exhaustion(self) -> None:
        """AC #3, #5: After max_retries, raise WsConnectionError(retryable=False)."""
        mock_ws = AsyncMock()
        mock_ws.connect = AsyncMock(side_effect=WsConnectionError("fail"))

        sleep_calls: list[float] = []

        async def mock_sleep(delay: float) -> None:
            sleep_calls.append(delay)

        with (
            patch(_WS_CLIENT_PATH, return_value=mock_ws),
            patch("akgentic.infra.cli.connection.asyncio.sleep", side_effect=mock_sleep),
        ):
            cm = ConnectionManager(
                server_url="http://localhost:8000",
                team_id="t1",
                max_retries=3,
            )
            with pytest.raises(WsConnectionError, match="Reconnection failed after 3 attempts"):
                await cm._reconnect()

        assert cm.state == ConnectionState.DISCONNECTED
        assert len(sleep_calls) == 3


class TestReceiveEvent:
    """AC #5: receive_event()."""

    async def test_receive_success(self) -> None:
        """AC #5: Successful event updates _last_event_time."""
        cm = ConnectionManager(server_url="http://localhost:8000", team_id="t1")
        mock_ws = AsyncMock()
        mock_ws.receive_event = AsyncMock(return_value={"type": "test"})
        cm._ws_client = mock_ws
        cm._state = ConnectionState.CONNECTED

        event = await cm.receive_event()

        assert event == {"type": "test"}
        assert cm._last_event_time > 0

    async def test_receive_not_connected_raises(self) -> None:
        """AC #5: receive_event() raises WsConnectionError when not connected."""
        cm = ConnectionManager(server_url="http://localhost:8000", team_id="t1")

        with pytest.raises(WsConnectionError, match="Not connected"):
            await cm.receive_event()

    async def test_receive_triggers_reconnect_on_failure(self) -> None:
        """AC #5: ConnectionClosed triggers reconnect, then resumes streaming."""
        cm = ConnectionManager(server_url="http://localhost:8000", team_id="t1")
        mock_ws = AsyncMock()

        call_count = 0

        async def recv_side_effect() -> dict:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise websockets.exceptions.ConnectionClosedError(
                    rcvd=None, sent=None
                )
            return {"type": "after_reconnect"}

        mock_ws.receive_event = AsyncMock(side_effect=recv_side_effect)
        cm._ws_client = mock_ws
        cm._state = ConnectionState.CONNECTED

        # Mock _reconnect to succeed without actual reconnection
        cm._reconnect = AsyncMock()  # type: ignore[method-assign]

        event = await cm.receive_event()

        cm._reconnect.assert_called_once()
        assert event == {"type": "after_reconnect"}

    async def test_receive_propagates_reconnect_failure(self) -> None:
        """AC #5: WsConnectionError propagates when reconnect exhausted."""
        cm = ConnectionManager(server_url="http://localhost:8000", team_id="t1")
        mock_ws = AsyncMock()
        mock_ws.receive_event = AsyncMock(
            side_effect=websockets.exceptions.ConnectionClosedError(
                rcvd=None, sent=None
            )
        )
        cm._ws_client = mock_ws
        cm._state = ConnectionState.CONNECTED

        # Mock _reconnect to raise (retries exhausted)
        cm._reconnect = AsyncMock(  # type: ignore[method-assign]
            side_effect=WsConnectionError(
                "Reconnection failed after 10 attempts", retryable=False
            )
        )

        with pytest.raises(WsConnectionError, match="Reconnection failed"):
            await cm.receive_event()


class TestSwitchTeam:
    """AC #7: Atomic team switch."""

    async def test_switch_success(self) -> None:
        """AC #7: New WS connects first, old WS closed after."""
        old_ws = AsyncMock()
        new_ws = AsyncMock()
        new_ws.connect = AsyncMock(return_value=new_ws)

        cm = ConnectionManager(server_url="http://localhost:8000", team_id="t1")
        cm._ws_client = old_ws
        cm._state = ConnectionState.CONNECTED

        with patch(_WS_CLIENT_PATH, return_value=new_ws):
            await cm.switch_team("t2")

        assert cm.team_id == "t2"
        assert cm._ws_client is new_ws
        old_ws.close.assert_called_once()

    async def test_switch_failure_preserves_old(self) -> None:
        """AC #7: Failed switch leaves old connection untouched."""
        old_ws = AsyncMock()
        new_ws = AsyncMock()
        new_ws.connect = AsyncMock(side_effect=WsConnectionError("refused"))

        cm = ConnectionManager(server_url="http://localhost:8000", team_id="t1")
        cm._ws_client = old_ws
        cm._state = ConnectionState.CONNECTED

        with patch(_WS_CLIENT_PATH, return_value=new_ws):
            with pytest.raises(WsConnectionError):
                await cm.switch_team("bad-team")

        assert cm.team_id == "t1"
        assert cm._ws_client is old_ws
        old_ws.close.assert_not_called()


class TestClose:
    """AC #2: close() method."""

    async def test_close(self) -> None:
        cm = ConnectionManager(server_url="http://localhost:8000", team_id="t1")
        mock_ws = AsyncMock()
        cm._ws_client = mock_ws
        cm._state = ConnectionState.CONNECTED

        await cm.close()

        mock_ws.close.assert_called_once()
        assert cm._ws_client is None
        assert cm.state == ConnectionState.DISCONNECTED

    async def test_close_when_not_connected(self) -> None:
        cm = ConnectionManager(server_url="http://localhost:8000", team_id="t1")

        await cm.close()  # Should not raise

        assert cm.state == ConnectionState.DISCONNECTED


class TestContextManager:
    """Context manager support."""

    async def test_context_manager(self) -> None:
        mock_ws = AsyncMock()
        mock_ws.connect = AsyncMock(return_value=mock_ws)

        with patch(_WS_CLIENT_PATH, return_value=mock_ws):
            cm = ConnectionManager(server_url="http://localhost:8000", team_id="t1")
            async with cm as conn:
                assert conn is cm
                assert cm.state == ConnectionState.CONNECTED

        assert cm.state == ConnectionState.DISCONNECTED


class TestCheckHealth:
    """AC #6: Health monitoring."""

    async def test_ping_when_idle(self) -> None:
        """AC #6: Ping sent when no event for 60+ seconds."""
        cm = ConnectionManager(server_url="http://localhost:8000", team_id="t1")
        mock_ws = AsyncMock()
        mock_ws.ping = AsyncMock()
        cm._ws_client = mock_ws
        cm._state = ConnectionState.CONNECTED
        # Set last event time to 120 seconds before current monotonic time
        cm._last_event_time = time.monotonic() - 120.0

        await cm.check_health()

        mock_ws.ping.assert_called_once()

    async def test_no_ping_when_recent_event(self) -> None:
        """AC #6: No ping when events are recent."""
        cm = ConnectionManager(server_url="http://localhost:8000", team_id="t1")
        mock_ws = AsyncMock()
        mock_ws.ping = AsyncMock()
        cm._ws_client = mock_ws
        cm._state = ConnectionState.CONNECTED
        cm._last_event_time = time.monotonic()  # just now

        await cm.check_health()

        mock_ws.ping.assert_not_called()

    async def test_ping_failure_triggers_reconnect(self) -> None:
        """AC #6: Failed ping triggers reconnect."""
        cm = ConnectionManager(server_url="http://localhost:8000", team_id="t1")
        mock_ws = AsyncMock()
        mock_ws.ping = AsyncMock(side_effect=ConnectionError("dead"))
        cm._ws_client = mock_ws
        cm._state = ConnectionState.CONNECTED
        # Ensure the 60s idle threshold is exceeded even on fresh CI runners
        cm._last_event_time = time.monotonic() - 120.0

        cm._reconnect = AsyncMock()  # type: ignore[method-assign]

        await cm.check_health()

        cm._reconnect.assert_called_once()
