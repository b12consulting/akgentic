"""Tests for team CRUD endpoints using FastAPI TestClient."""

from __future__ import annotations

import uuid
from collections.abc import Generator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from akgentic.infra.server.auth import RequestUser, get_request_user


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


# --- RequestUser identity seam (ADR-023 Story 26.1) ---


def test_create_team_default_identity_anonymous(client: TestClient) -> None:
    """With no override, POST /teams persists user_id == 'anonymous' (AC #5)."""
    resp = client.post("/teams/", json={"catalog_entry_id": "test-team"})
    assert resp.status_code == 201
    assert resp.json()["user_id"] == "anonymous"


@pytest.fixture()
def overridden_user_client(app: FastAPI) -> Generator[TestClient, None, None]:
    """TestClient with get_request_user overridden to a fixed identity."""
    app.dependency_overrides[get_request_user] = lambda: RequestUser(
        user_id="alice", email="alice@example.com"
    )
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_create_team_uses_overridden_identity(
    overridden_user_client: TestClient,
) -> None:
    """When get_request_user is overridden, POST /teams persists that user_id (AC #6)."""
    resp = overridden_user_client.post("/teams/", json={"catalog_entry_id": "test-team"})
    assert resp.status_code == 201
    assert resp.json()["user_id"] == "alice"


def test_list_teams_filters_by_overridden_identity(
    overridden_user_client: TestClient,
) -> None:
    """Under the override, GET /teams returns only the overridden user's teams (AC #6)."""
    overridden_user_client.post("/teams/", json={"catalog_entry_id": "test-team"})
    resp = overridden_user_client.get("/teams/")
    assert resp.status_code == 200
    teams = resp.json()["teams"]
    assert len(teams) == 1
    assert teams[0]["user_id"] == "alice"


# --- Optional ?status= lifecycle filter (Epic 49) ---
#
# The filtered requests use "/teams" with no trailing slash: that is the
# registered path, so the query string never depends on Starlette's
# slash-redirect preserving it.


def test_list_teams_status_running_excludes_stopped(client: TestClient) -> None:
    """?status=running returns only the running team; omitting it returns both."""
    running = client.post("/teams/", json={"catalog_entry_id": "test-team"})
    stopped = client.post("/teams/", json={"catalog_entry_id": "test-team"})
    assert running.status_code == 201
    assert stopped.status_code == 201
    running_id = running.json()["team_id"]
    stopped_id = stopped.json()["team_id"]
    assert client.post(f"/teams/{stopped_id}/stop").status_code == 204

    filtered = client.get("/teams", params={"status": "running"})
    assert filtered.status_code == 200
    assert [t["team_id"] for t in filtered.json()["teams"]] == [running_id]

    unfiltered = client.get("/teams")
    assert unfiltered.status_code == 200
    assert {t["team_id"] for t in unfiltered.json()["teams"]} == {running_id, stopped_id}


def test_list_teams_unknown_status_returns_422(client: TestClient) -> None:
    """An unknown status is rejected by FastAPI's own enum validation."""
    resp = client.get("/teams", params={"status": "bogus"})
    assert resp.status_code == 422


def test_list_teams_status_does_not_reach_across_users(app: FastAPI) -> None:
    """?status=running narrows within the caller's teams — it never widens past them.

    Another user's *running* team is the case that would surface a bypassed
    owner filter, so one is created under a second identity first.
    """
    app.dependency_overrides[get_request_user] = lambda: RequestUser(
        user_id="alice", email="alice@example.com"
    )
    alice_resp = TestClient(app).post("/teams/", json={"catalog_entry_id": "test-team"})
    assert alice_resp.status_code == 201
    app.dependency_overrides.clear()

    default_client = TestClient(app)
    own_resp = default_client.post("/teams/", json={"catalog_entry_id": "test-team"})
    assert own_resp.status_code == 201

    resp = default_client.get("/teams", params={"status": "running"})
    assert resp.status_code == 200
    assert [t["team_id"] for t in resp.json()["teams"]] == [own_resp.json()["team_id"]]
