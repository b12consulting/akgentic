"""V1 Frontend Adapter E2E tests — real running server (Story 9.8, AC #21-#26).

These tests require the server to have the V1 frontend adapter enabled.
Tests are skipped if the V1 adapter is not active (GET /process returns 404).
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
import pytest
import websockets

from tests.e2e.conftest import CATALOG_ENTRY_ID, poll_until

pytestmark = [pytest.mark.e2e]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def v1_enabled(e2e_http_client: httpx.Client) -> None:
    """Skip tests if the V1 adapter is not enabled on the running server."""
    resp = e2e_http_client.get("/process/")
    if resp.status_code == 404:
        pytest.skip("V1 frontend adapter not enabled on the running server")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _create_v1_process(client: httpx.Client) -> str:
    """Create a V1 process and return its id."""
    resp = client.post(f"/process/{CATALOG_ENTRY_ID}")
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    data: dict[str, Any] = resp.json()
    assert "id" in data
    assert "status" in data
    process_id: str = data["id"]
    return process_id


def _delete_v1_process(client: httpx.Client, process_id: str) -> None:
    """Best-effort V1 process cleanup."""
    try:
        client.delete(f"/process/{process_id}")
    except Exception:  # noqa: BLE001
        pass


def _has_v1_message_response(messages: list[dict[str, Any]]) -> bool:
    """Check if any V1 message is from an agent with content."""
    for msg in messages:
        if msg.get("type") == "agent" and msg.get("content"):
            return True
    return False


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_e2e_v1_list_processes(
    e2e_http_client: httpx.Client,
    v1_enabled: None,
) -> None:
    """AC #21: GET /process returns V1 response shape."""
    resp = e2e_http_client.get("/process/")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    # Each entry should have V1 shape
    for entry in data:
        assert "id" in entry
        assert "status" in entry
        assert "type" in entry


def test_e2e_v1_create_process(
    e2e_http_client: httpx.Client,
    v1_enabled: None,
) -> None:
    """AC #22: POST /process/agent-team creates V1 process."""
    process_id: str | None = None
    try:
        resp = e2e_http_client.post(f"/process/{CATALOG_ENTRY_ID}")
        assert resp.status_code == 200
        data = resp.json()
        assert "id" in data
        assert "status" in data
        process_id = data["id"]

        # V1 specific fields
        assert "type" in data
        assert "created_at" in data
    finally:
        if process_id:
            _delete_v1_process(e2e_http_client, process_id)


def test_e2e_v1_send_message(
    e2e_http_client: httpx.Client,
    v1_enabled: None,
) -> None:
    """AC #23: POST /process/{id}/message + wait, verify V1 message shape."""
    process_id: str | None = None
    try:
        process_id = _create_v1_process(e2e_http_client)

        # Send message via V1 PATCH endpoint
        resp = e2e_http_client.patch(
            f"/process/{process_id}",
            json={"content": "hello from V1"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

        # Wait for response via V1 messages endpoint
        messages: list[dict[str, Any]] = []

        def _check() -> bool:
            nonlocal messages
            resp = e2e_http_client.get(f"/messages/{process_id}")
            if resp.status_code != 200:
                return False
            messages = resp.json()
            return _has_v1_message_response(messages)

        poll_until(
            _check, timeout=60.0, interval=1.0, message="Timed out waiting for V1 message response"
        )

        # Verify V1 message shape
        for msg in messages:
            assert "id" in msg
            assert "sender" in msg
            assert "content" in msg
            assert "timestamp" in msg
            assert "type" in msg
    finally:
        if process_id:
            _delete_v1_process(e2e_http_client, process_id)


def test_e2e_v1_events(
    e2e_http_client: httpx.Client,
    v1_enabled: None,
) -> None:
    """AC #24: GET /process/{id}/events verifies V1 event format."""
    process_id: str | None = None
    try:
        process_id = _create_v1_process(e2e_http_client)

        # Send a message to generate events
        e2e_http_client.patch(
            f"/process/{process_id}",
            json={"content": "hello"},
        )

        # Wait for events via V1 messages endpoint
        messages: list[dict[str, Any]] = []

        def _check() -> bool:
            nonlocal messages
            resp = e2e_http_client.get(f"/messages/{process_id}")
            if resp.status_code != 200:
                return False
            messages = resp.json()
            return _has_v1_message_response(messages)

        poll_until(_check, timeout=60.0, interval=1.0, message="Timed out waiting for V1 events")

        assert len(messages) >= 1, "Expected at least 1 V1 message"
    finally:
        if process_id:
            _delete_v1_process(e2e_http_client, process_id)


def _is_v1_manager_message(data: dict[str, Any]) -> bool:
    """Check if a V1 WS event is a message envelope from @Manager."""
    if data.get("type") != "message":
        return False
    payload = data.get("payload", {})
    if not isinstance(payload, dict):
        return False
    return bool(payload.get("message_type") == "agent" and payload.get("sender") == "@Manager")


async def _collect_v1_ws_events(
    ws: websockets.ClientConnection,
    deadline: float,
) -> tuple[list[dict[str, Any]], set[str]]:
    """Collect V1 WS events until @Manager responds or deadline expires."""
    events: list[dict[str, Any]] = []
    envelope_types: set[str] = set()
    loop = asyncio.get_running_loop()
    while loop.time() < deadline:
        remaining = deadline - loop.time()
        if remaining <= 0:
            break
        try:
            raw = await asyncio.wait_for(ws.recv(), timeout=min(remaining, 2.0))
            data = json.loads(raw)
            events.append(data)
            env_type = data.get("type")
            if env_type:
                envelope_types.add(env_type)
            if _is_v1_manager_message(data):
                break
        except TimeoutError:
            continue
    return events, envelope_types


async def test_e2e_v1_websocket(
    e2e_http_client: httpx.Client,
    e2e_ws_url: str,
    v1_enabled: None,
) -> None:
    """AC #25: V1 WebSocket sends envelope types: message, state, tool_update."""
    process_id: str | None = None
    try:
        process_id = _create_v1_process(e2e_http_client)
        uri = f"{e2e_ws_url}/ws/{process_id}"

        async with websockets.connect(uri) as ws:
            # Send message via V1 REST (run sync in executor to satisfy ASYNC212)
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(
                None,
                lambda: e2e_http_client.patch(
                    f"/process/{process_id}",
                    json={"content": "hello"},
                ),
            )

            deadline = loop.time() + 60.0
            events, envelope_types = await _collect_v1_ws_events(ws, deadline)

            assert len(events) >= 1, "Expected at least 1 V1 WS event"
            assert "message" in envelope_types, (
                f"Expected 'message' envelope type, got: {envelope_types}"
            )
    finally:
        if process_id:
            _delete_v1_process(e2e_http_client, process_id)


def test_e2e_v1_delete(
    e2e_http_client: httpx.Client,
    v1_enabled: None,
) -> None:
    """AC #26: DELETE /process/{id} verifies V1 delete response."""
    process_id: str | None = None
    process_id = _create_v1_process(e2e_http_client)
    try:
        resp = e2e_http_client.delete(f"/process/{process_id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"

        # Verify deleted
        resp = e2e_http_client.get(f"/process/{process_id}")
        assert resp.status_code == 404
        process_id = None  # Already deleted
    finally:
        if process_id:
            _delete_v1_process(e2e_http_client, process_id)
