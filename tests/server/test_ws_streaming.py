"""Tests for the WebSocket streaming supervisor.

Unit tests that exercise ``_run_streaming_loop`` directly with stubbed
``service``, ``reader``, and ``websocket`` dependencies. Covers the
supervisor's cancellation path (issue #227, Epic 21), the ``StreamClosed``
→ idle-loop transition, and the executor-isolation invariant
(``_WS_READER_POOL`` is distinct from ``asyncio``'s default executor).
"""

from __future__ import annotations

import asyncio
import logging
import re
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import WebSocketDisconnect

from akgentic.infra.protocols.event_stream import StreamClosed
from akgentic.infra.server.routes.ws import (
    ConnectionManager,
    _get_reader_pool,
    _run_streaming_loop,
    shutdown_reader_pool,
)


@pytest.fixture(autouse=True)
def _reset_reader_pool() -> None:
    """Ensure each test starts and ends with a fresh module-level pool."""
    shutdown_reader_pool()
    yield
    shutdown_reader_pool()


def _make_mocks(
    *,
    reader_pool_size: int = 4,
) -> tuple[AsyncMock, MagicMock, uuid.UUID, ConnectionManager]:
    """Build minimal mocks for ``_run_streaming_loop``.

    ``websocket.receive`` is an ``AsyncMock`` that blocks forever by default —
    tests that want the supervisor to finish via the ``watch_disconnect`` side
    must override it explicitly.
    """
    websocket = AsyncMock()
    websocket.app = MagicMock()
    websocket.app.state = SimpleNamespace(
        settings=SimpleNamespace(ws_reader_pool_size=reader_pool_size),
    )

    async def _block_forever() -> None:
        await asyncio.Event().wait()

    websocket.receive.side_effect = _block_forever

    service = MagicMock()
    event_stream = MagicMock()
    reader = MagicMock()
    event_stream.subscribe.return_value = reader
    service.get_event_stream.return_value = event_stream

    team_id = uuid.uuid4()
    conn_mgr = ConnectionManager()

    return websocket, service, team_id, conn_mgr


# ---------------------------------------------------------------------------
# Cancellation path (ADR-015 / issue #227)
# ---------------------------------------------------------------------------


async def test_cancelled_error_closes_reader() -> None:
    """Cancellation of the reader future still triggers ``reader.close()``."""
    websocket, service, team_id, conn_mgr = _make_mocks()
    reader = service.get_event_stream().subscribe.return_value

    # read_next returns None (timeout tick) so pump loops; watch_disconnect
    # raises WebSocketDisconnect and wins the race.
    reader.read_next.return_value = None

    async def _receive_disconnect() -> None:
        raise WebSocketDisconnect()

    websocket.receive.side_effect = _receive_disconnect

    await _run_streaming_loop(websocket, service, team_id, conn_mgr)
    reader.close.assert_called_once()


async def test_cancelled_error_logs_streaming_stopped(caplog: pytest.LogCaptureFixture) -> None:
    """A completed supervisor emits 'WebSocket streaming stopped'."""
    websocket, service, team_id, conn_mgr = _make_mocks()
    service.get_event_stream().subscribe.return_value.read_next.return_value = None

    async def _receive_disconnect() -> None:
        raise WebSocketDisconnect()

    websocket.receive.side_effect = _receive_disconnect

    with caplog.at_level(logging.INFO, logger="akgentic.infra.server.routes.ws"):
        await _run_streaming_loop(websocket, service, team_id, conn_mgr)

    assert any("WebSocket streaming stopped" in msg for msg in caplog.messages)


async def test_cancelled_error_does_not_propagate() -> None:
    """Supervisor swallows cancellation — no exception escapes."""
    websocket, service, team_id, conn_mgr = _make_mocks()
    service.get_event_stream().subscribe.return_value.read_next.return_value = None

    async def _receive_disconnect() -> None:
        raise WebSocketDisconnect()

    websocket.receive.side_effect = _receive_disconnect

    await _run_streaming_loop(websocket, service, team_id, conn_mgr)


# ---------------------------------------------------------------------------
# AC 15: StreamClosed transitions to idle loop
# ---------------------------------------------------------------------------


async def test_stream_closed_transitions_to_idle_loop() -> None:
    """``StreamClosed`` from pump drives the idle-loop fallback.

    ``conn_mgr.clear_restored(team_id)`` is called and ``_run_idle_loop`` is
    awaited exactly once. Uses sync ``MagicMock`` for the reader — the
    StreamReader protocol is sync; no ``AsyncMock.read_next``.
    """
    websocket, service, team_id, conn_mgr = _make_mocks()
    reader = service.get_event_stream().subscribe.return_value
    reader.read_next.side_effect = StreamClosed()

    conn_mgr.mark_restored(team_id)  # should be cleared by the transition

    mock_idle = AsyncMock()
    with patch(
        "akgentic.infra.server.routes.ws._run_idle_loop",
        new=mock_idle,
    ):
        await _run_streaming_loop(websocket, service, team_id, conn_mgr)

    assert not conn_mgr.is_restored(team_id)
    mock_idle.assert_awaited_once()
    reader.close.assert_called_once()


