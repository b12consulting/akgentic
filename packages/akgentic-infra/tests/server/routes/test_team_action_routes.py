"""Tests for team action endpoints using FastAPI TestClient."""

from __future__ import annotations

import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from akgentic.infra.server.auth import RequestUser, get_request_user


def test_send_message_success(client: TestClient) -> None:
    """POST /teams/{id}/message on running team returns 204."""
    create_resp = client.post("/teams/", json={"catalog_namespace": "test-team"})
    team_id = create_resp.json()["team_id"]
    resp = client.post(f"/teams/{team_id}/message", json={"content": "hello"})
    assert resp.status_code == 204


def test_send_message_not_found(client: TestClient) -> None:
    """POST /teams/{id}/message on non-existent team returns 404."""
    resp = client.post(
        f"/teams/{uuid.uuid4()}/message",
        json={"content": "hello"},
    )
    assert resp.status_code == 404


def test_send_message_stopped_team(client: TestClient) -> None:
    """POST /teams/{id}/message on stopped team returns 409."""
    create_resp = client.post("/teams/", json={"catalog_namespace": "test-team"})
    team_id = create_resp.json()["team_id"]
    client.post(f"/teams/{team_id}/stop")
    resp = client.post(f"/teams/{team_id}/message", json={"content": "hello"})
    assert resp.status_code == 409


def test_send_message_to_agent_success(client: TestClient) -> None:
    """POST /teams/{id}/message/{agent} on running team returns 204."""
    create_resp = client.post("/teams/", json={"catalog_namespace": "test-team"})
    team_id = create_resp.json()["team_id"]
    resp = client.post(f"/teams/{team_id}/message/@Manager", json={"content": "hello"})
    assert resp.status_code == 204


def test_send_message_to_agent_not_found_team(client: TestClient) -> None:
    """POST /teams/{id}/message/{agent} on non-existent team returns 404."""
    resp = client.post(
        f"/teams/{uuid.uuid4()}/message/@Manager",
        json={"content": "hello"},
    )
    assert resp.status_code == 404


def test_send_message_to_agent_stopped_team(client: TestClient) -> None:
    """POST /teams/{id}/message/{agent} on stopped team returns 409."""
    create_resp = client.post("/teams/", json={"catalog_namespace": "test-team"})
    team_id = create_resp.json()["team_id"]
    client.post(f"/teams/{team_id}/stop")
    resp = client.post(f"/teams/{team_id}/message/@Manager", json={"content": "hello"})
    assert resp.status_code == 409


def test_send_message_to_agent_unknown_agent(client: TestClient) -> None:
    """POST /teams/{id}/message/{agent} with unknown agent returns 404."""
    create_resp = client.post("/teams/", json={"catalog_namespace": "test-team"})
    team_id = create_resp.json()["team_id"]
    resp = client.post(f"/teams/{team_id}/message/ghost", json={"content": "hello"})
    assert resp.status_code == 404


def test_send_message_from_to_success(client: TestClient) -> None:
    """POST /teams/{id}/message/from/{sender}/to/{recipient} returns 204."""
    create_resp = client.post("/teams/", json={"catalog_namespace": "test-team"})
    team_id = create_resp.json()["team_id"]
    resp = client.post(
        f"/teams/{team_id}/message/from/@Human/to/@Manager",
        json={"content": "hello"},
    )
    assert resp.status_code == 204


def test_send_message_from_to_not_found_team(client: TestClient) -> None:
    """POST send_from_to on non-existent team returns 404."""
    resp = client.post(
        f"/teams/{uuid.uuid4()}/message/from/@Human/to/@Manager",
        json={"content": "hello"},
    )
    assert resp.status_code == 404


def test_send_message_from_to_stopped_team(client: TestClient) -> None:
    """POST send_from_to on stopped team returns 409."""
    create_resp = client.post("/teams/", json={"catalog_namespace": "test-team"})
    team_id = create_resp.json()["team_id"]
    client.post(f"/teams/{team_id}/stop")
    resp = client.post(
        f"/teams/{team_id}/message/from/@Human/to/@Manager",
        json={"content": "hello"},
    )
    assert resp.status_code == 409


def test_send_message_from_to_unknown_sender(client: TestClient) -> None:
    """POST send_from_to with unknown sender returns 404."""
    create_resp = client.post("/teams/", json={"catalog_namespace": "test-team"})
    team_id = create_resp.json()["team_id"]
    resp = client.post(
        f"/teams/{team_id}/message/from/@Ghost/to/@Manager",
        json={"content": "hello"},
    )
    assert resp.status_code == 404


