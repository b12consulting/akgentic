"""Integration tests — V1 frontend adapter with real actors and real LLM."""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

pytestmark = [pytest.mark.integration, pytest.mark.llm]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

CATALOG_ENTRY_ID = "test-team"
POLL_INTERVAL_S = 1.0
POLL_TIMEOUT_S = 60.0


def _create_v1_team(client: TestClient) -> str:
    """POST /process/{type} (V1) and return the team_id."""
    resp = client.post(f"/process/{CATALOG_ENTRY_ID}")
    assert resp.status_code == 200
    data = resp.json()
    assert "id" in data
    assert "status" in data
    assert "params" in data
    team_id: str = data["id"]
    return team_id


def _wait_for_v1_messages(
    client: TestClient,
    team_id: str,
    timeout: float = POLL_TIMEOUT_S,
) -> list[dict[str, object]]:
    """Poll GET /messages/{id} until an LLM response from @Manager appears."""
    deadline = time.monotonic() + timeout
    messages: list[dict[str, object]] = []
    while time.monotonic() < deadline:
        resp = client.get(f"/messages/{team_id}")
        assert resp.status_code == 200
        messages = resp.json()
        if _has_v1_llm_content(messages):
            return messages
        time.sleep(POLL_INTERVAL_S)
    pytest.fail(
        f"Timed out after {timeout}s waiting for V1 LLM response "
        f"(got {len(messages)} messages, none with LLM content)"
    )


def _has_v1_llm_content(messages: list[dict[str, object]]) -> bool:
    """Check if any V1 message entry contains LLM content from @Manager."""
    for msg in messages:
        sender = msg.get("sender")
        content = msg.get("content")
        if (
            isinstance(sender, str)
            and "@Manager" in sender
            and isinstance(content, str)
            and len(content) > 0
        ):
            return True
    return False


# ---------------------------------------------------------------------------
# Tests — V1 REST round-trip
# ---------------------------------------------------------------------------


