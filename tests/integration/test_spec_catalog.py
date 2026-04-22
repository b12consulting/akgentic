"""Integration tests — catalog CRUD through mounted akgentic-catalog routers.

Validates catalog routers mounted at /catalog from story 6.9.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

pytestmark = [pytest.mark.integration, pytest.mark.llm]


# ---------------------------------------------------------------------------
# AC #6 — Catalog CRUD for teams
# ---------------------------------------------------------------------------


class TestCatalogTeamCrud:
    """Exercise the mounted akgentic-catalog team router CRUD operations."""

    def test_list_teams(self, integration_client: TestClient) -> None:
        """AC #6: GET /catalog/api/teams returns seeded entries."""
        resp = integration_client.get("/catalog/api/teams")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        ids = [t["id"] for t in data]
        assert "test-team" in ids

    def test_get_team(self, integration_client: TestClient) -> None:
        """AC #6: GET /catalog/api/teams/{id} returns full entry."""
        resp = integration_client.get("/catalog/api/teams/test-team")
        assert resp.status_code == 200
        body = resp.json()
        assert body["id"] == "test-team"
        assert body["name"] == "Integration Test Team"

    def test_create_update_delete_team(
        self,
        integration_client: TestClient,
    ) -> None:
        """AC #6: POST/PUT/DELETE /catalog/api/teams for full CRUD."""
        new_entry = {
            "id": "crud-test-team",
            "name": "CRUD Test Team",
            "entry_point": "human-proxy",
            "message_types": ["akgentic.agent.AgentMessage"],
            "members": [{"agent_id": "human-proxy"}],
            "profiles": [],
        }

        # Create
        resp = integration_client.post("/catalog/api/teams/", json=new_entry)
        assert resp.status_code == 201
        assert resp.json()["id"] == "crud-test-team"

        # Read back
        resp = integration_client.get("/catalog/api/teams/crud-test-team")
        assert resp.status_code == 200
        assert resp.json()["name"] == "CRUD Test Team"

        # Update
        new_entry["name"] = "Updated CRUD Team"
        resp = integration_client.put(
            "/catalog/api/teams/crud-test-team",
            json=new_entry,
        )
        assert resp.status_code == 200
        assert resp.json()["name"] == "Updated CRUD Team"

        # Delete
        resp = integration_client.delete("/catalog/api/teams/crud-test-team")
        assert resp.status_code == 204

    def test_get_nonexistent_team_404(
        self,
        integration_client: TestClient,
    ) -> None:
        """AC #6: GET /catalog/api/teams/nonexistent returns 404."""
        resp = integration_client.get("/catalog/api/teams/nonexistent-xyz")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# AC #6 — Catalog list+get for agents
# ---------------------------------------------------------------------------


class TestCatalogAgentCrud:
    """Exercise the mounted akgentic-catalog agent router."""

    def test_list_agents(self, integration_client: TestClient) -> None:
        """AC #6: GET /catalog/api/agents returns seeded entries."""
        resp = integration_client.get("/catalog/api/agents")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        ids = [a["id"] for a in data]
        assert "human-proxy" in ids
        assert "manager" in ids

    def test_get_agent(self, integration_client: TestClient) -> None:
        """AC #6: GET /catalog/api/agents/{id} returns entry."""
        resp = integration_client.get("/catalog/api/agents/manager")
        assert resp.status_code == 200
        assert resp.json()["id"] == "manager"

    def test_get_nonexistent_agent_404(
        self,
        integration_client: TestClient,
    ) -> None:
        """AC #6: GET /catalog/api/agents/nonexistent returns 404."""
        resp = integration_client.get("/catalog/api/agents/nonexistent-xyz")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# AC #6 — Catalog list+get for tools and templates
# ---------------------------------------------------------------------------


class TestCatalogToolsTemplates:
    """Exercise the mounted tool and template catalog routers."""

    def test_list_tools(self, integration_client: TestClient) -> None:
        """AC #6: GET /catalog/api/tools returns 200 (may be empty)."""
        resp = integration_client.get("/catalog/api/tools")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    def test_list_templates(self, integration_client: TestClient) -> None:
        """AC #6: GET /catalog/api/templates returns 200 (may be empty)."""
        resp = integration_client.get("/catalog/api/templates")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)
