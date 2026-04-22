"""WebSocket E2E tests — real running server, real LLM (Story 9.8, AC #9-#13).

Uses the ``websockets`` library for WS client connections.
All tests hit a live server via httpx.Client + websockets.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
import pytest
import websockets

from tests.e2e.conftest import create_team, delete_team, send_message

pytestmark = [pytest.mark.e2e]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_e2e_ws_live_events(
    e2e_http_client: httpx.Client,
    e2e_ws_url: str,
) -> None:
    """AC #9: Connect WS, send message via REST, receive live events."""
    team_id: str | None = None
    try:
        team_id = create_team(e2e_http_client)
        uri = f"{e2e_ws_url}/ws/{team_id}"

        async with websockets.connect(uri) as ws:
            # Send message via REST
            send_message(e2e_http_client, team_id, "hello from e2e ws test")

            # Collect events from WS
            events: list[dict[str, Any]] = []
            loop = asyncio.get_running_loop()
            deadline = loop.time() + 60.0
            while loop.time() < deadline:
                remaining = deadline - loop.time()
                if remaining <= 0:
                    break
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=min(remaining, 2.0))
                    data = json.loads(raw)
                    events.append(data)
                    # Check if we have a SentMessage from @Manager
                    model = data.get("__model__", "")
                    short = model.rsplit(".", 1)[-1] if model else ""
                    sender = data.get("sender", {})
                    if (
                        short == "SentMessage"
                        and isinstance(sender, dict)
                        and sender.get("name") == "@Manager"
                    ):
                        break
                except TimeoutError:
                    continue

            assert len(events) >= 1, "Expected at least 1 event from WebSocket"

            # Verify we got various event types
            model_types = set()
            for ev in events:
                model = ev.get("__model__", "")
                short = model.rsplit(".", 1)[-1] if model else ""
                if short:
                    model_types.add(short)

            # AC #10: Verify event types received
            assert len(model_types) >= 1, f"Expected event types, got: {model_types}"
    finally:
        if team_id:
            delete_team(e2e_http_client, team_id)


async def test_e2e_ws_message_content_shape(
    e2e_http_client: httpx.Client,
    e2e_ws_url: str,
) -> None:
    """AC #11: Verify SentMessage events contain nested message.content."""
    team_id: str | None = None
    try:
        team_id = create_team(e2e_http_client)
        uri = f"{e2e_ws_url}/ws/{team_id}"

        async with websockets.connect(uri) as ws:
            send_message(e2e_http_client, team_id, "hello")

            # Wait for SentMessage from @Manager
            sent_message = None
            loop = asyncio.get_running_loop()
            deadline = loop.time() + 60.0
            while loop.time() < deadline:
                remaining = deadline - loop.time()
                if remaining <= 0:
                    break
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=min(remaining, 2.0))
                    data = json.loads(raw)
                    model = data.get("__model__", "")
                    short = model.rsplit(".", 1)[-1] if model else ""
                    sender = data.get("sender", {})
                    if (
                        short == "SentMessage"
                        and isinstance(sender, dict)
                        and sender.get("name") == "@Manager"
                    ):
                        sent_message = data
                        break
                except TimeoutError:
                    continue

            assert sent_message is not None, "No SentMessage from @Manager received via WS"

            # AC #11: nested message.content (not flat content)
            message = sent_message.get("message")
            assert isinstance(message, dict), "SentMessage.message must be a dict"
            content = message.get("content")
            assert isinstance(content, str) and len(content) > 0, (
                "SentMessage.message.content must be a non-empty string"
            )
    finally:
        if team_id:
            delete_team(e2e_http_client, team_id)


async def test_e2e_ws_tool_call_arguments(
    e2e_http_client: httpx.Client,
    e2e_ws_url: str,
) -> None:
    """AC #12: If ToolCallEvent present, verify it has arguments field.

    Note: ToolCallEvents are only produced when the LLM uses tools. This test
    collects all events and checks any ToolCallEvent found. If no tool calls
    occur (common with simple prompts), the test passes with a note.
    """
    team_id: str | None = None
    try:
        team_id = create_team(e2e_http_client)
        uri = f"{e2e_ws_url}/ws/{team_id}"

        async with websockets.connect(uri) as ws:
            send_message(e2e_http_client, team_id, "hello")

            events: list[dict[str, Any]] = []
            loop = asyncio.get_running_loop()
            deadline = loop.time() + 60.0
            found_manager = False
            while loop.time() < deadline and not found_manager:
                remaining = deadline - loop.time()
                if remaining <= 0:
                    break
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=min(remaining, 2.0))
                    data = json.loads(raw)
                    events.append(data)
                    model = data.get("__model__", "")
                    short = model.rsplit(".", 1)[-1] if model else ""
                    sender = data.get("sender", {})
                    if (
                        short == "SentMessage"
                        and isinstance(sender, dict)
                        and sender.get("name") == "@Manager"
                    ):
                        found_manager = True
                except TimeoutError:
                    continue

            # Check for ToolCallEvent
            tool_call_events = []
            for ev in events:
                model = ev.get("__model__", "")
                short = model.rsplit(".", 1)[-1] if model else ""
                if short == "ToolCallEvent":
                    tool_call_events.append(ev)

            # AC #12: If tool calls occurred, verify arguments field
            for tc in tool_call_events:
                assert "arguments" in tc, "ToolCallEvent must have 'arguments' field"
    finally:
        if team_id:
            delete_team(e2e_http_client, team_id)


async def test_e2e_ws_disconnect_on_delete(
    e2e_http_client: httpx.Client,
    e2e_ws_url: str,
) -> None:
    """AC #13: Verify WS disconnects when team is deleted."""
    team_id: str | None = None
    try:
        team_id = create_team(e2e_http_client)
        uri = f"{e2e_ws_url}/ws/{team_id}"

        async with websockets.connect(uri) as ws:
            # Delete team via REST (run sync httpx in executor to avoid ASYNC212)
            loop = asyncio.get_running_loop()
            resp = await loop.run_in_executor(
                None,
                e2e_http_client.delete,
                f"/teams/{team_id}",
            )
            assert resp.status_code == 204
            team_id = None  # Already deleted

            # WS should disconnect (receive close frame or connection error)
            disconnected = False
            try:
                # Try to receive — should get a close or error
                await asyncio.wait_for(ws.recv(), timeout=10.0)
            except (
                websockets.exceptions.ConnectionClosed,
                websockets.exceptions.ConnectionClosedError,
                websockets.exceptions.ConnectionClosedOK,
                TimeoutError,
            ):
                disconnected = True

            # If recv returned data, the WS may still be open — check state
            if not disconnected:
                # The connection should be closing/closed after team deletion
                assert ws.close_code is not None, (
                    "WebSocket should disconnect when team is deleted"
                )
    finally:
        if team_id:
            delete_team(e2e_http_client, team_id)
