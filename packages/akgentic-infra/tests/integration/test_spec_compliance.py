"""Integration tests — spec compliance: app factory, RuntimeCache lifecycle, WS TeamHandle.

Validates ADR-002 remediation for stories 6.1--6.10.
"""

from __future__ import annotations

import time
import uuid
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from akgentic.infra.server.app import create_app
from akgentic.infra.server.settings import CommunitySettings
from akgentic.infra.wiring import wire_community

from ._helpers import create_team

pytestmark = [pytest.mark.integration, pytest.mark.llm]


# ---------------------------------------------------------------------------
# AC #1 — 2-line fixture pattern
# ---------------------------------------------------------------------------


class TestAppFactory:
    """Verify the 2-line fixture pattern: settings -> create_app -> TestClient."""

    def test_app_starts_and_teams_returns_200(
        self,
        integration_client: TestClient,
    ) -> None:
        """AC #1: App created via create_app(settings) serves /teams/ correctly."""
        resp = integration_client.get("/teams/")
        assert resp.status_code == 200

    def test_two_line_fixture_pattern(self, tmp_path: Path) -> None:
        """AC #1: Demonstrate the fixture pattern works end-to-end."""
        from tests.integration.conftest import _seed_integration_catalog

        settings = CommunitySettings(
            workspaces_root=tmp_path / "workspaces",
            event_store_path=tmp_path / "event_store",
            catalog_path=tmp_path / "catalog",
        )
        _seed_integration_catalog(settings.catalog_path)
        services = wire_community(settings)
        try:
            app = create_app(services, settings)
            client = TestClient(app)

            resp = client.get("/teams/")
            assert resp.status_code == 200
            assert "teams" in resp.json()
        finally:
            services.actor_system.shutdown()

    def test_team_create_get_delete_via_test_client(
        self,
        integration_client: TestClient,
        integration_app: FastAPI,
    ) -> None:
        """AC #1: Team create/get/delete work through TestClient."""
        team_id = create_team(integration_client)
        try:
            resp = integration_client.get(f"/teams/{team_id}")
            assert resp.status_code == 200
            assert resp.json()["status"] == "running"

            integration_client.post(f"/teams/{team_id}/stop")
            time.sleep(0.5)

            resp = integration_client.delete(f"/teams/{team_id}")
            assert resp.status_code == 204

            resp = integration_client.get(f"/teams/{team_id}")
            if resp.status_code == 200:
                assert resp.json()["status"] == "deleted"
            else:
                assert resp.status_code == 404
        finally:
            integration_client.post(f"/teams/{team_id}/stop")


# ---------------------------------------------------------------------------
# AC #2 — RuntimeCache/TeamHandle lifecycle
# ---------------------------------------------------------------------------


class TestRuntimeCacheLifecycle:
    """Verify RuntimeCache store/get/remove through team lifecycle operations."""

    def test_cache_lifecycle_create_stop_restore_delete(
        self,
        integration_client: TestClient,
        integration_app: FastAPI,
    ) -> None:
        """AC #2: Full RuntimeCache lifecycle -- create, stop, restore, delete."""
        cache = integration_app.state.services.runtime_cache
        team_id_str = create_team(integration_client)
        try:
            team_id = uuid.UUID(team_id_str)
            assert cache.get(team_id) is not None

            resp = integration_client.post(f"/teams/{team_id_str}/stop")
            assert resp.status_code == 204
            assert cache.get(team_id) is None

            resp = integration_client.get(f"/teams/{team_id_str}/events")
            assert resp.status_code == 200
            events = resp.json()["events"]
            assert len(events) >= 1

            resp = integration_client.post(f"/teams/{team_id_str}/restore")
            assert resp.status_code == 200
            assert resp.json()["status"] == "running"
            assert cache.get(team_id) is not None

            resp = integration_client.post(f"/teams/{team_id_str}/stop")
            assert resp.status_code == 204
            resp = integration_client.delete(f"/teams/{team_id_str}")
            assert resp.status_code == 204
            assert cache.get(team_id) is None
        finally:
            integration_client.post(f"/teams/{team_id_str}/stop")


# ---------------------------------------------------------------------------
# AC #4 — WebSocket via TeamHandle
# ---------------------------------------------------------------------------


class TestWebSocketTeamHandle:
    """Verify WebSocket events flow through TeamHandle.subscribe/unsubscribe."""

    def test_ws_events_arrive_via_team_handle(
        self,
        integration_client: TestClient,
    ) -> None:
        """AC #4: Create team, open WS, verify events arrive via TeamHandle."""
        team_id = create_team(integration_client)
        try:
            with integration_client.websocket_connect(f"/ws/{team_id}") as ws:
                time.sleep(0.3)
                integration_client.post(
                    f"/teams/{team_id}/message",
                    json={"content": "Say hello"},
                )

                data = ws.receive_json(mode="text")
                assert isinstance(data, dict)
                assert "__model__" in data
                model = str(data.get("__model__", ""))
                assert "SentMessage" in model or "ReceivedMessage" in model, (
                    f"Expected SentMessage or ReceivedMessage, got: {model}"
                )
        finally:
            integration_client.post(f"/teams/{team_id}/stop")
            time.sleep(0.5)

    def test_ws_send_message_via_rest_receive_via_ws(
        self,
        integration_client: TestClient,
    ) -> None:
        """AC #4: Send message via REST, verify WS receives events, close cleanly."""
        team_id = create_team(integration_client)
        try:
            with integration_client.websocket_connect(f"/ws/{team_id}") as ws:
                time.sleep(0.3)
                integration_client.post(
                    f"/teams/{team_id}/message",
                    json={"content": "Say yes"},
                )

                data = ws.receive_json(mode="text")
                assert isinstance(data, dict)
                assert "__model__" in data

            resp = integration_client.get(f"/teams/{team_id}")
            assert resp.status_code == 200
        finally:
            integration_client.post(f"/teams/{team_id}/stop")
            time.sleep(0.5)
