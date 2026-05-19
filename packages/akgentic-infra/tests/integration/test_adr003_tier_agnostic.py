"""Integration tests — ADR-003: tier-agnostic refactoring.

Validates stories 7.1 (WorkerHandle, PlacementStrategy) and 7.2
(TierServices, ServerSettings, TeamService, create_app) end-to-end.

Note: TestSettingsHierarchy and test_team_service_has_no_actor_internal_imports
were reclassified as unit tests (story 9.4).
"""

from __future__ import annotations

import time
import uuid
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from akgentic.infra.protocols.placement import PlacementStrategy
from akgentic.infra.protocols.runtime_cache import RuntimeCache
from akgentic.infra.protocols.team_handle import TeamHandle
from akgentic.infra.protocols.worker_handle import WorkerHandle
from akgentic.infra.server.app import create_app
from akgentic.infra.server.deps import CommunityServices, TierServices
from akgentic.infra.server.settings import CommunitySettings
from akgentic.infra.wiring import wire_community

from ._helpers import create_team, poll_until

pytestmark = [pytest.mark.integration, pytest.mark.llm]


# ---------------------------------------------------------------------------
# AC #2 — Tier-agnostic fixture pattern
# ---------------------------------------------------------------------------


class TestTierAgnosticFixturePattern:
    """Verify the fixture pattern: CommunitySettings -> wire_community -> create_app."""

    def test_wire_community_returns_community_services(
        self,
        integration_settings: CommunitySettings,
    ) -> None:
        """AC #2: wire_community returns CommunityServices with all TierServices fields."""
        services = wire_community(integration_settings)
        try:
            assert isinstance(services, CommunityServices)
            assert isinstance(services.placement, PlacementStrategy)
            assert isinstance(services.worker_handle, WorkerHandle)
            assert isinstance(services.runtime_cache, RuntimeCache)
            assert services.catalog is not None
        finally:
            services.actor_system.shutdown()

    def test_create_app_accepts_tier_services(
        self,
        integration_services: CommunityServices,
        integration_settings: CommunitySettings,
    ) -> None:
        """AC #2: create_app(services, settings) produces a working FastAPI app."""
        app = create_app(integration_services, integration_settings)
        assert isinstance(app, FastAPI)
        client = TestClient(app)
        resp = client.get("/teams/")
        assert resp.status_code == 200

    def test_inline_fixture_pattern(self, tmp_path: Path) -> None:
        """AC #2: Full inline pattern in one test."""
        from ._helpers import seed_integration_catalog

        settings = CommunitySettings(
            workspaces_root=tmp_path / "workspaces",
            event_store_path=tmp_path / "event_store",
            catalog_path=tmp_path / "catalog",
        )
        seed_integration_catalog(settings.catalog_path)
        services = wire_community(settings)
        try:
            app = create_app(services, settings)
            client = TestClient(app)

            resp = client.get("/teams/")
            assert resp.status_code == 200
            assert "teams" in resp.json()
        finally:
            services.actor_system.shutdown()


# ---------------------------------------------------------------------------
# AC #1 — WorkerHandle and PlacementStrategy lifecycle
# ---------------------------------------------------------------------------


