"""Integration tests — V1 frontend adapter spec compliance for story 6.8 endpoints.

Validates all V1 endpoints added in story 6.8.

Note: TestLlmContextEnvelope was reclassified as a unit test and moved to
tests/frontend_adapter/test_v1_ws_classify.py (story 9.4).
"""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

pytestmark = [pytest.mark.integration, pytest.mark.llm]

CATALOG_ENTRY_ID = "test-team"


def _create_v1_team(client: TestClient) -> str:
    """POST /process/{type} (V1) and return the team_id."""
    resp = client.post(f"/process/{CATALOG_ENTRY_ID}")
    assert resp.status_code == 200
    data = resp.json()
    team_id: str = data["id"]
    return team_id


# ---------------------------------------------------------------------------
# AC #5 — V1 story-6.8 endpoint translations
# ---------------------------------------------------------------------------


class TestV1Story68Endpoints:
    """Integration tests for V1 endpoints added in story 6.8."""

    def test_patch_description(self, v1_adapter_client: TestClient) -> None:
        """AC #5: PATCH /process/{id}/description returns ok."""
        team_id = _create_v1_team(v1_adapter_client)
        try:
            resp = v1_adapter_client.patch(
                f"/process/{team_id}/description",
                json={"description": "Updated description"},
            )
            assert resp.status_code == 200
            assert resp.json()["status"] == "ok"
        finally:
            v1_adapter_client.delete(f"/process/{team_id}/archive")
            time.sleep(0.5)

    def test_relaunch_message_not_found(self, v1_adapter_client: TestClient) -> None:
        """AC #5: POST /relaunch/{id}/message/{msgId} returns 404 for unknown msg."""
        team_id = _create_v1_team(v1_adapter_client)
        try:
            resp = v1_adapter_client.post(
                f"/relaunch/{team_id}/message/00000000-0000-0000-0000-000000000000",
            )
            assert resp.status_code == 404
            assert "not found" in resp.json()["detail"].lower()
        finally:
            v1_adapter_client.delete(f"/process/{team_id}/archive")
            time.sleep(0.5)

    def test_patch_state_not_running(self, v1_adapter_client: TestClient) -> None:
        """AC #5: PATCH /state/{id}/of/{agent} returns 404 for stopped team."""
        team_id = _create_v1_team(v1_adapter_client)
        try:
            # Stop the team first
            v1_adapter_client.delete(f"/process/{team_id}/archive")
            time.sleep(0.5)

            resp = v1_adapter_client.patch(
                f"/state/{team_id}/of/@Manager",
                json={"content": "state update"},
            )
            assert resp.status_code == 404
        finally:
            # Already archived above; no additional cleanup needed
            pass

    def test_get_config_team(self, v1_adapter_client: TestClient) -> None:
        """AC #5: GET /config/team returns catalog entries."""
        resp = v1_adapter_client.get("/config/team")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        assert len(data) >= 1
        entry = data[0]
        assert "id" in entry
        assert "type" in entry
        assert entry["type"] == "team"
        assert "data" in entry

    def test_get_config_agent(self, v1_adapter_client: TestClient) -> None:
        """AC #5: GET /config/agent returns catalog entries."""
        resp = v1_adapter_client.get("/config/agent")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        assert len(data) >= 1

    def test_get_config_unknown_type(self, v1_adapter_client: TestClient) -> None:
        """AC #5: GET /config/unknown returns 400."""
        resp = v1_adapter_client.get("/config/unknown")
        assert resp.status_code == 400

    def test_get_team_configs(self, v1_adapter_client: TestClient) -> None:
        """AC #5: GET /team-configs returns team catalog entries as dict."""
        resp = v1_adapter_client.get("/team-configs/")
        assert resp.status_code == 200
        data = resp.json()
        # ADR-004 (8.2) changed response from list to dict keyed by team name
        assert isinstance(data, dict)
        assert len(data) >= 1
        for _name, entry in data.items():
            assert "module" in entry
            assert "setup" in entry

    def test_get_feedback_empty(self, v1_adapter_client: TestClient) -> None:
        """AC #5: GET /get-feedback returns empty list (stub)."""
        resp = v1_adapter_client.get("/get-feedback")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_set_feedback_ok(self, v1_adapter_client: TestClient) -> None:
        """AC #5: POST /set-feedback returns ok (stub)."""
        resp = v1_adapter_client.post(
            "/set-feedback",
            json={"id": "fb-1", "content": "Great!", "rating": 5},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    def test_put_config_create_and_update(self, v1_adapter_client: TestClient) -> None:
        """AC #5: PUT /config/{config_type} creates a new entry and updates."""
        # Get an existing entry to know the data shape
        resp = v1_adapter_client.get("/config/team")
        assert resp.status_code == 200
        existing = resp.json()[0]

        # Create a new entry via PUT /config/{config_type} (ADR-004 path shape)
        config: dict[str, object] = {
            **existing["data"],
            "id": "v1-put-test",
            "name": "V1 PUT Test",
        }
        put_body: dict[str, object] = {
            "id": "v1-put-test",
            "name": "V1 PUT Test",
            "config": config,
            "dry_run": False,
        }
        resp = v1_adapter_client.put("/config/team", json=put_body)
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

        # Update the entry via PUT
        config["name"] = "V1 PUT Updated"
        resp = v1_adapter_client.put("/config/team", json=put_body)
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

        # Clean up via DELETE /config/{config_type}/{config_id} (ADR-004 path shape)
        resp = v1_adapter_client.delete("/config/team/v1-put-test")
        assert resp.status_code == 200

    def test_delete_config_not_found(self, v1_adapter_client: TestClient) -> None:
        """AC #5: DELETE /config/{config_type}/{config_id} for nonexistent returns 404."""
        # ADR-004 path shape: DELETE /config/{config_type}/{config_id}
        resp = v1_adapter_client.delete("/config/team/nonexistent")
        assert resp.status_code == 404
