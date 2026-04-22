"""Integration tests — full team lifecycle with real LLM."""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

pytestmark = [pytest.mark.integration, pytest.mark.llm]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

CATALOG_ENTRY_ID = "test-team"
POLL_INTERVAL_S = 1.0
POLL_TIMEOUT_S = 60.0


def _wait_for_llm_response(
    client: TestClient,
    team_id: str,
    timeout: float = POLL_TIMEOUT_S,
) -> list[dict[str, object]]:
    """Poll events until an LLM-generated response from @Manager appears."""
    deadline = time.monotonic() + timeout
    events: list[dict[str, object]] = []
    while time.monotonic() < deadline:
        resp = client.get(f"/teams/{team_id}/events")
        assert resp.status_code == 200
        events = resp.json()["events"]
        if _has_llm_content(events):
            return events
        time.sleep(POLL_INTERVAL_S)
    pytest.fail(
        f"Timed out after {timeout}s waiting for LLM response "
        f"(got {len(events)} events, none with LLM content)"
    )


def _has_llm_content(events: list[dict[str, object]]) -> bool:
    """Check if any event contains LLM-generated content from @Manager."""
    for ev_wrapper in events:
        ev = ev_wrapper["event"]
        if not isinstance(ev, dict):
            continue
        msg = ev.get("message")
        if not isinstance(msg, dict):
            continue
        content = msg.get("content")
        sender = ev.get("sender")
        if not isinstance(sender, dict):
            continue
        if isinstance(content, str) and len(content) > 0 and sender.get("name") == "@Manager":
            return True
    return False


def _create_team(client: TestClient) -> str:
    """POST /teams and return the team_id."""
    resp = client.post("/teams/", json={"catalog_entry_id": CATALOG_ENTRY_ID})
    assert resp.status_code == 201
    data = resp.json()
    assert data["status"] == "running"
    return data["team_id"]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestTeamLifecycle:
    """Integration tests exercising the full team lifecycle via HTTP."""

    def test_create_team_send_message_receive_response(
        self,
        integration_client: TestClient,
    ) -> None:
        """AC #1, #2: Create a team and verify it is running."""
        team_id = _create_team(integration_client)
        try:
            resp = integration_client.get(f"/teams/{team_id}")
            assert resp.status_code == 200
            assert resp.json()["status"] == "running"
        finally:
            integration_client.post(f"/teams/{team_id}/stop")

    def test_send_message_and_receive_llm_response(
        self,
        integration_client: TestClient,
    ) -> None:
        """AC #5 (partial): Send a message and verify LLM responds."""
        team_id = _create_team(integration_client)
        try:
            resp = integration_client.post(
                f"/teams/{team_id}/message",
                json={"content": "Say hello in exactly three words."},
            )
            assert resp.status_code == 204

            events = _wait_for_llm_response(integration_client, team_id)
            assert _has_llm_content(events)
        finally:
            integration_client.post(f"/teams/{team_id}/stop")

    def test_stop_and_restore_team(
        self,
        integration_client: TestClient,
    ) -> None:
        """AC #5 (partial): Stop and restore a team."""
        team_id = _create_team(integration_client)
        try:
            resp = integration_client.post(f"/teams/{team_id}/stop")
            assert resp.status_code == 204

            resp = integration_client.get(f"/teams/{team_id}")
            assert resp.status_code == 200
            assert resp.json()["status"] == "stopped"

            resp = integration_client.post(f"/teams/{team_id}/restore")
            assert resp.status_code == 200
            assert resp.json()["status"] == "running"
        finally:
            integration_client.post(f"/teams/{team_id}/stop")

    def test_events_persisted_after_stop(
        self,
        integration_client: TestClient,
    ) -> None:
        """AC #5 (partial): Events persist after stop."""
        team_id = _create_team(integration_client)
        try:
            integration_client.post(
                f"/teams/{team_id}/message",
                json={"content": "Respond with one word."},
            )
            _wait_for_llm_response(integration_client, team_id)

            resp = integration_client.post(f"/teams/{team_id}/stop")
            assert resp.status_code == 204

            resp = integration_client.get(f"/teams/{team_id}/events")
            assert resp.status_code == 200
            events = resp.json()["events"]
            assert len(events) >= 3
        finally:
            integration_client.post(f"/teams/{team_id}/stop")

    def test_full_lifecycle_round_trip(
        self,
        integration_client: TestClient,
    ) -> None:
        """AC #5: Full round-trip -- create, message, LLM, stop, restore."""
        team_id = _create_team(integration_client)
        try:
            resp = integration_client.post(
                f"/teams/{team_id}/message",
                json={"content": "What is 2 + 2? Answer with the number."},
            )
            assert resp.status_code == 204

            events = _wait_for_llm_response(integration_client, team_id)
            assert _has_llm_content(events)

            resp = integration_client.post(f"/teams/{team_id}/stop")
            assert resp.status_code == 204
            resp = integration_client.get(f"/teams/{team_id}")
            assert resp.json()["status"] == "stopped"

            resp = integration_client.post(f"/teams/{team_id}/restore")
            assert resp.status_code == 200
            assert resp.json()["status"] == "running"

            resp = integration_client.get(f"/teams/{team_id}/events")
            assert resp.status_code == 200
            final_events = resp.json()["events"]
            assert len(final_events) >= 3
            assert _has_llm_content(final_events)
        finally:
            integration_client.post(f"/teams/{team_id}/stop")
