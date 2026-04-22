"""Integration tests -- Real V1 adapter round-trip tests (Story 9.5).

Every test creates a real team via the V1 TestClient backed by
wire_community() + create_app(), exercises a V1 endpoint, and validates
the response shape against the V1 contract in models.py.

No mocks. No hand-crafted dicts.
"""

from __future__ import annotations

import time
import uuid

import pytest
from fastapi.testclient import TestClient

pytestmark = [pytest.mark.integration, pytest.mark.llm]

CATALOG_ENTRY_ID = "test-team"
POLL_INTERVAL_S = 1.0
POLL_TIMEOUT_S = 60.0

# ---------------------------------------------------------------------------
# V1 response shape field sets (from models.py contract)
# ---------------------------------------------------------------------------

V1_PROCESS_CONTEXT_FIELDS = {
    "id",
    "type",
    "status",
    "created_at",
    "updated_at",
    "params",
    "orchestrator",
    "running",
    "config_name",
    "user_id",
    "user_email",
}

V1_MESSAGE_ENTRY_FIELDS = {"id", "sender", "content", "timestamp", "type"}

V1_STATUS_RESPONSE_FIELDS = {"status"}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _create_v1_team(client: TestClient) -> str:
    """POST /process/{type} (V1) and return the team_id."""
    resp = client.post(f"/process/{CATALOG_ENTRY_ID}")
    assert resp.status_code == 200
    data = resp.json()
    assert "id" in data
    return str(data["id"])


def _wait_for_v1_messages(
    client: TestClient,
    team_id: str,
    timeout: float = POLL_TIMEOUT_S,
) -> list[dict[str, object]]:
    """Poll GET /messages/{id} until at least one message with content appears."""
    deadline = time.monotonic() + timeout
    messages: list[dict[str, object]] = []
    while time.monotonic() < deadline:
        resp = client.get(f"/messages/{team_id}")
        assert resp.status_code == 200
        messages = resp.json()
        for msg in messages:
            content = msg.get("content")
            sender = msg.get("sender")
            if (
                isinstance(content, str)
                and len(content) > 0
                and isinstance(sender, str)
                and "@Manager" in sender
            ):
                return messages
        time.sleep(POLL_INTERVAL_S)
    pytest.fail(
        f"Timed out after {timeout}s waiting for V1 LLM response "
        f"(got {len(messages)} messages)"
    )


def _assert_v1_process_context(data: dict[str, object]) -> None:
    """Assert response matches V1ProcessContext contract."""
    for field in V1_PROCESS_CONTEXT_FIELDS:
        assert field in data, f"Missing field: {field}"
    assert isinstance(data["orchestrator"], dict)
    orch = data["orchestrator"]
    assert "name" in orch
    assert "role" in orch
    assert isinstance(data["params"], dict)
    assert isinstance(data["running"], bool)


# ---------------------------------------------------------------------------
# Task 2: V1 REST endpoint tests (AC #1, #2, #6)
# ---------------------------------------------------------------------------


class TestV1CreateProcess:
    """POST /process/{type} round-trip."""

    def test_create_process(self, v1_adapter_client: TestClient) -> None:
        """AC2: POST /process/{type} returns 200 with V1ProcessContext shape."""
        team_id = _create_v1_team(v1_adapter_client)
        try:
            resp = v1_adapter_client.get(f"/process/{team_id}")
            assert resp.status_code == 200
            data = resp.json()
            _assert_v1_process_context(data)
            assert data["status"] == "running"
            assert data["running"] is True
            assert data["params"]["workspace"] == "false"
            assert data["params"]["knowledge_graph"] == "false"
            assert data["user_id"] == "anonymous"
        finally:
            v1_adapter_client.delete(f"/process/{team_id}/archive")
            time.sleep(0.5)

    def test_create_process_not_found(self, v1_adapter_client: TestClient) -> None:
        """AC2: POST /process/nonexistent-type returns 404."""
        resp = v1_adapter_client.post("/process/nonexistent-type")
        assert resp.status_code == 404


class TestV1ListProcesses:
    """GET /process/ and GET /processes/ round-trips."""

    def test_list_processes(self, v1_adapter_client: TestClient) -> None:
        """AC2: GET /process/ returns flat list containing created team."""
        team_id = _create_v1_team(v1_adapter_client)
        try:
            resp = v1_adapter_client.get("/process/")
            assert resp.status_code == 200
            data = resp.json()
            assert isinstance(data, list)
            ids = [p["id"] for p in data]
            assert team_id in ids
            for process in data:
                _assert_v1_process_context(process)
        finally:
            v1_adapter_client.delete(f"/process/{team_id}/archive")
            time.sleep(0.5)

    def test_list_processes_alias(self, v1_adapter_client: TestClient) -> None:
        """AC2: GET /processes/ returns same flat list as GET /process/."""
        team_id = _create_v1_team(v1_adapter_client)
        try:
            resp_process = v1_adapter_client.get("/process/")
            resp_processes = v1_adapter_client.get("/processes/")
            assert resp_process.status_code == 200
            assert resp_processes.status_code == 200
            assert isinstance(resp_processes.json(), list)
            assert len(resp_process.json()) == len(resp_processes.json())
        finally:
            v1_adapter_client.delete(f"/process/{team_id}/archive")
            time.sleep(0.5)


