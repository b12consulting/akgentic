"""Integration tests — event persistence via REST after team lifecycle operations."""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from ._helpers import create_team, has_llm_content, wait_for_llm_response

pytestmark = [pytest.mark.integration, pytest.mark.llm]


# ---------------------------------------------------------------------------
# Tests (AC #2)
# ---------------------------------------------------------------------------


class TestEventPersistence:
    """Integration tests for event persistence after team lifecycle operations."""

    def test_events_persisted_after_stop(
        self,
        integration_client: TestClient,
    ) -> None:
        """AC #2: Create team, send message, wait for LLM, stop, verify events via REST."""
        team_id = create_team(integration_client)
        try:
            integration_client.post(
                f"/teams/{team_id}/message",
                json={"content": "Reply with exactly one word."},
            )
            events = wait_for_llm_response(integration_client, team_id)
            assert has_llm_content(events)

            resp = integration_client.post(f"/teams/{team_id}/stop")
            assert resp.status_code == 204

            resp = integration_client.get(f"/teams/{team_id}/events")
            assert resp.status_code == 200
            stopped_events = resp.json()["events"]

            models = [
                ev["event"].get("__model__", "")
                for ev in stopped_events
                if isinstance(ev.get("event"), dict)
            ]
            has_sent = any("SentMessage" in str(m) for m in models)
            has_received = any("ReceivedMessage" in str(m) for m in models)
            assert has_sent or has_received, (
                f"Expected SentMessage or ReceivedMessage in persisted events, got: {models}"
            )
        finally:
            integration_client.post(f"/teams/{team_id}/stop")

    def test_event_count_after_send_and_respond(
        self,
        integration_client: TestClient,
    ) -> None:
        """AC #2: Verify event count >= 3 after a full send-and-respond cycle."""
        team_id = create_team(integration_client)
        try:
            integration_client.post(
                f"/teams/{team_id}/message",
                json={"content": "What is 1 + 1? Answer with the number."},
            )
            events = wait_for_llm_response(integration_client, team_id)
            assert len(events) >= 3, (
                f"Expected >= 3 events after send-and-respond cycle, got {len(events)}"
            )
        finally:
            integration_client.post(f"/teams/{team_id}/stop")
            time.sleep(0.5)

    def test_events_survive_stop_restore_cycle(
        self,
        integration_client: TestClient,
    ) -> None:
        """AC #2: Events from before stop are still accessible after restore."""
        team_id = create_team(integration_client)
        try:
            integration_client.post(
                f"/teams/{team_id}/message",
                json={"content": "Respond with one word."},
            )
            events_before = wait_for_llm_response(integration_client, team_id)
            count_before = len(events_before)
            assert count_before >= 3

            resp = integration_client.post(f"/teams/{team_id}/stop")
            assert resp.status_code == 204

            resp = integration_client.post(f"/teams/{team_id}/restore")
            assert resp.status_code == 200
            assert resp.json()["status"] == "running"

            resp = integration_client.get(f"/teams/{team_id}/events")
            assert resp.status_code == 200
            events_after = resp.json()["events"]
            assert len(events_after) >= count_before, (
                f"Events lost after stop/restore: had {count_before}, now {len(events_after)}"
            )
        finally:
            integration_client.post(f"/teams/{team_id}/stop")
            time.sleep(0.5)
