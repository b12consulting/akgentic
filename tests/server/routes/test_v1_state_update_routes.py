"""Tests for V1 PATCH /state/{id}/of/{agent} endpoint."""

from __future__ import annotations

import uuid

from fastapi.testclient import TestClient


def test_v1_send_to_agent_success(v1_client: TestClient) -> None:
    """PATCH /state/{id}/of/{agent} on running team returns 200 with status ok."""
    create_resp = v1_client.post("/process/test-team")
    team_id = create_resp.json()["id"]
    resp = v1_client.patch(
        f"/state/{team_id}/of/@Manager",
        json={"content": "hello"},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_v1_send_to_agent_not_found_team(v1_client: TestClient) -> None:
    """PATCH /state/{id}/of/{agent} on non-existent team returns 404."""
    resp = v1_client.patch(
        f"/state/{uuid.uuid4()}/of/@Manager",
        json={"content": "hello"},
    )
    assert resp.status_code == 404


def test_v1_send_to_agent_unknown_agent(v1_client: TestClient) -> None:
    """PATCH /state/{id}/of/{agent} with unknown agent returns 404 (was 500 before fix)."""
    create_resp = v1_client.post("/process/test-team")
    team_id = create_resp.json()["id"]
    resp = v1_client.patch(
        f"/state/{team_id}/of/ghost",
        json={"content": "hello"},
    )
    assert resp.status_code == 404


def test_v1_send_to_agent_stopped_team(v1_client: TestClient) -> None:
    """PATCH /state/{id}/of/{agent} on stopped team returns 409."""
    create_resp = v1_client.post("/process/test-team")
    team_id = create_resp.json()["id"]
    v1_client.delete(f"/process/{team_id}/archive")
    resp = v1_client.patch(
        f"/state/{team_id}/of/@Manager",
        json={"content": "hello"},
    )
    assert resp.status_code == 409