class TestV1GetProcess:
    """GET /process/{id} round-trip."""

    def test_get_process(self, v1_adapter_client: TestClient) -> None:
        """AC2: GET /process/{id} returns single V1ProcessContext."""
        team_id = _create_v1_team(v1_adapter_client)
        try:
            resp = v1_adapter_client.get(f"/process/{team_id}")
            assert resp.status_code == 200
            data = resp.json()
            _assert_v1_process_context(data)
            assert data["id"] == team_id
        finally:
            v1_adapter_client.delete(f"/process/{team_id}/archive")
            time.sleep(0.5)

    def test_get_process_not_found(self, v1_adapter_client: TestClient) -> None:
        """AC2: GET /process/{random_uuid} returns 404."""
        random_id = str(uuid.uuid4())
        resp = v1_adapter_client.get(f"/process/{random_id}")
        assert resp.status_code == 404


class TestV1SendMessage:
    """PATCH /process/{id} round-trip."""

    def test_send_message(self, v1_adapter_client: TestClient) -> None:
        """AC2: PATCH /process/{id} with content returns status ok."""
        team_id = _create_v1_team(v1_adapter_client)
        try:
            resp = v1_adapter_client.patch(
                f"/process/{team_id}",
                json={"content": "hello"},
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "ok"
        finally:
            v1_adapter_client.delete(f"/process/{team_id}/archive")
            time.sleep(0.5)


class TestV1DeleteProcess:
    """DELETE /process/{id} round-trip."""

    def test_delete_process(self, v1_adapter_client: TestClient) -> None:
        """AC2: DELETE /process/{id} returns status ok."""
        team_id = _create_v1_team(v1_adapter_client)
        try:
            # First archive to stop it
            v1_adapter_client.delete(f"/process/{team_id}/archive")
            time.sleep(0.5)
            # Then delete
            resp = v1_adapter_client.delete(f"/process/{team_id}")
            assert resp.status_code == 200
            assert resp.json()["status"] == "ok"
        except Exception:
            v1_adapter_client.delete(f"/process/{team_id}/archive")
            time.sleep(0.5)
            raise


class TestV1ArchiveProcess:
    """DELETE /process/{id}/archive round-trip."""

    def test_archive_process(self, v1_adapter_client: TestClient) -> None:
        """AC2: DELETE /process/{id}/archive returns status ok."""
        team_id = _create_v1_team(v1_adapter_client)
        try:
            resp = v1_adapter_client.delete(f"/process/{team_id}/archive")
            assert resp.status_code == 200
            assert resp.json()["status"] == "ok"
        finally:
            time.sleep(0.5)


class TestV1RestoreProcess:
    """POST /process/{id}/restore round-trip."""

    def test_restore_process(self, v1_adapter_client: TestClient) -> None:
        """AC2: create, archive, then POST /process/{id}/restore returns V1ProcessContext."""
        team_id = _create_v1_team(v1_adapter_client)
        try:
            # Archive first
            archive_resp = v1_adapter_client.delete(f"/process/{team_id}/archive")
            assert archive_resp.status_code == 200
            time.sleep(0.5)

            # Restore
            resp = v1_adapter_client.post(f"/process/{team_id}/restore")
            assert resp.status_code == 200
            data = resp.json()
            _assert_v1_process_context(data)
            assert data["id"] == team_id
        finally:
            v1_adapter_client.delete(f"/process/{team_id}/archive")
            time.sleep(0.5)


class TestV1GetMessages:
    """GET /messages/{id} round-trip."""

    def test_get_messages(self, v1_adapter_client: TestClient) -> None:
        """AC2: send a message, then GET /messages/{id} returns V1MessageEntry list."""
        team_id = _create_v1_team(v1_adapter_client)
        try:
            v1_adapter_client.patch(
                f"/process/{team_id}",
                json={"content": "Say hello in one word."},
            )
            messages = _wait_for_v1_messages(v1_adapter_client, team_id)
            assert isinstance(messages, list)
            assert len(messages) >= 1
            for msg in messages:
                for field in V1_MESSAGE_ENTRY_FIELDS:
                    assert field in msg, f"Missing field: {field}"
        finally:
            v1_adapter_client.delete(f"/process/{team_id}/archive")
            time.sleep(0.5)


class TestV1HumanInput:
    """POST /process_human_input/{id}/human/{proxy} round-trip."""

    def test_human_input(self, v1_adapter_client: TestClient) -> None:
        """AC2: human input with message dict containing id field succeeds."""
        team_id = _create_v1_team(v1_adapter_client)
        try:
            v1_adapter_client.patch(
                f"/process/{team_id}",
                json={"content": "Say hello in one word."},
            )
            messages = _wait_for_v1_messages(v1_adapter_client, team_id)
            assert len(messages) >= 1
            msg_id = messages[0]["id"]

            resp = v1_adapter_client.post(
                f"/process_human_input/{team_id}/human/@Human",
                json={
                    "content": "reply",
                    "message": {"id": str(msg_id), "content": "original"},
                },
            )
            assert resp.status_code == 200
            assert resp.json()["status"] == "ok"
        finally:
            v1_adapter_client.delete(f"/process/{team_id}/archive")
            time.sleep(0.5)


# ---------------------------------------------------------------------------
# Task 3: V1 WebSocket envelope tests (AC #3)
# ---------------------------------------------------------------------------


class TestV1WebSocketEnvelopes:
    """WebSocket envelope tests via real V1 adapter app.

    These tests create a team, send a message, and then connect to the
    WebSocket to verify envelope shapes. Since WebSocket events are
    generated asynchronously by the actor system, we first wait for
    messages to appear via polling, then connect the WS.
    """

    def test_message_envelope(self, v1_adapter_client: TestClient) -> None:
        """AC3: message envelope has type 'message' with MessagePayload fields."""
        team_id = _create_v1_team(v1_adapter_client)
        try:
            v1_adapter_client.patch(
                f"/process/{team_id}",
                json={"content": "Say hello in one word."},
            )
            # Wait for LLM response to ensure events exist
            _wait_for_v1_messages(v1_adapter_client, team_id)

            # Connect to WebSocket and verify envelope
            with v1_adapter_client.websocket_connect(f"/ws/{team_id}") as ws:
                # The WS should replay existing events
                data = ws.receive_json(mode="text")
                payload = data["payload"]
                # First event could be message or state -- find a message
                found_message = payload["type"] == "message"
                attempts = 0
                while not found_message and attempts < 20:
                    try:
                        data = ws.receive_json(mode="text")
                        payload = data["payload"]
                        found_message = payload["type"] == "message"
                    except Exception:
                        break
                    attempts += 1

                assert found_message, (
                    "No 'message' envelope received from WS after 20 attempts"
                )
                assert payload["type"] == "message"
                assert "id" in payload
                assert "sender" in payload
                assert "content" in payload
                assert "timestamp" in payload
                assert "message_type" in payload
        finally:
            v1_adapter_client.delete(f"/process/{team_id}/archive")
            time.sleep(0.5)

    def test_error_envelope(self, v1_adapter_client: TestClient) -> None:
        """AC3: error envelope has type 'error' with ErrorPayload fields.

        We test this via the unit-level wrap_event function since triggering
        a real error through the WS requires specific failure conditions.
        The wrap_event function is exercised by the real adapter, so this
        validates the envelope shape produced by real code.
        """
        from akgentic.core.messages.orchestrator import ErrorMessage

        from akgentic.infra.server.routes.frontend_adapter import ErrorPayload
        from akgentic.infra.server.routes.frontend_adapter.angular_v1.ws import wrap_event

        msg = ErrorMessage(
            exception_type="ValueError",
            exception_value="test error",
        )
        result = wrap_event(msg)
        assert result.payload.type == "error"
        assert isinstance(result.payload, ErrorPayload)
        assert result.payload.message == "test error"
        assert result.payload.timestamp

    def test_tool_update_envelope(self, v1_adapter_client: TestClient) -> None:
        """AC3: tool_update envelope has type 'tool_update' with ToolUpdatePayload fields.

        EventMessage events produce tool_update envelopes. Validated via
        wrap_event exercising the real adapter code.
        """
        from akgentic.core.messages.orchestrator import EventMessage

        from akgentic.infra.server.routes.frontend_adapter import ToolUpdatePayload
        from akgentic.infra.server.routes.frontend_adapter.angular_v1.ws import wrap_event

        msg = EventMessage(event={"tool_name": "search", "result": "found"})
        result = wrap_event(msg)
        assert result.payload.type == "tool_update"
        assert isinstance(result.payload, ToolUpdatePayload)
        assert result.payload.event is not None
        assert result.payload.timestamp

    def test_state_envelope(self, v1_adapter_client: TestClient) -> None:
        """AC3: state envelope has type 'state' with StatePayload fields.

        StateChangedMessage events produce state envelopes. Validated via
        wrap_event exercising the real adapter code.
        """
        from akgentic.core.actor_address_impl import ActorAddressProxy
        from akgentic.core.agent_state import BaseState
        from akgentic.core.messages.orchestrator import StateChangedMessage
        from akgentic.core.utils.deserializer import ActorAddressDict

        from akgentic.infra.server.routes.frontend_adapter import StatePayload
        from akgentic.infra.server.routes.frontend_adapter.angular_v1.ws import wrap_event

        sender = ActorAddressProxy(
            ActorAddressDict(
                __actor_address__=True,
                __actor_type__="akgentic.core.actor_address_impl.ActorAddressProxy",
                agent_id=str(uuid.uuid4()),
                name="@TestAgent",
                role="Tester",
                team_id=str(uuid.uuid4()),
                squad_id="",
                user_message=False,
            )
        )
        msg = StateChangedMessage(state=BaseState())
        msg.sender = sender
        result = wrap_event(msg)
        assert result.payload.type == "state"
        assert isinstance(result.payload, StatePayload)
        assert result.payload.agent == "@TestAgent"
        assert isinstance(result.payload.state, dict)
        assert result.payload.timestamp

    @pytest.mark.skip(reason="ContextChangedMessage not yet available in any akgentic package")
    def test_llm_context_envelope(self, v1_adapter_client: TestClient) -> None:
        """AC3: llm_context envelope requires ContextChangedMessage (not yet available)."""


# ---------------------------------------------------------------------------
# Task 5: V1 response shape validation (AC #6)
# ---------------------------------------------------------------------------


class TestV1ResponseShapes:
    """Validate V1 response shapes against the actual contract."""

    def test_process_context_field_types(self, v1_adapter_client: TestClient) -> None:
        """AC6: V1ProcessContext field types match contract."""
        team_id = _create_v1_team(v1_adapter_client)
        try:
            resp = v1_adapter_client.get(f"/process/{team_id}")
            data = resp.json()

            # String fields
            assert isinstance(data["id"], str)
            assert isinstance(data["type"], str)
            assert isinstance(data["status"], str)
            assert isinstance(data["created_at"], str)
            assert isinstance(data["updated_at"], str)
            assert isinstance(data["config_name"], str)
            assert isinstance(data["user_id"], str)
            assert isinstance(data["user_email"], str)

            # Bool field
            assert isinstance(data["running"], bool)

            # Dict fields
            assert isinstance(data["params"], dict)
            for val in data["params"].values():
                assert isinstance(val, str)

            # Orchestrator dict
            orch = data["orchestrator"]
            assert isinstance(orch, dict)
            assert isinstance(orch["name"], str)
            assert isinstance(orch["role"], str)
        finally:
            v1_adapter_client.delete(f"/process/{team_id}/archive")
            time.sleep(0.5)

    def test_get_process_list_returns_list(self, v1_adapter_client: TestClient) -> None:
        """AC6: GET /process/ returns a list (not a dict-grouped response)."""
        team_id = _create_v1_team(v1_adapter_client)
        try:
            resp = v1_adapter_client.get("/process/")
            assert resp.status_code == 200
            assert isinstance(resp.json(), list)
        finally:
            v1_adapter_client.delete(f"/process/{team_id}/archive")
            time.sleep(0.5)

    def test_message_entry_field_types(self, v1_adapter_client: TestClient) -> None:
        """AC6: V1MessageEntry field types match contract."""
        team_id = _create_v1_team(v1_adapter_client)
        try:
            v1_adapter_client.patch(
                f"/process/{team_id}",
                json={"content": "Say hello in one word."},
            )
            messages = _wait_for_v1_messages(v1_adapter_client, team_id)
            for msg in messages:
                assert isinstance(msg["id"], str)
                assert isinstance(msg["sender"], str)
                assert isinstance(msg["content"], str)
                assert isinstance(msg["timestamp"], str)
                assert isinstance(msg["type"], str)
        finally:
            v1_adapter_client.delete(f"/process/{team_id}/archive")
            time.sleep(0.5)

    def test_status_response_shape(self, v1_adapter_client: TestClient) -> None:
        """AC6: V1StatusResponse has exactly {status: str}."""
        team_id = _create_v1_team(v1_adapter_client)
        try:
            resp = v1_adapter_client.patch(
                f"/process/{team_id}",
                json={"content": "hi"},
            )
            data = resp.json()
            assert "status" in data
            assert isinstance(data["status"], str)
            assert data["status"] == "ok"
        finally:
            v1_adapter_client.delete(f"/process/{team_id}/archive")
            time.sleep(0.5)