# ---------------------------------------------------------------------------
# AC 14: Dedicated reader pool, distinct from the default executor
# ---------------------------------------------------------------------------


async def test_reader_pool_is_dedicated_not_default() -> None:
    """``_WS_READER_POOL`` is a dedicated ``ThreadPoolExecutor``.

    Also asserts ``max_workers`` matches ``settings.ws_reader_pool_size``.
    """
    from concurrent.futures import ThreadPoolExecutor

    websocket = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                settings=SimpleNamespace(ws_reader_pool_size=7),
            ),
        ),
    )
    pool = _get_reader_pool(websocket)  # type: ignore[arg-type]

    assert isinstance(pool, ThreadPoolExecutor)
    default_exec = asyncio.get_running_loop()._default_executor
    assert pool is not default_exec  # may be None or a distinct pool
    assert pool._max_workers == 7  # type: ignore[attr-defined]


def test_ws_py_never_uses_default_executor() -> None:
    """Static assertion: ``ws.py`` never dispatches via ``run_in_executor(None, …)``.

    Dispatching reader polling onto ``asyncio``'s default executor would
    defeat the isolation contract of ``_WS_READER_POOL`` (issue #227) — a
    subtle regression that is hard to reproduce in a behavioural test, so
    we guard it with a source-level grep.
    """
    ws_src = (
        Path(__file__).resolve().parents[2]
        / "src"
        / "akgentic"
        / "infra"
        / "server"
        / "routes"
        / "ws.py"
    )
    source = ws_src.read_text()
    assert not re.search(r"run_in_executor\(\s*None\b", source), (
        "ws.py must never dispatch reader polling via the default executor"
    )


async def test_reader_pool_is_reused_across_calls() -> None:
    """Repeated ``_get_reader_pool`` calls return the same singleton."""
    websocket = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                settings=SimpleNamespace(ws_reader_pool_size=2),
            ),
        ),
    )
    pool_a = _get_reader_pool(websocket)  # type: ignore[arg-type]
    pool_b = _get_reader_pool(websocket)  # type: ignore[arg-type]
    assert pool_a is pool_b


async def test_shutdown_reader_pool_resets_singleton() -> None:
    """``shutdown_reader_pool`` releases the executor and allows re-construction."""
    websocket = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                settings=SimpleNamespace(ws_reader_pool_size=2),
            ),
        ),
    )
    pool_a = _get_reader_pool(websocket)  # type: ignore[arg-type]
    shutdown_reader_pool()
    pool_b = _get_reader_pool(websocket)  # type: ignore[arg-type]
    assert pool_a is not pool_b


# ---------------------------------------------------------------------------
# Supervisor behaviour: event forwarding and adapter wrapping
# ---------------------------------------------------------------------------


async def test_supervisor_forwards_events_until_disconnect() -> None:
    """Events returned from ``read_next`` are forwarded via ``send_text``."""
    websocket, service, team_id, conn_mgr = _make_mocks()
    reader = service.get_event_stream().subscribe.return_value

    event = MagicMock()
    event.model_dump_json.return_value = '{"hello": "world"}'
    # First tick returns the event, subsequent ticks return None (timeout).
    reader.read_next.side_effect = [event, None, None, None]

    async def _receive_disconnect() -> None:
        # Give pump a chance to forward the event before we exit.
        await asyncio.sleep(0)
        raise WebSocketDisconnect()

    websocket.receive.side_effect = _receive_disconnect

    await _run_streaming_loop(websocket, service, team_id, conn_mgr)

    websocket.send_text.assert_any_call('{"hello": "world"}')
    reader.close.assert_called_once()


async def test_supervisor_skips_unserializable_events() -> None:
    """A ``send_text`` failure is logged at debug level and pump continues."""
    websocket, service, team_id, conn_mgr = _make_mocks()
    reader = service.get_event_stream().subscribe.return_value

    bad_event = MagicMock()
    bad_event.model_dump_json.side_effect = RuntimeError("not json")
    reader.read_next.side_effect = [bad_event, None, None]

    async def _receive_disconnect() -> None:
        await asyncio.sleep(0)
        raise WebSocketDisconnect()

    websocket.receive.side_effect = _receive_disconnect

    await _run_streaming_loop(websocket, service, team_id, conn_mgr)
    reader.close.assert_called_once()
