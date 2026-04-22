"""Tests for the template catalog API router."""

from __future__ import annotations

from fastapi.testclient import TestClient

from tests.conftest import make_template


class TestTemplateRouter:
    """AC-1: Template router CRUD and search endpoints."""

    def test_create_returns_201(self, client: TestClient) -> None:
        """POST /api/templates creates entry and returns 201."""
        entry = make_template()
        resp = client.post("/api/templates/", json=entry.model_dump())
        assert resp.status_code == 201
        assert resp.json()["id"] == entry.id

    def test_list_returns_entries(self, client: TestClient) -> None:
        """GET /api/templates lists all entries."""
        client.post("/api/templates/", json=make_template(id="t1").model_dump())
        client.post("/api/templates/", json=make_template(id="t2").model_dump())
        resp = client.get("/api/templates/")
        assert resp.status_code == 200
        assert len(resp.json()) == 2

    def test_get_by_id(self, client: TestClient) -> None:
        """GET /api/templates/{id} returns entry."""
        entry = make_template(id="t1")
        client.post("/api/templates/", json=entry.model_dump())
        resp = client.get("/api/templates/t1")
        assert resp.status_code == 200
        assert resp.json()["id"] == "t1"

    def test_get_nonexistent_returns_404(self, client: TestClient) -> None:
        """GET /api/templates/{id} returns 404 for missing entry."""
        resp = client.get("/api/templates/missing")
        assert resp.status_code == 404

    def test_search_with_query(self, client: TestClient) -> None:
        """POST /api/templates/search returns filtered results."""
        client.post("/api/templates/", json=make_template(id="t1").model_dump())
        client.post("/api/templates/", json=make_template(id="t2").model_dump())
        resp = client.post("/api/templates/search", json={"id": "t1"})
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["id"] == "t1"

    def test_search_empty_results(self, client: TestClient) -> None:
        """POST /api/templates/search returns empty array when no matches."""
        resp = client.post("/api/templates/search", json={"id": "nonexistent"})
        assert resp.status_code == 200
        assert resp.json() == []

    def test_update_entry(self, client: TestClient) -> None:
        """PUT /api/templates/{id} updates and returns updated entry."""
        client.post("/api/templates/", json=make_template(id="t1", template="old").model_dump())
        updated = make_template(id="t1", template="new {role}")
        resp = client.put("/api/templates/t1", json=updated.model_dump())
        assert resp.status_code == 200
        assert resp.json()["template"] == "new {role}"

    def test_update_id_mismatch_returns_409(self, client: TestClient) -> None:
        """PUT /api/templates/{id} with mismatched body id returns 409."""
        client.post("/api/templates/", json=make_template(id="t1").model_dump())
        mismatched = make_template(id="t2")
        resp = client.put("/api/templates/t1", json=mismatched.model_dump())
        assert resp.status_code == 409

    def test_update_nonexistent_returns_404(self, client: TestClient) -> None:
        """PUT /api/templates/{id} returns 404 for missing entry."""
        entry = make_template(id="missing")
        resp = client.put("/api/templates/missing", json=entry.model_dump())
        assert resp.status_code == 404

    def test_delete_returns_204(self, client: TestClient) -> None:
        """DELETE /api/templates/{id} returns 204."""
        client.post("/api/templates/", json=make_template(id="t1").model_dump())
        resp = client.delete("/api/templates/t1")
        assert resp.status_code == 204

    def test_delete_nonexistent_returns_404(self, client: TestClient) -> None:
        """DELETE /api/templates/{id} returns 404 for missing entry."""
        resp = client.delete("/api/templates/missing")
        assert resp.status_code == 404

    def test_create_duplicate_returns_409(self, client: TestClient) -> None:
        """POST /api/templates with duplicate id returns 409."""
        entry = make_template(id="t1")
        client.post("/api/templates/", json=entry.model_dump())
        resp = client.post("/api/templates/", json=entry.model_dump())
        assert resp.status_code == 409
        assert "errors" in resp.json()
