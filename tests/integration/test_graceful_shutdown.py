"""Integration tests — graceful shutdown sequence (Story 14.5 + ADR-015).

Exercises the real wired shutdown stack (LocalWorkerHandle, ConnectionManager,
lifespan) with TestModel LLM injection.

ADR-015 Decision 2 simplified stop_all(): it calls actor_system.shutdown()
directly without per-team stop_team() calls.  Teams keep RUNNING status so
they can be resumed on next server start.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from akgentic.infra.server.deps import CommunityServices
from akgentic.infra.server.routes.ws import ConnectionManager

from ._helpers import create_team

pytestmark = [pytest.mark.smoke]


# ---------------------------------------------------------------------------
# AC #1 — stop_all shuts down actor system, teams stay RUNNING for resume
# ---------------------------------------------------------------------------


def test_stop_all_shuts_down_actor_system_teams_stay_running(
    smoke_services: CommunityServices,
    smoke_client: TestClient,
) -> None:
    """stop_all() shuts down the actor system; teams remain 'running' for resume."""
    team_id = create_team(smoke_client)

    # Verify team is running
    resp = smoke_client.get(f"/teams/{team_id}")
    assert resp.status_code == 200
    assert resp.json()["status"] == "running"

    with patch.object(
        smoke_services.worker_handle._actor_system,
        "shutdown",
        wraps=smoke_services.worker_handle._actor_system.shutdown,
    ) as mock_shutdown:
        smoke_services.worker_handle.stop_all()

    # Actor system must be shut down
    mock_shutdown.assert_called_once()

    # ADR-015: teams keep RUNNING status for resume on next server start
    resp = smoke_client.get(f"/teams/{team_id}")
    assert resp.status_code == 200
    assert resp.json()["status"] == "running"


# ---------------------------------------------------------------------------
# AC #2 — stop_all uses simplified path: no per-team stop_team() calls
# ---------------------------------------------------------------------------


def test_stop_all_does_not_call_stop_team(
    smoke_services: CommunityServices,
    smoke_client: TestClient,
) -> None:
    """stop_all() calls actor_system.shutdown() directly, never stop_team()."""
    create_team(smoke_client)
    create_team(smoke_client)

    with (
        patch.object(
            smoke_services.worker_handle, "stop_team",
        ) as mock_stop_team,
        patch.object(
            smoke_services.worker_handle._actor_system,
            "shutdown",
            wraps=smoke_services.worker_handle._actor_system.shutdown,
        ) as mock_shutdown,
    ):
        smoke_services.worker_handle.stop_all()

    # Simplified path: actor_system.shutdown() only, no per-team teardown
    mock_shutdown.assert_called_once()
    mock_stop_team.assert_not_called()


# ---------------------------------------------------------------------------
# AC #3 — lifespan shutdown sequence: draining, disconnect_all, stop_all
# ---------------------------------------------------------------------------


def test_lifespan_shutdown_sequence(
    smoke_app: FastAPI,
    smoke_services: CommunityServices,
) -> None:
    """Verify lifespan sets draining=True and calls disconnect_all then stop_all."""
    call_order: list[str] = []

    original_disconnect_all = smoke_app.state.connection_manager.disconnect_all
    original_stop_all = smoke_services.worker_handle.stop_all

    async def tracked_disconnect_all() -> None:
        call_order.append("disconnect_all")
        await original_disconnect_all()

    def tracked_stop_all() -> None:
        call_order.append("stop_all")
        original_stop_all()

    smoke_app.state.connection_manager.disconnect_all = tracked_disconnect_all
    smoke_services.worker_handle.stop_all = tracked_stop_all

    # Use TestClient as context manager to trigger lifespan startup + shutdown
    with TestClient(smoke_app):
        assert smoke_app.state.draining is False

    # After exiting the context manager, shutdown has run
    assert smoke_app.state.draining is True
    assert "disconnect_all" in call_order
    assert "stop_all" in call_order
    assert call_order.index("disconnect_all") < call_order.index("stop_all")


# ---------------------------------------------------------------------------
# AC #5 — WebSocket receives close code 1001 on shutdown
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_websocket_receives_1001_on_shutdown() -> None:
    """Verify disconnect_all sends close code 1001 to tracked WebSocket connections.

    Uses a real ConnectionManager with an AsyncMock WebSocket to verify the
    close code without the threading complexity of TestClient WebSocket.
    This validates the same code path that the lifespan shutdown invokes.
    """
    conn_mgr = ConnectionManager()
    ws = AsyncMock()
    conn_mgr.track(ws)

    assert ws in conn_mgr._active

    await conn_mgr.disconnect_all()

    ws.close.assert_awaited_once_with(code=1001, reason="Server shutting down")
    assert len(conn_mgr._active) == 0


def test_websocket_tracked_during_connection(
    smoke_app: FastAPI,
    smoke_client: TestClient,
) -> None:
    """Verify that a WebSocket connection to a real team is tracked by ConnectionManager."""
    team_id = create_team(smoke_client)
    conn_mgr: ConnectionManager = smoke_app.state.connection_manager

    # Before connecting, no active WebSockets
    initial_count = len(conn_mgr._active)

    with smoke_client.websocket_connect(f"/ws/{team_id}"):
        # During connection, the WebSocket should be tracked
        assert len(conn_mgr._active) > initial_count

    # After disconnect, the WebSocket is untracked
    assert len(conn_mgr._active) == initial_count