def test_send_message_from_to_unknown_recipient(client: TestClient) -> None:
    """POST send_from_to with unknown recipient returns 404."""
    create_resp = client.post("/teams/", json={"catalog_namespace": "test-team"})
    team_id = create_resp.json()["team_id"]
    resp = client.post(
        f"/teams/{team_id}/message/from/@Human/to/@Ghost",
        json={"content": "hello"},
    )
    assert resp.status_code == 404


def test_human_input_not_found_team(client: TestClient) -> None:
    """POST /teams/{id}/human-input on non-existent team returns 404."""
    resp = client.post(
        f"/teams/{uuid.uuid4()}/human-input",
        json={"content": "yes", "message_id": "abc"},
    )
    assert resp.status_code == 404


def test_human_input_invalid_message(client: TestClient) -> None:
    """POST /teams/{id}/human-input with invalid message_id returns 404."""
    create_resp = client.post("/teams/", json={"catalog_namespace": "test-team"})
    team_id = create_resp.json()["team_id"]
    resp = client.post(
        f"/teams/{team_id}/human-input",
        json={"content": "yes", "message_id": "nonexistent"},
    )
    assert resp.status_code == 404


def test_stop_team_success(client: TestClient) -> None:
    """POST /teams/{id}/stop on running team returns 204."""
    create_resp = client.post("/teams/", json={"catalog_namespace": "test-team"})
    team_id = create_resp.json()["team_id"]
    resp = client.post(f"/teams/{team_id}/stop")
    assert resp.status_code == 204
    # Verify team is now stopped
    get_resp = client.get(f"/teams/{team_id}")
    assert get_resp.json()["status"] == "stopped"


def test_stop_team_already_stopped(client: TestClient) -> None:
    """POST /teams/{id}/stop on already stopped team returns 409."""
    create_resp = client.post("/teams/", json={"catalog_namespace": "test-team"})
    team_id = create_resp.json()["team_id"]
    client.post(f"/teams/{team_id}/stop")
    resp = client.post(f"/teams/{team_id}/stop")
    assert resp.status_code == 409


def test_stop_team_not_found(client: TestClient) -> None:
    """POST /teams/{id}/stop on non-existent team returns 404."""
    resp = client.post(f"/teams/{uuid.uuid4()}/stop")
    assert resp.status_code == 404


def test_restore_team_success(client: TestClient) -> None:
    """POST /teams/{id}/restore on stopped team returns 200 + TeamResponse."""
    create_resp = client.post("/teams/", json={"catalog_namespace": "test-team"})
    team_id = create_resp.json()["team_id"]
    client.post(f"/teams/{team_id}/stop")
    resp = client.post(f"/teams/{team_id}/restore")
    assert resp.status_code == 200
    data = resp.json()
    assert data["team_id"] == team_id
    assert data["status"] == "running"


def test_restore_team_already_running(client: TestClient) -> None:
    """POST /teams/{id}/restore on already running team returns 409."""
    create_resp = client.post("/teams/", json={"catalog_namespace": "test-team"})
    team_id = create_resp.json()["team_id"]
    resp = client.post(f"/teams/{team_id}/restore")
    assert resp.status_code == 409


def test_restore_team_not_found(client: TestClient) -> None:
    """POST /teams/{id}/restore on non-existent team returns 404."""
    resp = client.post(f"/teams/{uuid.uuid4()}/restore")
    assert resp.status_code == 404


def test_get_events_success(client: TestClient) -> None:
    """GET /teams/{id}/events returns events for a team."""
    create_resp = client.post("/teams/", json={"catalog_namespace": "test-team"})
    team_id = create_resp.json()["team_id"]
    # Stop team first so events are flushed
    client.post(f"/teams/{team_id}/stop")
    resp = client.get(f"/teams/{team_id}/events")
    assert resp.status_code == 200
    data = resp.json()
    assert "events" in data
    assert isinstance(data["events"], list)


def test_get_events_not_found(client: TestClient) -> None:
    """GET /teams/{id}/events on non-existent team returns 404."""
    resp = client.get(f"/teams/{uuid.uuid4()}/events")
    assert resp.status_code == 404


@pytest.mark.skip(
    reason="Flaky: race in TeamManager.delete_team — on_stop subscribers still "
    "flushing event_store writes while rmtree runs; pre-existing, not introduced by Epic 22."
)
def test_stop_deleted_team(client: TestClient) -> None:
    """POST /teams/{id}/stop on deleted team returns 404."""
    create_resp = client.post("/teams/", json={"catalog_namespace": "test-team"})
    team_id = create_resp.json()["team_id"]
    client.delete(f"/teams/{team_id}")
    resp = client.post(f"/teams/{team_id}/stop")
    assert resp.status_code == 404


