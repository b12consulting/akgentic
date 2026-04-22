"""WebSocket client wrapper for real-time event streaming."""

from __future__ import annotations

import json
import logging
import warnings

import websockets.asyncio.client
import websockets.exceptions

from akgentic.core.messages.message import Message
from akgentic.core.utils.deserializer import deserialize_object

_log = logging.getLogger(__name__)


class WsConnectionError(Exception):
    """WebSocket connection failed."""

    def __init__(self, reason: str, *, retryable: bool = True) -> None:
        self.reason = reason
        self.retryable = retryable
        super().__init__(reason)


class WsClient:
    """Async WebSocket client for streaming team events."""

    def __init__(self, base_url: str, team_id: str, api_key: str | None = None) -> None:
        ws_url = base_url.replace("https://", "wss://").replace("http://", "ws://")
        self._url = f"{ws_url}/ws/{team_id}"
        self._headers: list[tuple[str, str]] = []
        if api_key:
            self._headers.append(("Authorization", f"Bearer {api_key}"))
        self._ws: websockets.asyncio.client.ClientConnection | None = None
        self._v1_warned: bool = False

    @property
    def url(self) -> str:
        """Return the resolved WebSocket URL."""
        return self._url

    async def connect(self) -> WsClient:
        """Open WebSocket connection."""
        try:
            self._ws = await websockets.asyncio.client.connect(
                self._url,
                additional_headers=self._headers,
            )
        except (ConnectionRefusedError, OSError) as exc:
            raise WsConnectionError(f"Connection error: {exc}", retryable=True) from exc
        except websockets.exceptions.InvalidStatus as exc:
            status = exc.response.status_code
            if status == 404 or status == 403:
                raise WsConnectionError("Team not found", retryable=False) from exc
            raise WsConnectionError(f"WebSocket rejected: HTTP {status}", retryable=True) from exc
        except websockets.exceptions.InvalidHandshake as exc:
            raise WsConnectionError(f"WebSocket handshake failed: {exc}", retryable=True) from exc
        return self

    async def receive_event(self) -> Message:
        """Read next JSON message from WebSocket and deserialize to a typed Message.

        Returns:
            Deserialized ``Message`` instance.

        Raises:
            RuntimeError: If not connected.
            WsConnectionError: If deserialization fails persistently (event skipped
                internally; this is only raised by the caller for connection issues).
        """
        if self._ws is None:
            raise RuntimeError("Not connected")
        while True:
            raw = await self._ws.recv()
            text = raw if isinstance(raw, str) else raw.decode("utf-8")
            parsed = json.loads(text)

            # DEPRECATED: V1 dual-format envelope unwrapping — remove when
            # AngularV1Adapter is retired.
            if isinstance(parsed, dict) and "payload" in parsed:
                if not self._v1_warned:
                    warnings.warn(
                        "V1 dual-format envelope detected; this path is deprecated",
                        DeprecationWarning,
                        stacklevel=2,
                    )
                    self._v1_warned = True
                raw_dict = parsed.get("event", parsed)
            else:
                raw_dict = parsed

            try:
                result = deserialize_object(raw_dict)
            except ValueError:
                _log.warning("Skipping malformed event: deserialization failed", exc_info=True)
                continue

            if not isinstance(result, Message):
                _log.warning("Skipping event: deserialized to %s, expected Message", type(result))
                continue

            return result

    async def ping(self) -> None:
        """Send a WebSocket ping frame. Raises if not connected."""
        if self._ws is None:
            raise RuntimeError("Not connected")
        await self._ws.ping()

    async def close(self) -> None:
        """Close WebSocket connection."""
        if self._ws is not None:
            await self._ws.close()
            self._ws = None

    async def __aenter__(self) -> WsClient:
        return await self.connect()

    async def __aexit__(self, *_args: object) -> None:
        await self.close()
