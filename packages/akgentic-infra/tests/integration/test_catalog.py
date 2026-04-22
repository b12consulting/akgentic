"""Integration tests — catalog browsing endpoints with real wired app."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

pytestmark = [pytest.mark.integration, pytest.mark.llm]

# ---------------------------------------------------------------------------
# Tests (AC #3)
# ---------------------------------------------------------------------------


class TestCatalogIntegration:
    """Integration tests for catalog browsing with real YAML catalog."""

    def test_list_teams_returns_seeded_entry(
        self,
        integration_client: TestClient,
    ) -> None:
        """AC #3: GET /catalog/api/teams returns at least the seeded test-team."""
        resp = integration_client.get("/catalog/api/teams")
        assert resp.status_code == 200
        body = resp.json()
        assert isinstance(body, list)
        ids = [t["id"] for t in body]
        assert "test-team" in ids, f"Expected 'test-team' in catalog, got: {ids}"

    def test_get_team_details(
        self,
        integration_client: TestClient,
    ) -> None:
        """AC #3: GET /catalog/api/teams/test-team returns full details."""
        resp = integration_client.get("/catalog/api/teams/test-team")
        assert resp.status_code == 200
        body = resp.json()
        assert body["id"] == "test-team"
        assert body["name"] == "Integration Test Team"
        assert body["entry_point"] == "human-proxy"
        assert isinstance(body["members"], list)
        assert len(body["members"]) == 2
        assert isinstance(body["profiles"], list)

    def test_get_nonexistent_team_returns_404(
        self,
        integration_client: TestClient,
    ) -> None:
        """AC #3: GET /catalog/api/teams/nonexistent returns 404."""
        resp = integration_client.get("/catalog/api/teams/nonexistent")
        assert resp.status_code == 404
        assert "not found" in resp.json()["detail"].lower()