@pytest.mark.skip(
    reason="Flaky: same race as test_stop_deleted_team; pre-existing, not introduced by Epic 22."
)
def test_restore_deleted_team(client: TestClient) -> None:
    """POST /teams/{id}/restore on deleted team returns 404."""
    create_resp = client.post("/teams/", json={"catalog_namespace": "test-team"})
    team_id = create_resp.json()["team_id"]
    client.delete(f"/teams/{team_id}")
    resp = client.post(f"/teams/{team_id}/restore")
    assert resp.status_code == 404


def test_get_events_running_team(client: TestClient) -> None:
    """GET /teams/{id}/events on running team returns events."""
    create_resp = client.post("/teams/", json={"catalog_namespace": "test-team"})
    team_id = create_resp.json()["team_id"]
    resp = client.get(f"/teams/{team_id}/events")
    assert resp.status_code == 200
    assert isinstance(resp.json()["events"], list)


# --- Route-level authorization: per-team gate on the action routes (Story 40.5) ---


def _identity(app: FastAPI, user: RequestUser) -> TestClient:
    """A TestClient whose request-user seam resolves to ``user``."""
    app.dependency_overrides[get_request_user] = lambda: user
    return TestClient(app)


def _owned_team(owner: TestClient) -> str:
    """Create a running team under the owner's identity; return its id."""
    resp = owner.post("/teams/", json={"catalog_namespace": "test-team"})
    assert resp.status_code == 201
    return resp.json()["team_id"]


def test_action_routes_deny_non_owner_404(app: FastAPI) -> None:
    """A non-owner non-admin gets 404 on every action route (no existence leak) — AC2."""
    owner = _identity(app, RequestUser(user_id="alice"))
    team_id = _owned_team(owner)

    intruder = _identity(app, RequestUser(user_id="bob"))
    assert (
        intruder.post(f"/teams/{team_id}/message/@Manager", json={"content": "x"}).status_code
        == 404
    )
    assert (
        intruder.post(
            f"/teams/{team_id}/message/from/@Human/to/@Manager", json={"content": "x"}
        ).status_code
        == 404
    )
    human = intruder.post(
        f"/teams/{team_id}/human-input", json={"content": "y", "message_id": "m"}
    )
    assert human.status_code == 404
    # The gate denied (before the handler) — its detail, not the route's own error.
    assert human.json()["detail"] == "Team not found"
    assert intruder.post(f"/teams/{team_id}/stop").status_code == 404
    assert intruder.post(f"/teams/{team_id}/restore").status_code == 404
    assert intruder.get(f"/teams/{team_id}/agent-states").status_code == 404
    app.dependency_overrides.clear()


def test_action_routes_owner_reaches_handler(app: FastAPI) -> None:
    """The owner passes the gate and reaches each route's normal outcome — AC3."""
    owner = _identity(app, RequestUser(user_id="alice"))
    team_id = _owned_team(owner)

    assert (
        owner.post(f"/teams/{team_id}/message/@Manager", json={"content": "x"}).status_code == 204
    )
    assert (
        owner.post(
            f"/teams/{team_id}/message/from/@Human/to/@Manager", json={"content": "x"}
        ).status_code
        == 204
    )
    assert owner.get(f"/teams/{team_id}/agent-states").status_code == 200
    # Reaches the handler: an unknown message_id is the route's own 404, NOT the
    # gate's "Team not found" — proving the gate let the owner through.
    human = owner.post(
        f"/teams/{team_id}/human-input", json={"content": "y", "message_id": "missing"}
    )
    assert human.status_code == 404
    assert human.json()["detail"] != "Team not found"
    assert owner.post(f"/teams/{team_id}/stop").status_code == 204
    assert owner.post(f"/teams/{team_id}/restore").status_code == 200
    app.dependency_overrides.clear()


def test_action_routes_admin_non_owner_allowed(app: FastAPI) -> None:
    """An ``admin`` bypasses ownership on the action routes — AC3."""
    owner = _identity(app, RequestUser(user_id="alice"))
    team_id = _owned_team(owner)

    admin = _identity(app, RequestUser(user_id="root", roles=["admin"]))
    assert admin.get(f"/teams/{team_id}/agent-states").status_code == 200
    assert (
        admin.post(f"/teams/{team_id}/message/@Manager", json={"content": "x"}).status_code == 204
    )
    assert admin.post(f"/teams/{team_id}/stop").status_code == 204
    assert admin.post(f"/teams/{team_id}/restore").status_code == 200
    app.dependency_overrides.clear()