class TestWorkerHandleLifecycle:
    """Verify WorkerHandle lifecycle operations end-to-end."""

    def test_create_team_via_placement_strategy(
        self,
        integration_client: TestClient,
        integration_app: FastAPI,
    ) -> None:
        """AC #1: POST /teams/ creates team; cache populated."""
        team_id_str = create_team(integration_client)
        try:
            team_id = uuid.UUID(team_id_str)
            cache = integration_app.state.services.runtime_cache
            assert cache.get(team_id) is not None
        finally:
            integration_client.post(f"/teams/{team_id_str}/stop")
            # No pollable condition for teardown; sleep for actor cleanup
            time.sleep(0.5)

    def test_stop_team_via_worker_handle(
        self,
        integration_client: TestClient,
        integration_app: FastAPI,
    ) -> None:
        """AC #1: Stop team clears cache; team status reflects stopped."""
        team_id_str = create_team(integration_client)
        try:
            team_id = uuid.UUID(team_id_str)
            cache = integration_app.state.services.runtime_cache

            resp = integration_client.post(f"/teams/{team_id_str}/stop")
            assert resp.status_code == 204
            assert cache.get(team_id) is None

            resp = integration_client.get(f"/teams/{team_id_str}")
            assert resp.status_code == 200
            assert resp.json()["status"] == "stopped"
        finally:
            integration_client.post(f"/teams/{team_id_str}/stop")

    def test_resume_team_via_worker_handle(
        self,
        integration_client: TestClient,
        integration_app: FastAPI,
    ) -> None:
        """AC #1: Stop then restore repopulates cache; status is running."""
        team_id_str = create_team(integration_client)
        try:
            team_id = uuid.UUID(team_id_str)
            cache = integration_app.state.services.runtime_cache

            integration_client.post(f"/teams/{team_id_str}/stop")
            poll_until(
                lambda: cache.get(team_id) is None,
                message="Cache not cleared after stop",
            )

            resp = integration_client.post(f"/teams/{team_id_str}/restore")
            assert resp.status_code == 200
            assert resp.json()["status"] == "running"
            assert cache.get(team_id) is not None
        finally:
            integration_client.post(f"/teams/{team_id_str}/stop")
            # No pollable condition for final teardown; sleep for actor cleanup
            time.sleep(0.5)

    def test_delete_team_via_worker_handle(
        self,
        integration_client: TestClient,
        integration_app: FastAPI,
    ) -> None:
        """AC #1: Create, stop, delete -> team removed."""
        team_id_str = create_team(integration_client)
        try:
            team_id = uuid.UUID(team_id_str)
            cache = integration_app.state.services.runtime_cache

            integration_client.post(f"/teams/{team_id_str}/stop")
            poll_until(
                lambda: cache.get(team_id) is None,
                message="Cache not cleared after stop",
            )

            resp = integration_client.delete(f"/teams/{team_id_str}")
            assert resp.status_code == 204
            assert cache.get(team_id) is None

            resp = integration_client.get(f"/teams/{team_id_str}")
            if resp.status_code == 200:
                assert resp.json()["status"] == "deleted"
            else:
                assert resp.status_code == 404
        finally:
            integration_client.post(f"/teams/{team_id_str}/stop")

    def test_full_worker_lifecycle(
        self,
        integration_client: TestClient,
        integration_app: FastAPI,
    ) -> None:
        """AC #1: create -> stop -> resume -> stop -> delete in one test."""
        cache = integration_app.state.services.runtime_cache
        team_id_str = create_team(integration_client)
        try:
            team_id = uuid.UUID(team_id_str)
            assert cache.get(team_id) is not None

            # Stop
            resp = integration_client.post(f"/teams/{team_id_str}/stop")
            assert resp.status_code == 204
            assert cache.get(team_id) is None

            # Resume
            poll_until(
                lambda: cache.get(team_id) is None,
                message="Cache not cleared after stop",
            )
            resp = integration_client.post(f"/teams/{team_id_str}/restore")
            assert resp.status_code == 200
            assert cache.get(team_id) is not None

            # Stop again
            resp = integration_client.post(f"/teams/{team_id_str}/stop")
            assert resp.status_code == 204
            assert cache.get(team_id) is None

            # Delete
            poll_until(
                lambda: cache.get(team_id) is None,
                message="Cache not cleared after second stop",
            )
            resp = integration_client.delete(f"/teams/{team_id_str}")
            assert resp.status_code == 204
            assert cache.get(team_id) is None

            resp = integration_client.get(f"/teams/{team_id_str}")
            if resp.status_code == 200:
                assert resp.json()["status"] == "deleted"
            else:
                assert resp.status_code == 404
        finally:
            integration_client.post(f"/teams/{team_id_str}/stop")


# ---------------------------------------------------------------------------
# AC #3 — TeamService uses only protocols
# ---------------------------------------------------------------------------


