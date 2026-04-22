"""Tests for WsClient WebSocket wrapper."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import websockets.exceptions

from akgentic.core.messages.message import Message
from akgentic.infra.cli.ws_client import WsClient, WsConnectionError


class TestUrlConversion:
    def test_http_to_ws(self) -> None:
        client = WsClient("http://localhost:8000", "team-1")
        assert client.url == "ws://localhost:8000/ws/team-1"

    def test_https_to_wss(self) -> None:
        client = WsClient("https://example.com", "team-2")
        assert client.url == "wss://example.com/ws/team-2"

    def test_preserves_port(self) -> None:
        client = WsClient("http://host:9090", "t3")
        assert client.url == "ws://host:9090/ws/t3"

    def test_api_key_stored(self) -> None:
        client = WsClient("http://localhost:8000", "t1", api_key="secret")
        assert ("Authorization", "Bearer secret") in client._headers

    def test_no_api_key(self) -> None:
        client = WsClient("http://localhost:8000", "t1")
        assert client._headers == []


class TestWsConnectionError:
    def test_default_retryable(self) -> None:
        err = WsConnectionError("reason")
        assert err.reason == "reason"
        assert err.retryable is True
        assert str(err) == "reason"

    def test_not_retryable(self) -> None:
        err = WsConnectionError("reason", retryable=False)
        assert err.retryable is False

    def test_str_output(self) -> None:
        err = WsConnectionError("some reason")
        assert str(err) == "some reason"


class TestConnect:
    async def test_connect_success(self) -> None:
        mock_ws = AsyncMock()
        with patch(
            "akgentic.infra.cli.ws_client.websockets.asyncio.client.connect",
            new_callable=AsyncMock,
            return_value=mock_ws,
        ):
            client = WsClient("http://localhost:8000", "t1")
            result = await client.connect()
            assert result is client
            assert client._ws is mock_ws

    async def test_connect_refused_raises_ws_error(self) -> None:
        with patch(
            "akgentic.infra.cli.ws_client.websockets.asyncio.client.connect",
            new_callable=AsyncMock,
            side_effect=ConnectionRefusedError("refused"),
        ):
            client = WsClient("http://localhost:8000", "t1")
            with pytest.raises(WsConnectionError) as exc_info:
                await client.connect()
            assert exc_info.value.retryable is True
            assert "Connection error" in exc_info.value.reason

    async def test_connect_os_error_raises_ws_error(self) -> None:
        with patch(
            "akgentic.infra.cli.ws_client.websockets.asyncio.client.connect",
            new_callable=AsyncMock,
            side_effect=OSError("network error"),
        ):
            client = WsClient("http://localhost:8000", "t1")
            with pytest.raises(WsConnectionError) as exc_info:
                await client.connect()
            assert exc_info.value.retryable is True

    async def test_connect_invalid_status_404_raises_ws_error(self) -> None:
        mock_response = MagicMock()
        mock_response.status_code = 404
        exc = websockets.exceptions.InvalidStatus(mock_response)
        with patch(
            "akgentic.infra.cli.ws_client.websockets.asyncio.client.connect",
            new_callable=AsyncMock,
            side_effect=exc,
        ):
            client = WsClient("http://localhost:8000", "t1")
            with pytest.raises(WsConnectionError) as exc_info:
                await client.connect()
            assert exc_info.value.retryable is False
            assert "not found" in exc_info.value.reason.lower()

    async def test_connect_invalid_status_403_raises_ws_error(self) -> None:
        mock_response = MagicMock()
        mock_response.status_code = 403
        exc = websockets.exceptions.InvalidStatus(mock_response)
        with patch(
            "akgentic.infra.cli.ws_client.websockets.asyncio.client.connect",
            new_callable=AsyncMock,
            side_effect=exc,
        ):
            client = WsClient("http://localhost:8000", "t1")
            with pytest.raises(WsConnectionError) as exc_info:
                await client.connect()
            assert exc_info.value.retryable is False

    async def test_connect_invalid_status_500_raises_ws_error(self) -> None:
        mock_response = MagicMock()
        mock_response.status_code = 500
        exc = websockets.exceptions.InvalidStatus(mock_response)
        with patch(
            "akgentic.infra.cli.ws_client.websockets.asyncio.client.connect",
            new_callable=AsyncMock,
            side_effect=exc,
        ):
            client = WsClient("http://localhost:8000", "t1")
            with pytest.raises(WsConnectionError) as exc_info:
                await client.connect()
            assert exc_info.value.retryable is True
            assert "HTTP 500" in exc_info.value.reason

    async def test_connect_invalid_handshake_raises_ws_error(self) -> None:
        exc = websockets.exceptions.InvalidHandshake("bad handshake")
        with patch(
            "akgentic.infra.cli.ws_client.websockets.asyncio.client.connect",
            new_callable=AsyncMock,
            side_effect=exc,
        ):
            client = WsClient("http://localhost:8000", "t1")
            with pytest.raises(WsConnectionError) as exc_info:
                await client.connect()
            assert exc_info.value.retryable is True
            assert "handshake failed" in exc_info.value.reason.lower()


class TestReceiveEvent:
    async def test_receive_json_returns_message(self) -> None:
        """receive_event() deserializes JSON string into a typed Message."""
        raw = {"__model__": "SomeModel", "data": "hello"}
        mock_ws = AsyncMock()
        mock_ws.recv = AsyncMock(return_value=json.dumps(raw))
        mock_msg = MagicMock(spec=Message)
        client = WsClient("http://localhost:8000", "t1")
        client._ws = mock_ws
        with patch(
            "akgentic.infra.cli.ws_client.deserialize_object",
            return_value=mock_msg,
        ) as mock_deser:
            result = await client.receive_event()
        assert result is mock_msg
        mock_deser.assert_called_once_with(raw)

    async def test_receive_bytes_returns_message(self) -> None:
        """receive_event() handles bytes input and returns typed Message."""
        raw = {"__model__": "SomeModel", "data": "hi"}
        mock_ws = AsyncMock()
        mock_ws.recv = AsyncMock(return_value=json.dumps(raw).encode("utf-8"))
        mock_msg = MagicMock(spec=Message)
        client = WsClient("http://localhost:8000", "t1")
        client._ws = mock_ws
        with patch(
            "akgentic.infra.cli.ws_client.deserialize_object",
            return_value=mock_msg,
        ):
            result = await client.receive_event()
        assert result is mock_msg

    async def test_v1_envelope_unwraps_event_key(self) -> None:
        """V1 dual-format envelope: extracts 'event' dict before deserializing."""
        inner = {"__model__": "FQN.SentMessage", "content": "hi"}
        envelope = {"payload": {"type": "v1-stuff"}, "event": inner}
        mock_ws = AsyncMock()
        mock_ws.recv = AsyncMock(return_value=json.dumps(envelope))
        mock_msg = MagicMock(spec=Message)
        client = WsClient("http://localhost:8000", "t1")
        client._ws = mock_ws
        with patch(
            "akgentic.infra.cli.ws_client.deserialize_object",
            return_value=mock_msg,
        ) as mock_deser:
            result = await client.receive_event()
        assert result is mock_msg
        mock_deser.assert_called_once_with(inner)

    async def test_deserialization_failure_skips_event(self) -> None:
        """ValueError from deserialize_object skips event and retries."""
        bad_raw = {"__model__": "Unknown"}
        good_raw = {"__model__": "Good"}
        mock_ws = AsyncMock()
        mock_ws.recv = AsyncMock(
            side_effect=[json.dumps(bad_raw), json.dumps(good_raw)]
        )
        mock_msg = MagicMock(spec=Message)
        client = WsClient("http://localhost:8000", "t1")
        client._ws = mock_ws
        with patch(
            "akgentic.infra.cli.ws_client.deserialize_object",
            side_effect=[ValueError("unknown"), mock_msg],
        ):
            result = await client.receive_event()
        assert result is mock_msg

    async def test_non_message_result_skips_event(self) -> None:
        """If deserialize_object returns a non-Message, event is skipped."""
        mock_ws = AsyncMock()
        mock_ws.recv = AsyncMock(
            side_effect=[json.dumps({"a": 1}), json.dumps({"b": 2})]
        )
        mock_msg = MagicMock(spec=Message)
        client = WsClient("http://localhost:8000", "t1")
        client._ws = mock_ws
        with patch(
            "akgentic.infra.cli.ws_client.deserialize_object",
            side_effect=["not-a-message", mock_msg],
        ):
            result = await client.receive_event()
        assert result is mock_msg

    async def test_receive_not_connected_raises(self) -> None:
        client = WsClient("http://localhost:8000", "t1")
        with pytest.raises(RuntimeError, match="Not connected"):
            await client.receive_event()


class TestClose:
    async def test_close(self) -> None:
        mock_ws = AsyncMock()
        client = WsClient("http://localhost:8000", "t1")
        client._ws = mock_ws
        await client.close()
        mock_ws.close.assert_awaited_once()
        assert client._ws is None

    async def test_close_when_not_connected(self) -> None:
        client = WsClient("http://localhost:8000", "t1")
        await client.close()  # Should not raise


class TestContextManager:
    async def test_async_with(self) -> None:
        mock_ws = AsyncMock()
        with patch(
            "akgentic.infra.cli.ws_client.websockets.asyncio.client.connect",
            new_callable=AsyncMock,
            return_value=mock_ws,
        ):
            client = WsClient("http://localhost:8000", "t1")
            async with client as ws:
                assert ws is client
                assert client._ws is mock_ws
            mock_ws.close.assert_awaited_once()
