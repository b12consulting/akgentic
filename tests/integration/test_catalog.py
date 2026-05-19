"""Integration tests — v2 unified /admin/catalog endpoints with real wired app."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

pytestmark = [pytest.mark.integration, pytest.mark.llm]


class TestCatalogIntegration:
    """Integration tests for catalog browsing with the v2 YAML-backed catalog."""

    def test_list_teams_returns_seeded_entry(
        self,
        integration_client: TestClient,
    ) -> None:
        """AC #3: GET /admin/catalog/team returns at least the seeded team entry."""
        resp = integration_client.get(
            "/admin/catalog/team", params={"namespace": "test-team"}
        )
        assert resp.status_code == 200
        body = resp.json()
        assert isinstance(body, list)
        ids = [t["id"] for t in body]
        assert "team" in ids, f"Expected 'team' in catalog, got: {ids}"

    def test_resolve_team_details(
        self,
        integration_client: TestClient,
    ) -> None:
        """AC #3: GET /admin/catalog/team/test-team/resolve returns a TeamCard."""
        resp = integration_client.get("/admin/catalog/team/test-team/resolve")
        assert resp.status_code == 200
        body = resp.json()
        assert body["name"] == "Integration Test Team"
        assert isinstance(body.get("members"), list)

    def test_get_nonexistent_team_returns_404(
        self,
        integration_client: TestClient,
    ) -> None:
        """AC #3: GET /admin/catalog/team/nonexistent returns 404."""
        resp = integration_client.get(
            "/admin/catalog/team/nonexistent", params={"namespace": "test-team"}
        )
        assert resp.status_code == 404
        assert "not found" in resp.json()["detail"].lower()
