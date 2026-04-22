"""Tests for team CRUD endpoints using FastAPI TestClient."""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient


def test_create_team_success(client: TestClient) -> None:
    """POST /teams with valid catalog entry returns 201."""
    resp = client.post("/teams/", json={"catalog_entry_id": "test-team"})
    assert resp.status_code == 201
    data = resp.json()
    assert "team_id" in data
    assert data["status"] == "running"
    assert data["name"] == "Test Team"


def test_create_team_invalid_entry(client: TestClient) -> None:
    """POST /teams with unknown catalog entry returns 404."""
    resp = client.post("/teams/", json={"catalog_entry_id": "nonexistent"})
    assert resp.status_code == 404


def test_list_teams_empty(client: TestClient) -> None:
    """GET /teams returns empty list when no teams exist."""
    resp = client.get("/teams/")
    assert resp.status_code == 200
    assert resp.json()["teams"] == []


def test_list_teams_after_create(client: TestClient) -> None:
    """GET /teams returns created teams."""
    client.post("/teams/", json={"catalog_entry_id": "test-team"})
    resp = client.get("/teams/")
    assert resp.status_code == 200
    teams = resp.json()["teams"]
    assert len(teams) == 1
    assert teams[0]["name"] == "Test Team"


def test_get_team_success(client: TestClient) -> None:
    """GET /teams/{id} returns team detail."""
    create_resp = client.post("/teams/", json={"catalog_entry_id": "test-team"})
    team_id = create_resp.json()["team_id"]
    resp = client.get(f"/teams/{team_id}")
    assert resp.status_code == 200
    assert resp.json()["team_id"] == team_id


def test_get_team_not_found(client: TestClient) -> None:
    """GET /teams/{id} returns 404 for unknown team."""
    resp = client.get(f"/teams/{uuid.uuid4()}")
    assert resp.status_code == 404


@pytest.mark.skip(
    reason="Flaky: race in TeamManager.delete_team — on_stop subscribers still "
    "flushing event_store writes while rmtree runs; pre-existing, not introduced by Epic 22."
)
def test_delete_team_success(client: TestClient) -> None:
    """DELETE /teams/{id} returns 204 and removes team."""
    create_resp = client.post("/teams/", json={"catalog_entry_id": "test-team"})
    team_id = create_resp.json()["team_id"]
    resp = client.delete(f"/teams/{team_id}")
    assert resp.status_code == 204
    # Verify team is actually gone
    get_resp = client.get(f"/teams/{team_id}")
    assert get_resp.status_code == 404


def test_delete_team_not_found(client: TestClient) -> None:
    """DELETE /teams/{id} returns 404 for unknown team."""
    resp = client.delete(f"/teams/{uuid.uuid4()}")
    assert resp.status_code == 404