class TestV1FrontendAdapter:
    """Integration tests for V1 frontend adapter REST endpoints."""

    def test_create_team_v1(self, v1_adapter_client: TestClient) -> None:
        """AC #1: POST /process/{type} returns V1ProcessContext shape."""
        resp = v1_adapter_client.post(f"/process/{CATALOG_ENTRY_ID}")
        assert resp.status_code == 200
        data = resp.json()

        try:
            assert "id" in data
            assert "status" in data
            assert "params" in data
            assert "type" in data
            assert "created_at" in data
            assert "updated_at" in data
            assert isinstance(data["id"], str)
            assert isinstance(data["status"], str)
            assert isinstance(data["params"], dict)
        finally:
            v1_adapter_client.delete(f"/process/{data['id']}/archive")
            time.sleep(0.5)

    def test_send_message_v1(self, v1_adapter_client: TestClient) -> None:
        """AC #2: PATCH /process/{id} sends message and returns V1StatusResponse."""
        team_id = _create_v1_team(v1_adapter_client)
        try:
            resp = v1_adapter_client.patch(
                f"/process/{team_id}",
                json={"content": "Say hello in one word."},
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "ok"
        finally:
            v1_adapter_client.delete(f"/process/{team_id}/archive")
            time.sleep(0.5)

    def test_get_messages_v1(self, v1_adapter_client: TestClient) -> None:
        """AC #2: GET /messages/{id} returns V1MessageEntry list with LLM response."""
        team_id = _create_v1_team(v1_adapter_client)
        try:
            v1_adapter_client.patch(
                f"/process/{team_id}",
                json={"content": "Say hello in one word."},
            )

            messages = _wait_for_v1_messages(v1_adapter_client, team_id)

            assert len(messages) >= 1
            for msg in messages:
                assert "id" in msg
                assert "sender" in msg
                assert "content" in msg
                assert "timestamp" in msg
                assert "type" in msg
        finally:
            v1_adapter_client.delete(f"/process/{team_id}/archive")
            time.sleep(0.5)

    def test_full_round_trip_v1(self, v1_adapter_client: TestClient) -> None:
        """AC #1, #2: Full V1 round-trip -- create, send, retrieve, archive."""
        resp = v1_adapter_client.post(f"/process/{CATALOG_ENTRY_ID}")
        assert resp.status_code == 200
        create_data = resp.json()
        assert "id" in create_data
        team_id = create_data["id"]

        try:
            resp = v1_adapter_client.patch(
                f"/process/{team_id}",
                json={"content": "Say hello in one word."},
            )
            assert resp.status_code == 200
            assert resp.json()["status"] == "ok"

            messages = _wait_for_v1_messages(v1_adapter_client, team_id)
            assert _has_v1_llm_content(messages)

            llm_messages = [
                m
                for m in messages
                if isinstance(m.get("sender"), str)
                and "@Manager" in str(m.get("sender"))
                and isinstance(m.get("content"), str)
                and len(str(m.get("content"))) > 0
            ]
            assert len(llm_messages) >= 1
            llm_msg = llm_messages[0]
            assert "id" in llm_msg
            assert "sender" in llm_msg
            assert "content" in llm_msg
            assert "timestamp" in llm_msg
            assert "type" in llm_msg
        finally:
            resp = v1_adapter_client.delete(f"/process/{team_id}/archive")
            assert resp.status_code == 200
            assert resp.json()["status"] == "ok"
            time.sleep(0.5)


# ---------------------------------------------------------------------------
# Tests — V1 WebSocket
# ---------------------------------------------------------------------------


class TestV1WebSocket:
    """Integration tests for WebSocket events with V1 adapter loaded."""

    def test_ws_v1_envelope_format(self, v1_adapter_client: TestClient) -> None:
        """AC #3: WebSocket delivers events in V1 envelope format."""
        team_id = _create_v1_team(v1_adapter_client)
        try:
            with v1_adapter_client.websocket_connect(f"/ws/{team_id}") as ws:
                time.sleep(0.3)
                v1_adapter_client.patch(
                    f"/process/{team_id}",
                    json={"content": "Say hello"},
                )

                data = ws.receive_json(mode="text")
                assert isinstance(data, dict)
                assert "payload" in data
                payload = data["payload"]
                assert isinstance(payload, dict)
                assert "type" in payload
                assert payload["type"] in ("message", "state", "tool_update")

                if payload["type"] == "message":
                    assert "sender" in payload
                    assert "content" in payload
                    assert "message_type" in payload
                elif payload["type"] == "state":
                    assert "agent" in payload
                    assert "state" in payload
        finally:
            v1_adapter_client.delete(f"/process/{team_id}/archive")
            time.sleep(0.5)

    def test_ws_v1_message_fields(self, v1_adapter_client: TestClient) -> None:
        """AC #3: V1 message envelope contains sender, content, message_type."""
        team_id = _create_v1_team(v1_adapter_client)
        try:
            message_events: list[dict[str, object]] = []
            with v1_adapter_client.websocket_connect(f"/ws/{team_id}") as ws:
                time.sleep(0.3)
                v1_adapter_client.patch(
                    f"/process/{team_id}",
                    json={"content": "Say yes"},
                )

                deadline = time.monotonic() + 30.0
                while time.monotonic() < deadline:
                    try:
                        data = ws.receive_json(mode="text")
                    except (WebSocketDisconnect, RuntimeError):
                        break
                    if not isinstance(data, dict) or "payload" not in data:
                        continue
                    payload = data["payload"]
                    if isinstance(payload, dict) and payload.get("type") == "message":
                        message_events.append(payload)
                        break

            assert len(message_events) >= 1, "Expected at least one V1 message envelope"
            msg = message_events[0]
            assert "sender" in msg
            assert "content" in msg
            assert "message_type" in msg
            assert "id" in msg
            assert "timestamp" in msg
            assert msg["message_type"] in ("user", "agent", "system")
        finally:
            v1_adapter_client.delete(f"/process/{team_id}/archive")
            time.sleep(0.5)