class TestTeamServiceProtocolRouting:
    """Verify TeamService routes operations through protocols."""

    def test_create_team_routes_through_placement(
        self,
        integration_client: TestClient,
        integration_app: FastAPI,
    ) -> None:
        """AC #3: Creating a team produces a handle in the runtime cache."""
        team_id_str = create_team(integration_client)
        try:
            team_id = uuid.UUID(team_id_str)
            cache = integration_app.state.services.runtime_cache
            handle = cache.get(team_id)
            assert handle is not None
            assert isinstance(handle, TeamHandle)
        finally:
            integration_client.post(f"/teams/{team_id_str}/stop")
            time.sleep(0.5)

    def test_send_message_routes_through_team_handle(
        self,
        integration_client: TestClient,
    ) -> None:
        """AC #3: Send message via API; events appear."""
        team_id = create_team(integration_client)
        try:
            resp = integration_client.post(
                f"/teams/{team_id}/message",
                json={"content": "Hello from integration test"},
            )
            assert resp.status_code == 204

            # SentMessage event should appear immediately
            time.sleep(0.5)
            resp = integration_client.get(f"/teams/{team_id}/events")
            assert resp.status_code == 200
            events = resp.json()["events"]
            assert len(events) >= 1
            event_strs = [str(ev) for ev in events]
            assert any("Hello from integration test" in s for s in event_strs), (
                f"Expected sent message content in events, got: {events}"
            )
        finally:
            integration_client.post(f"/teams/{team_id}/stop")
            time.sleep(0.5)

    def test_stop_routes_through_worker_handle(
        self,
        integration_client: TestClient,
    ) -> None:
        """AC #3: Stop preserves events in event store."""
        team_id = create_team(integration_client)
        try:
            integration_client.post(
                f"/teams/{team_id}/message",
                json={"content": "Test message"},
            )
            time.sleep(0.5)

            resp = integration_client.post(f"/teams/{team_id}/stop")
            assert resp.status_code == 204

            resp = integration_client.get(f"/teams/{team_id}/events")
            assert resp.status_code == 200
            events = resp.json()["events"]
            assert len(events) >= 1
        finally:
            integration_client.post(f"/teams/{team_id}/stop")

    def test_restore_routes_through_worker_handle(
        self,
        integration_client: TestClient,
        integration_app: FastAPI,
    ) -> None:
        """AC #3: Create, stop, restore -> team running, cache handle is TeamHandle."""
        team_id_str = create_team(integration_client)
        try:
            team_id = uuid.UUID(team_id_str)

            integration_client.post(f"/teams/{team_id_str}/stop")
            time.sleep(0.5)

            resp = integration_client.post(f"/teams/{team_id_str}/restore")
            assert resp.status_code == 200
            assert resp.json()["status"] == "running"

            cache = integration_app.state.services.runtime_cache
            handle = cache.get(team_id)
            assert handle is not None
            assert isinstance(handle, TeamHandle)
        finally:
            integration_client.post(f"/teams/{team_id_str}/stop")
            time.sleep(0.5)


# ---------------------------------------------------------------------------
# AC #2, #3 — Catalog access via TierServices
# ---------------------------------------------------------------------------


class TestCatalogAccessViaTierServices:
    """Verify catalogs are accessible through TierServices."""

    def test_catalogs_on_tier_services(self) -> None:
        """AC #2: TierServices model exposes the unified catalog field."""
        tier_fields = set(TierServices.model_fields.keys())
        assert "catalog" in tier_fields, "TierServices missing catalog"

    def test_catalogs_accessible_through_services(
        self,
        integration_services: CommunityServices,
    ) -> None:
        """AC #2: Wired CommunityServices has a non-None unified catalog."""
        assert integration_services.catalog is not None

    def test_team_creation_uses_catalog_from_tier_services(
        self,
        integration_client: TestClient,
    ) -> None:
        """AC #3: Team created via API uses catalog resolution."""
        team_id = create_team(integration_client)
        try:
            resp = integration_client.get(f"/teams/{team_id}")
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "running"
        finally:
            integration_client.post(f"/teams/{team_id}/stop")
            time.sleep(0.5)
