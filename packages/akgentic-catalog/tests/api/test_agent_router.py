"""Tests for the agent catalog API router."""

from __future__ import annotations

from fastapi.testclient import TestClient

from tests.conftest import make_agent


class TestAgentRouter:
    """AC-3: Agent router CRUD and search endpoints."""

    def test_create_returns_201(self, client: TestClient) -> None:
        """POST /api/agents creates entry and returns 201."""
        entry = make_agent(id="a1", name="alpha")
        resp = client.post("/api/agents/", json=entry.model_dump())
        assert resp.status_code == 201
        assert resp.json()["id"] == "a1"

    def test_list_returns_entries(self, client: TestClient) -> None:
        """GET /api/agents lists all entries."""
        client.post("/api/agents/", json=make_agent(id="a1", name="alpha").model_dump())
        client.post("/api/agents/", json=make_agent(id="a2", name="beta").model_dump())
        resp = client.get("/api/agents/")
        assert resp.status_code == 200
        assert len(resp.json()) == 2

    def test_get_by_id(self, client: TestClient) -> None:
        """GET /api/agents/{id} returns entry."""
        client.post("/api/agents/", json=make_agent(id="a1", name="alpha").model_dump())
        resp = client.get("/api/agents/a1")
        assert resp.status_code == 200
        assert resp.json()["id"] == "a1"

    def test_get_nonexistent_returns_404(self, client: TestClient) -> None:
        """GET /api/agents/{id} returns 404 for missing entry."""
        resp = client.get("/api/agents/missing")
        assert resp.status_code == 404

    def test_search_with_query(self, client: TestClient) -> None:
        """POST /api/agents/search returns filtered results."""
        client.post("/api/agents/", json=make_agent(id="a1", name="alpha").model_dump())
        client.post("/api/agents/", json=make_agent(id="a2", name="beta").model_dump())
        resp = client.post("/api/agents/search", json={"id": "a1"})
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["id"] == "a1"

    def test_search_empty_results(self, client: TestClient) -> None:
        """POST /api/agents/search returns empty array when no matches."""
        resp = client.post("/api/agents/search", json={"id": "nonexistent"})
        assert resp.status_code == 200
        assert resp.json() == []

    def test_update_entry(self, client: TestClient) -> None:
        """PUT /api/agents/{id} updates and returns updated entry."""
        client.post("/api/agents/", json=make_agent(id="a1", name="alpha").model_dump())
        updated = make_agent(id="a1", name="alpha-v2")
        resp = client.put("/api/agents/a1", json=updated.model_dump())
        assert resp.status_code == 200
        assert resp.json()["card"]["config"]["name"] == "alpha-v2"

    def test_update_id_mismatch_returns_409(self, client: TestClient) -> None:
        """PUT /api/agents/{id} with mismatched body id returns 409."""
        client.post("/api/agents/", json=make_agent(id="a1", name="alpha").model_dump())
        mismatched = make_agent(id="a2", name="alpha")
        resp = client.put("/api/agents/a1", json=mismatched.model_dump())
        assert resp.status_code == 409

    def test_update_nonexistent_returns_404(self, client: TestClient) -> None:
        """PUT /api/agents/{id} returns 404 for missing entry."""
        entry = make_agent(id="missing", name="ghost")
        resp = client.put("/api/agents/missing", json=entry.model_dump())
        assert resp.status_code == 404

    def test_delete_returns_204(self, client: TestClient) -> None:
        """DELETE /api/agents/{id} returns 204."""
        client.post("/api/agents/", json=make_agent(id="a1", name="alpha").model_dump())
        resp = client.delete("/api/agents/a1")
        assert resp.status_code == 204

    def test_delete_nonexistent_returns_404(self, client: TestClient) -> None:
        """DELETE /api/agents/{id} returns 404 for missing entry."""
        resp = client.delete("/api/agents/missing")
        assert resp.status_code == 404

    def test_create_duplicate_returns_409(self, client: TestClient) -> None:
        """POST /api/agents with duplicate id returns 409."""
        entry = make_agent(id="a1", name="alpha")
        client.post("/api/agents/", json=entry.model_dump())
        resp = client.post("/api/agents/", json=entry.model_dump())
        assert resp.status_code == 409
        assert "errors" in resp.json()

    def test_create_with_invalid_tool_ids_returns_409(self, client: TestClient) -> None:
        """POST /api/agents with invalid tool_ids returns 409."""
        entry = make_agent(id="a1", name="alpha", tool_ids=["nonexistent-tool"])
        resp = client.post("/api/agents/", json=entry.model_dump())
        assert resp.status_code == 409

    def test_create_with_invalid_routes_to_returns_409(self, client: TestClient) -> None:
        """POST /api/agents with invalid routes_to returns 409."""
        entry = make_agent(id="a1", name="alpha", routes_to=["nonexistent-agent"])
        resp = client.post("/api/agents/", json=entry.model_dump())
        assert resp.status_code == 409
