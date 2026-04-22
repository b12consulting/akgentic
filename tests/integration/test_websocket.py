"""Integration tests — WebSocket event streaming with real actors and real LLM."""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from ._helpers import create_team, has_llm_content, wait_for_llm_response

pytestmark = [pytest.mark.integration, pytest.mark.llm]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestWebSocketIntegration:
    """Integration tests for WebSocket event streaming (AC #1)."""

    def test_ws_create_team_send_message_receive_events(
        self,
        integration_client: TestClient,
    ) -> None:
        """AC #1: Create team, open WS, send message, verify events delivered via WS."""
        team_id = create_team(integration_client)
        try:
            with integration_client.websocket_connect(f"/ws/{team_id}") as ws:
                time.sleep(0.3)
                integration_client.post(
                    f"/teams/{team_id}/message",
                    json={"content": "Say hello"},
                )

                data = ws.receive_json(mode="text")
                assert isinstance(data, dict)
                assert "__model__" in data
                model = str(data.get("__model__", ""))
                assert "SentMessage" in model or "ReceivedMessage" in model, (
                    f"Expected SentMessage or ReceivedMessage, got: {model}"
                )
        finally:
            integration_client.post(f"/teams/{team_id}/stop")
            time.sleep(0.5)

    def test_ws_full_round_trip_with_llm(
        self,
        integration_client: TestClient,
    ) -> None:
        """AC #1: Send message via HTTP, verify LLM response arrives, open WS."""
        team_id = create_team(integration_client)
        try:
            integration_client.post(
                f"/teams/{team_id}/message",
                json={"content": "Reply with exactly one word."},
            )
            events = wait_for_llm_response(integration_client, team_id)
            assert has_llm_content(events)

            with integration_client.websocket_connect(f"/ws/{team_id}") as ws:
                time.sleep(0.3)
                integration_client.post(
                    f"/teams/{team_id}/message",
                    json={"content": "Say yes."},
                )
                data = ws.receive_json(mode="text")
                assert isinstance(data, dict)
                assert "__model__" in data

                model = str(data.get("__model__", ""))
                assert "SentMessage" in model or "ReceivedMessage" in model, (
                    f"Expected SentMessage or ReceivedMessage, got: {model}"
                )
        finally:
            integration_client.post(f"/teams/{team_id}/stop")
            time.sleep(0.5)

    def test_ws_restore_receives_events(
        self,
        integration_client: TestClient,
    ) -> None:
        """AC #1: Connect WS to stopped team, restore, verify events after restore."""
        team_id = create_team(integration_client)
        try:
            resp = integration_client.post(f"/teams/{team_id}/stop")
            assert resp.status_code == 204

            with integration_client.websocket_connect(f"/ws/{team_id}") as ws:
                integration_client.post(f"/teams/{team_id}/restore")
                time.sleep(0.5)

                integration_client.post(
                    f"/teams/{team_id}/message",
                    json={"content": "hello after restore"},
                )

                data = ws.receive_json(mode="text")
                assert isinstance(data, dict)
                assert "__model__" in data
        finally:
            integration_client.post(f"/teams/{team_id}/stop")
            time.sleep(0.5)
