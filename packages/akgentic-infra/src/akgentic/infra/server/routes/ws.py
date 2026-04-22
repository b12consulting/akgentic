"""WebSocket route for real-time event streaming via EventStream.

The streaming loop is a v1-style supervisor: ``pump`` forwards events and
``watch_disconnect`` awaits client frames; whichever finishes first cancels
the other. Reader polling runs on a dedicated ``ThreadPoolExecutor``
(``_WS_READER_POOL``) so that blocking ``reader.read_next(timeout)`` calls
cannot starve the default executor. The ``StreamReader`` protocol stays
sync — enterprise adapters (``DaprStreamReader``) are unaffected.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import cast

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from starlette.websockets import WebSocketState

from akgentic.core.messages import Message
from akgentic.infra.protocols.event_stream import StreamClosed, StreamReader
from akgentic.infra.server.routes.frontend_adapter import FrontendAdapter
from akgentic.infra.server.services.team_service import TeamService
from akgentic.team.models import TeamStatus

logger = logging.getLogger(__name__)

router = APIRouter()


_WS_READER_POOL: ThreadPoolExecutor | None = None


def _get_reader_pool(websocket: WebSocket) -> ThreadPoolExecutor:
    """Return the module-level dedicated reader pool, constructing it on first use.

    Sized from ``app.state.settings.ws_reader_pool_size``. The pool is
    process-global and reused across connections. It stays isolated from
    ``asyncio``'s default executor to prevent the WS reader polling
    workload from starving other ``run_in_executor`` callers in the same
    process.

    See issue #227.
    """
    global _WS_READER_POOL
    if _WS_READER_POOL is None:
        settings = websocket.app.state.settings
        _WS_READER_POOL = ThreadPoolExecutor(
            max_workers=settings.ws_reader_pool_size,
            thread_name_prefix="ws-reader",
        )
    return _WS_READER_POOL


def shutdown_reader_pool() -> None:
    """Shut down the dedicated reader pool, cancelling queued futures.

    Invoked by the FastAPI lifespan teardown. ``wait=False`` + ``cancel_futures=True``
    allow the process to exit promptly even if a reader thread is mid-poll;
    the daemon-threaded executor does not block interpreter shutdown.
    """
    global _WS_READER_POOL
    if _WS_READER_POOL is None:
        return
    _WS_READER_POOL.shutdown(wait=False, cancel_futures=True)
    _WS_READER_POOL = None


class ConnectionManager:
    """Manages WebSocket connections for restore notification and graceful shutdown.

    Stored on ``app.state.connection_manager``. Tracks:
    - **active connections** (``_active``) -- every accepted WebSocket, for
      ``disconnect_all()`` during graceful shutdown (ADR-013).
    - **waiting connections** (``_waiting``) -- idle WebSockets waiting for
      a stopped team to be restored.
    - **restored flags** (``_restored``) -- signals idle loops that a team
      has been restored so they can transition to streaming.
    """

    def __init__(self) -> None:
        self._waiting: dict[uuid.UUID, list[WebSocket]] = {}
        self._restored: set[uuid.UUID] = set()
        self._active: set[WebSocket] = set()

    def track(self, ws: WebSocket) -> None:
        """Register an active WebSocket connection for shutdown tracking."""
        self._active.add(ws)

    def untrack(self, ws: WebSocket) -> None:
        """Remove an active WebSocket connection from shutdown tracking."""
        self._active.discard(ws)

    def add_waiting(self, team_id: uuid.UUID, ws: WebSocket) -> None:
        """Register an idle WebSocket waiting for a team to be restored."""
        self._waiting.setdefault(team_id, []).append(ws)

    def remove_waiting(self, team_id: uuid.UUID, ws: WebSocket) -> None:
        """Remove a WebSocket from the waiting list."""
        conns = self._waiting.get(team_id)
        if conns is not None:
            try:
                conns.remove(ws)
            except ValueError:
                pass
            if not conns:
                del self._waiting[team_id]

    def pop_waiting(self, team_id: uuid.UUID) -> list[WebSocket]:
        """Pop and return all waiting connections for a team."""
        return self._waiting.pop(team_id, [])

    def is_restored(self, team_id: uuid.UUID) -> bool:
        """Check if a team has been restored."""
        return team_id in self._restored

    def mark_restored(self, team_id: uuid.UUID) -> None:
        """Mark a team as restored so idle loops can detect it."""
        self._restored.add(team_id)

    def clear_restored(self, team_id: uuid.UUID) -> None:
        """Clear the restored flag for a team."""
        self._restored.discard(team_id)

    async def disconnect_all(self) -> None:
        """Close all active WebSocket connections with code 1001 (Going Away).

        Iterates a snapshot of ``_active`` to avoid mutation during iteration.
        Individual failures are logged and skipped so one broken connection
        does not block the rest.
        """
        snapshot = set(self._active)
        closed = 0
        failed = 0
        for ws in snapshot:
            try:
                await ws.close(code=1001, reason="Server shutting down")
                closed += 1
            except Exception:  # noqa: BLE001
                failed += 1
                logger.warning("Failed to close WebSocket during disconnect_all", exc_info=True)
        self._active.clear()
        logger.info("disconnect_all: closed %d, failed %d connection(s)", closed, failed)


def _get_team_service(ws: WebSocket) -> TeamService:
    """Extract TeamService from app.state."""
    return cast(TeamService, ws.app.state.team_service)


def _get_connection_manager(ws: WebSocket) -> ConnectionManager:
    """Extract ConnectionManager from app.state."""
    return cast(ConnectionManager, ws.app.state.connection_manager)


@router.websocket("/ws/{team_id}")
async def websocket_events(websocket: WebSocket, team_id: uuid.UUID) -> None:
    """Stream real-time orchestrator events over WebSocket.

    - Running team: subscribes and pushes events in real-time.
    - Stopped team: accepts connection and idles until restored.
    - Non-existent team: rejects with close code 4004.
    """
    service = _get_team_service(websocket)
    conn_mgr = _get_connection_manager(websocket)

    process = service.get_team(team_id)
    if process is None:
        logger.info("WebSocket rejected: team_id=%s (not found)", team_id)
        await websocket.close(code=4004, reason="Team not found")
        return

    await websocket.accept()
    conn_mgr.track(websocket)
    logger.info("WebSocket connected: team_id=%s", team_id)

    adapter: FrontendAdapter | None = getattr(websocket.app.state, "frontend_adapter", None)

    try:
        if process.status == TeamStatus.RUNNING:
            await _run_streaming_loop(websocket, service, team_id, conn_mgr, adapter)
        else:
            await _run_idle_loop(websocket, team_id, conn_mgr, service, adapter)
    finally:
        conn_mgr.untrack(websocket)


async def _run_streaming_loop(
    websocket: WebSocket,
    service: TeamService,
    team_id: uuid.UUID,
    conn_mgr: ConnectionManager,
    adapter: FrontendAdapter | None = None,
) -> None:
    """Subscribe to EventStream and forward events over WebSocket.

    Uses a v1-style supervisor: ``pump`` forwards events while
    ``watch_disconnect`` awaits client frames. Whichever finishes first
    cancels the other. Disconnect detection is structural (sub-millisecond
    via ``websocket.receive()``) rather than polled via ``client_state``.
    See issue #227.
    """
    logger.debug("Streaming loop started: team_id=%s", team_id)
    event_stream = service.get_event_stream()
    try:
        reader = event_stream.subscribe(team_id, cursor=0)
    except StreamClosed:
        logger.debug("Stream already closed for team %s, entering idle loop", team_id)
        conn_mgr.clear_restored(team_id)
        await _run_idle_loop(websocket, team_id, conn_mgr, service, adapter)
        return

    stream_closed = await _supervise_stream(websocket, reader, adapter, team_id)

    if stream_closed:
        logger.debug("StreamClosed for team %s, transitioning to idle loop", team_id)
        conn_mgr.clear_restored(team_id)
        await _run_idle_loop(websocket, team_id, conn_mgr, service, adapter)


async def _send_event(
    websocket: WebSocket,
    event: Message,
    adapter: FrontendAdapter | None,
    team_id: uuid.UUID,
) -> None:
    """Serialize and forward a single event, logging and skipping on failure."""
    try:
        if adapter is not None:
            wrapped = adapter.wrap_ws_event(event)
            await websocket.send_text(wrapped.model_dump_json())
        else:
            await websocket.send_text(event.model_dump_json())
        logger.debug("Event forwarded to client: team_id=%s", team_id)
    except WebSocketDisconnect:
        raise
    except Exception:  # noqa: BLE001
        logger.debug("Skipping unserializable event: team_id=%s", team_id)


async def _await_and_suppress(task: asyncio.Task[None]) -> None:
    """Await ``task`` swallowing cancellation, disconnect, and incidental errors."""
    with contextlib.suppress(asyncio.CancelledError, WebSocketDisconnect, Exception):
        await task


async def _supervise_stream(
    websocket: WebSocket,
    reader: StreamReader,
    adapter: FrontendAdapter | None,
    team_id: uuid.UUID,
) -> bool:
    """Race ``pump`` against ``watch_disconnect`` until one finishes.

    Returns ``True`` iff the event stream observed ``StreamClosed`` (callers
    transition to the idle loop). Cancels the losing task and awaits it
    under ``contextlib.suppress`` so cleanup is idempotent.

    Note on cancellation: cancelling ``pump_task`` marks its
    ``run_in_executor`` future as cancelled on the event-loop side, but the
    executor thread still holds ``threading.Event.wait(0.5)`` in
    ``LocalStreamReader.read_next``. ``reader.close()`` below signals the
    event and typically cuts the tick short to single-digit milliseconds.
    """
    stream_closed = False

    async def pump() -> None:
        nonlocal stream_closed
        loop = asyncio.get_running_loop()
        pool = _get_reader_pool(websocket)
        try:
            while True:
                event = await loop.run_in_executor(pool, reader.read_next, 0.5)
                if event is None:
                    continue
                await _send_event(websocket, event, adapter, team_id)
        except StreamClosed:
            stream_closed = True

    async def watch_disconnect() -> None:
        # Silently drops inbound client frames — correct for today's
        # server-push protocol. A future client→server WS protocol must
        # replace this helper. See issue #227.
        try:
            while True:
                await websocket.receive()
        except WebSocketDisconnect:
            return

    pump_task = asyncio.create_task(pump())
    watch_task = asyncio.create_task(watch_disconnect())
    try:
        done, pending = await asyncio.wait(
            [pump_task, watch_task], return_when=asyncio.FIRST_COMPLETED
        )
        for task in pending:
            task.cancel()
            await _await_and_suppress(task)
        for task in done:
            await _await_and_suppress(task)
        logger.info("WebSocket streaming stopped: team_id=%s", team_id)
    finally:
        reader.close()

    return stream_closed


async def _run_idle_loop(
    websocket: WebSocket,
    team_id: uuid.UUID,
    conn_mgr: ConnectionManager,
    service: TeamService,
    adapter: FrontendAdapter | None = None,
) -> None:
    """Wait for team restore or client disconnect.

    The ``timeout=1.0`` polling loop checks ``conn_mgr.is_restored()``
    on each timeout iteration. ``notify_restore()`` marks the team as
    restored, and this loop detects it on the next timeout iteration.
    """
    logger.debug("WebSocket idle, waiting for restore: team_id=%s", team_id)
    conn_mgr.add_waiting(team_id, websocket)
    try:
        while True:
            try:
                await asyncio.wait_for(websocket.receive_text(), timeout=1.0)
            except TimeoutError:
                if conn_mgr.is_restored(team_id):
                    await _run_streaming_loop(websocket, service, team_id, conn_mgr, adapter)
                    return
            except WebSocketDisconnect:
                return
    finally:
        conn_mgr.remove_waiting(team_id, websocket)


def notify_restore(
    conn_mgr: ConnectionManager,
    service: TeamService,
    team_id: uuid.UUID,
) -> None:
    """Called when a team is restored -- signal all waiting WebSocket connections.

    Marks the team as restored in ConnectionManager. Each idle loop detects
    this on its next timeout iteration and starts streaming via EventStream.
    """
    waiting = conn_mgr.pop_waiting(team_id)
    if not waiting:
        return

    logger.info("Notifying %d waiting WebSocket(s) for team %s", len(waiting), team_id)

    conn_mgr.mark_restored(team_id)

    # Re-add all connected WebSockets so they can pick up the restored signal
    for ws in waiting:
        if ws.client_state != WebSocketState.CONNECTED:
            continue
        conn_mgr.add_waiting(team_id, ws)
