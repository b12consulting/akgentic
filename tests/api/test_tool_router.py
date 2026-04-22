"""Tests for the tool catalog API router."""

from __future__ import annotations

from fastapi.testclient import TestClient

from tests.conftest import make_tool


class TestToolRouter:
    """AC-2: Tool router CRUD and search endpoints."""

    def test_create_returns_201(self, client: TestClient) -> None:
        """POST /api/tools creates entry and returns 201."""
        entry = make_tool()
        resp = client.post("/api/tools/", json=entry.model_dump())
        assert resp.status_code == 201
        assert resp.json()["id"] == entry.id

    def test_list_returns_entries(self, client: TestClient) -> None:
        """GET /api/tools lists all entries."""
        client.post("/api/tools/", json=make_tool(id="s1").model_dump())
        client.post("/api/tools/", json=make_tool(id="s2").model_dump())
        resp = client.get("/api/tools/")
        assert resp.status_code == 200
        assert len(resp.json()) == 2

    def test_get_by_id(self, client: TestClient) -> None:
        """GET /api/tools/{id} returns entry."""
        entry = make_tool(id="s1")
        client.post("/api/tools/", json=entry.model_dump())
        resp = client.get("/api/tools/s1")
        assert resp.status_code == 200
        assert resp.json()["id"] == "s1"

    def test_get_nonexistent_returns_404(self, client: TestClient) -> None:
        """GET /api/tools/{id} returns 404 for missing entry."""
        resp = client.get("/api/tools/missing")
        assert resp.status_code == 404

    def test_search_with_query(self, client: TestClient) -> None:
        """POST /api/tools/search returns filtered results."""
        client.post("/api/tools/", json=make_tool(id="s1").model_dump())
        client.post("/api/tools/", json=make_tool(id="s2").model_dump())
        resp = client.post("/api/tools/search", json={"id": "s1"})
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["id"] == "s1"

    def test_search_empty_results(self, client: TestClient) -> None:
        """POST /api/tools/search returns empty array when no matches."""
        resp = client.post("/api/tools/search", json={"id": "nonexistent"})
        assert resp.status_code == 200
        assert resp.json() == []

    def test_update_entry(self, client: TestClient) -> None:
        """PUT /api/tools/{id} updates and returns updated entry."""
        client.post("/api/tools/", json=make_tool(id="s1").model_dump())
        updated = make_tool(id="s1", description="Updated description")
        resp = client.put("/api/tools/s1", json=updated.model_dump())
        assert resp.status_code == 200
        assert resp.json()["tool"]["description"] == "Updated description"

    def test_update_id_mismatch_returns_409(self, client: TestClient) -> None:
        """PUT /api/tools/{id} with mismatched body id returns 409."""
        client.post("/api/tools/", json=make_tool(id="s1").model_dump())
        mismatched = make_tool(id="s2")
        resp = client.put("/api/tools/s1", json=mismatched.model_dump())
        assert resp.status_code == 409

    def test_update_nonexistent_returns_404(self, client: TestClient) -> None:
        """PUT /api/tools/{id} returns 404 for missing entry."""
        entry = make_tool(id="missing")
        resp = client.put("/api/tools/missing", json=entry.model_dump())
        assert resp.status_code == 404

    def test_delete_returns_204(self, client: TestClient) -> None:
        """DELETE /api/tools/{id} returns 204."""
        client.post("/api/tools/", json=make_tool(id="s1").model_dump())
        resp = client.delete("/api/tools/s1")
        assert resp.status_code == 204

    def test_delete_nonexistent_returns_404(self, client: TestClient) -> None:
        """DELETE /api/tools/{id} returns 404 for missing entry."""
        resp = client.delete("/api/tools/missing")
        assert resp.status_code == 404

    def test_create_duplicate_returns_409(self, client: TestClient) -> None:
        """POST /api/tools with duplicate id returns 409."""
        entry = make_tool(id="s1")
        client.post("/api/tools/", json=entry.model_dump())
        resp = client.post("/api/tools/", json=entry.model_dump())
        assert resp.status_code == 409
        assert "errors" in resp.json()
