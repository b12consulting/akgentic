"""Tests for mounted akgentic-catalog routers at /catalog/api/<type>."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient


def test_catalog_team_router_mounted(client: TestClient) -> None:
    """GET /catalog/api/teams returns list of team entries."""
    resp = client.get("/catalog/api/teams")
    assert resp.status_code == 200
    body = resp.json()
    assert isinstance(body, list)
    assert len(body) >= 1
    ids = [t["id"] for t in body]
    assert "test-team" in ids


def test_catalog_agent_router_mounted(client: TestClient) -> None:
    """GET /catalog/api/agents returns list of agent entries."""
    resp = client.get("/catalog/api/agents")
    assert resp.status_code == 200
    body = resp.json()
    assert isinstance(body, list)
    assert len(body) >= 1
    ids = [a["id"] for a in body]
    assert "human-proxy" in ids


def test_catalog_template_router_mounted(client: TestClient) -> None:
    """GET /catalog/api/templates returns list (may be empty)."""
    resp = client.get("/catalog/api/templates")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


def test_catalog_tool_router_mounted(client: TestClient) -> None:
    """GET /catalog/api/tools returns list (may be empty)."""
    resp = client.get("/catalog/api/tools")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


def test_catalog_team_get_by_id(client: TestClient) -> None:
    """GET /catalog/api/teams/{id} returns specific team entry."""
    resp = client.get("/catalog/api/teams/test-team")
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == "test-team"
    assert body["name"] == "Test Team"
    assert body["entry_point"] == "human-proxy"
    assert isinstance(body["members"], list)
    assert len(body["members"]) == 2


def test_catalog_team_crud_create_and_delete(client: TestClient) -> None:
    """POST then DELETE /catalog/api/teams/{id} works through mounted routers."""
    entry = {
        "id": "crud-test-team",
        "name": "CRUD Test",
        "entry_point": "human-proxy",
        "message_types": ["akgentic.core.messages.UserMessage"],
        "members": [{"agent_id": "human-proxy"}],
    }
    # Create
    resp = client.post("/catalog/api/teams/", json=entry)
    assert resp.status_code == 201
    assert resp.json()["id"] == "crud-test-team"

    # Verify it exists
    resp = client.get("/catalog/api/teams/crud-test-team")
    assert resp.status_code == 200
    assert resp.json()["name"] == "CRUD Test"

    # List should include it
    resp = client.get("/catalog/api/teams")
    ids = [t["id"] for t in resp.json()]
    assert "crud-test-team" in ids

    # Delete
    resp = client.delete("/catalog/api/teams/crud-test-team")
    assert resp.status_code == 204

    # Verify entry is gone
    resp = client.get("/catalog/api/teams/crud-test-team")
    assert resp.status_code == 404


def test_catalog_exception_handler_404(client: TestClient) -> None:
    """GET /catalog/api/teams/{id} returns 404 for missing entry via exception handler."""
    resp = client.get("/catalog/api/teams/nonexistent-entry")
    assert resp.status_code == 404
    body = resp.json()
    assert "detail" in body
    assert "not found" in body["detail"].lower()


def test_catalog_exception_handler_409(client: TestClient) -> None:
    """POST duplicate entry returns 409 via CatalogValidationError exception handler."""
    entry = {
        "id": "test-team",
        "name": "Duplicate Team",
        "entry_point": "human-proxy",
        "message_types": ["akgentic.core.messages.UserMessage"],
        "members": [{"agent_id": "human-proxy"}],
    }
    resp = client.post("/catalog/api/teams/", json=entry)
    assert resp.status_code == 409
    body = resp.json()
    assert "detail" in body
    assert "already exists" in body["detail"].lower()


def test_catalog_exception_handler_registered(app: FastAPI) -> None:
    """Exception handlers for EntryNotFoundError and CatalogValidationError are registered."""
    from akgentic.catalog.models.errors import CatalogValidationError, EntryNotFoundError

    assert EntryNotFoundError in app.exception_handlers
    assert CatalogValidationError in app.exception_handlers


def test_old_catalog_routes_removed(client: TestClient) -> None:
    """Old hand-rolled /catalog/teams endpoint no longer exists."""
    resp = client.get("/catalog/teams")
    assert resp.status_code == 404 or resp.status_code == 405


def test_core_routes_coexist_with_catalog(client: TestClient) -> None:
    """Core app routes still work after mounting catalog routers."""
    resp = client.get("/teams/")
    assert resp.status_code == 200
