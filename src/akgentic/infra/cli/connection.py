"""Connection manager with auto-reconnect for WebSocket event streaming."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from enum import Enum

import websockets.exceptions

from akgentic.core.messages.message import Message
from akgentic.infra.cli.ws_client import WsClient, WsConnectionError


class ConnectionState(Enum):
    """WebSocket connection lifecycle states."""

    CONNECTING = "connecting"
    CONNECTED = "connected"
    RECONNECTING = "reconnecting"
    DISCONNECTED = "disconnected"


class ConnectionManager:
    """Manages WebSocket lifecycle with auto-reconnect and atomic team switching.

    Composes WsClient instances internally. Provides exponential backoff
    reconnection on unexpected disconnects.
    """

    def __init__(
        self,
        server_url: str,
        team_id: str,
        api_key: str | None = None,
        on_state_change: Callable[[ConnectionState], None] | None = None,
        max_retries: int = 10,
    ) -> None:
        self._server_url = server_url
        self._team_id = team_id
        self._api_key = api_key
        self._on_state_change = on_state_change
        self._max_retries = max_retries
        self._ws_client: WsClient | None = None
        self._state: ConnectionState = ConnectionState.DISCONNECTED
        self._last_event_time: float = 0.0

    @property
    def state(self) -> ConnectionState:
        """Current connection state."""
        return self._state

    @property
    def team_id(self) -> str:
        """Currently connected team ID."""
        return self._team_id

    def _set_state(self, new_state: ConnectionState) -> None:
        """Update state and fire callback if set."""
        self._state = new_state
        if self._on_state_change is not None:
            self._on_state_change(new_state)

    async def connect(self) -> None:
        """Single connection attempt. Raises WsConnectionError on failure."""
        self._set_state(ConnectionState.CONNECTING)
        ws = WsClient(
            base_url=self._server_url,
            team_id=self._team_id,
            api_key=self._api_key,
        )
        try:
            await ws.connect()
        except WsConnectionError:
            self._set_state(ConnectionState.DISCONNECTED)
            raise
        self._ws_client = ws
        self._set_state(ConnectionState.CONNECTED)
        self._last_event_time = time.monotonic()

    async def _reconnect(self) -> None:
        """Reconnect with exponential backoff. Raises WsConnectionError after max_retries."""
        self._set_state(ConnectionState.RECONNECTING)
        for attempt in range(self._max_retries):
            delay = min(2**attempt, 30)
            try:
                await self.connect()
                return  # Success — state is now CONNECTED
            except WsConnectionError:
                await asyncio.sleep(delay)
        self._set_state(ConnectionState.DISCONNECTED)
        raise WsConnectionError(
            f"Reconnection failed after {self._max_retries} attempts",
            retryable=False,
        )

    async def receive_event(self) -> Message:
        """Read next event. Triggers reconnect on disconnect.

        Raises WsConnectionError only after max_retries exhausted.
        """
        while True:
            if self._ws_client is None:
                raise WsConnectionError("Not connected", retryable=False)
            try:
                event = await self._ws_client.receive_event()
                self._last_event_time = time.monotonic()
                return event
            except websockets.exceptions.ConnectionClosed:
                await self._reconnect()

    async def close(self) -> None:
        """Graceful shutdown."""
        if self._ws_client is not None:
            await self._ws_client.close()
            self._ws_client = None
        self._set_state(ConnectionState.DISCONNECTED)

    async def switch_team(self, new_team_id: str) -> None:
        """Atomic team switch: connect new WebSocket before closing old.

        On failure, old connection remains untouched.
        """
        new_ws = WsClient(
            base_url=self._server_url,
            team_id=new_team_id,
            api_key=self._api_key,
        )
        try:
            await new_ws.connect()
        except WsConnectionError:
            raise  # Old connection untouched
        old_ws = self._ws_client
        self._ws_client = new_ws
        self._team_id = new_team_id
        self._last_event_time = time.monotonic()
        if old_ws is not None:
            await old_ws.close()

    async def check_health(self) -> None:
        """Ping if no event received for 60s. Trigger reconnect on failure."""
        if (
            self._state == ConnectionState.CONNECTED
            and time.monotonic() - self._last_event_time > 60.0
            and self._ws_client is not None
        ):
            try:
                await self._ws_client.ping()
            except Exception:  # noqa: BLE001
                await self._reconnect()

    async def __aenter__(self) -> ConnectionManager:
        """Connect on context manager entry."""
        await self.connect()
        return self

    async def __aexit__(self, *_args: object) -> None:
        """Close on context manager exit."""
        await self.close()
