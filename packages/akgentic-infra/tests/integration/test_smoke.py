"""End-to-end smoke tests -- real app, real actors, TestModel LLM (Story 9.6).

Every test creates a real team via TestClient backed by wire_community() +
create_app() with ``pydantic_ai.models.test.TestModel`` injected for
deterministic, offline LLM responses.  No ``OPENAI_API_KEY`` required.

No mocks. No hand-crafted dicts.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient
from rich.console import Console

from akgentic.core.messages.message import Message
from akgentic.core.utils.deserializer import deserialize_object
from akgentic.infra.cli.renderers import RichRenderer
from akgentic.infra.cli.repl import _render_event_impl

from ._helpers import CATALOG_ENTRY_ID, create_team, has_llm_content, poll_until

pytestmark = [pytest.mark.smoke]

# Shorter timeouts -- TestModel responds instantly
_POLL_TIMEOUT = 10.0
_POLL_INTERVAL = 0.2


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_events(client: TestClient, team_id: str) -> list[dict[str, Any]]:
    """Fetch events from the events endpoint."""
    resp = client.get(f"/teams/{team_id}/events")
    assert resp.status_code == 200
    return resp.json()["events"]


def _send_message(client: TestClient, team_id: str, content: str = "hello") -> None:
    """Send a message to a team."""
    resp = client.post(f"/teams/{team_id}/message", json={"content": content})
    assert resp.status_code == 204


def _wait_for_manager_response(
    client: TestClient, team_id: str,
) -> list[dict[str, Any]]:
    """Poll events until @Manager has responded with content."""
    events: list[dict[str, Any]] = []

    def _check() -> bool:
        nonlocal events
        events = _get_events(client, team_id)
        return has_llm_content(events)

    poll_until(_check, timeout=_POLL_TIMEOUT, interval=_POLL_INTERVAL,
               message="Timed out waiting for @Manager response")
    return events


def _delete_team(client: TestClient, team_id: str) -> None:
    """Delete a team (best-effort cleanup)."""
    client.delete(f"/teams/{team_id}")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_smoke_list_catalog_teams(smoke_client: TestClient) -> None:
    """GET /catalog/api/teams/ returns the seeded catalog."""
    resp = smoke_client.get("/catalog/api/teams/")
    assert resp.status_code == 200
    teams = resp.json()
    assert isinstance(teams, list)
    assert len(teams) >= 1

    # Find our seeded entry
    entry = next((t for t in teams if t["id"] == CATALOG_ENTRY_ID), None)
    assert entry is not None, f"Expected catalog entry '{CATALOG_ENTRY_ID}' not found"

    # Validate response shape (AC #5: validate rendered CLI output / data shape)
    for field in ("id", "name", "entry_point", "members"):
        assert field in entry, f"Missing field '{field}' in catalog entry"


def test_smoke_create_team(smoke_client: TestClient) -> None:
    """POST /teams/ creates a running team."""
    team_id: str | None = None
    try:
        resp = smoke_client.post("/teams/", json={"catalog_entry_id": CATALOG_ENTRY_ID})
        assert resp.status_code == 201
        data = resp.json()
        assert data["status"] == "running"
        team_id = data["team_id"]
        assert team_id is not None

        # Verify GET /teams/{team_id} returns the created team
        resp2 = smoke_client.get(f"/teams/{team_id}")
        assert resp2.status_code == 200
        assert resp2.json()["status"] == "running"
    finally:
        if team_id:
            _delete_team(smoke_client, team_id)


def test_smoke_send_message_receive_response(smoke_client: TestClient) -> None:
    """Send message via REST, verify SentMessage with nested message.content."""
    team_id: str | None = None
    try:
        team_id = create_team(smoke_client)

        _send_message(smoke_client, team_id, "hello")
        events = _wait_for_manager_response(smoke_client, team_id)

        # Find the @Manager SentMessage
        manager_event = None
        for ev in events:
            event_data = ev["event"]
            model = event_data.get("__model__", "")
            short = model.rsplit(".", 1)[-1] if model else ""
            if short != "SentMessage":
                continue
            sender = event_data.get("sender", {})
            if isinstance(sender, dict) and sender.get("name") == "@Manager":
                manager_event = event_data
                break

        assert manager_event is not None, "No @Manager SentMessage found"

        # AC #5.2: SentMessage has nested message.content (not flat content)
        message = manager_event.get("message")
        assert isinstance(message, dict), "SentMessage.message must be a dict"
        content = message.get("content")
        assert isinstance(content, str) and len(content) > 0, (
            "SentMessage.message.content must be a non-empty string"
        )

        # AC #5.4: Render through _render_event_impl and verify output
        buf = _StringCapture()
        console = Console(file=buf, highlight=False, force_terminal=True)
        renderer = RichRenderer(console=console)
        typed_event = deserialize_object(ev["event"])
        assert isinstance(typed_event, Message), "Deserialized event must be a Message"
        rendered = _render_event_impl(typed_event, renderer)
        assert rendered is True, "_render_event_impl should return True for SentMessage"
        rendered_text = buf.getvalue()
        assert "@Manager" in rendered_text, "Rendered output must contain @Manager"
        assert len(rendered_text.strip()) > 0, "Rendered output must not be empty"
    finally:
        if team_id:
            _delete_team(smoke_client, team_id)


def test_smoke_agents_shows_members(smoke_client: TestClient) -> None:
    """Create team, verify StartMessage entries for @Human and @Manager."""
    team_id: str | None = None
    try:
        team_id = create_team(smoke_client)

        # Poll until @Manager StartMessage appears (actors start async)
        events: list[dict[str, Any]] = []

        def _has_manager_start() -> bool:
            nonlocal events
            events = _get_events(smoke_client, team_id)
            return any(
                _is_start_message(ev["event"])
                and ev["event"].get("config", {}).get("name") == "@Manager"
                for ev in events
            )

        poll_until(
            _has_manager_start,
            timeout=_POLL_TIMEOUT,
            interval=_POLL_INTERVAL,
            message="Timed out waiting for @Manager StartMessage",
        )

        # Extract StartMessage events
        start_events = [
            ev["event"] for ev in events if _is_start_message(ev["event"])
        ]
        assert len(start_events) >= 2, (
            f"Expected at least 2 StartMessage events, got {len(start_events)}"
        )

        # Verify agent names
        agent_names = set()
        for se in start_events:
            config = se.get("config", {})
            assert "name" in config, "StartMessage must have config.name"
            assert "role" in config, "StartMessage must have config.role"
            agent_names.add(config["name"])

        assert "@Human" in agent_names, "@Human not in StartMessage agents"
        assert "@Manager" in agent_names, "@Manager not in StartMessage agents"
    finally:
        if team_id:
            _delete_team(smoke_client, team_id)


def test_smoke_history_renders_messages(smoke_client: TestClient) -> None:
    """Send message, wait for response, render events, verify output."""
    team_id: str | None = None
    try:
        team_id = create_team(smoke_client)
        _send_message(smoke_client, team_id, "hello")
        events = _wait_for_manager_response(smoke_client, team_id)

        # Render each event through _render_event_impl and capture output
        rendered_outputs: list[str] = []
        for ev in events:
            try:
                typed_event = deserialize_object(ev["event"])
            except (ValueError, KeyError):
                continue
            if not isinstance(typed_event, Message):
                continue
            buf = _StringCapture()
            console = Console(file=buf, highlight=False, force_terminal=True)
            renderer = RichRenderer(console=console)
            was_rendered = _render_event_impl(typed_event, renderer)
            if was_rendered:
                rendered_outputs.append(buf.getvalue())

        # Must have at least 1 rendered message (agent response)
        # (user messages via REST don't produce SentMessage events from @Human)
        assert len(rendered_outputs) >= 1, (
            f"Expected at least 1 rendered message, got {len(rendered_outputs)}"
        )

        # Verify rendered output contains agent name and message content
        combined = "\n".join(rendered_outputs)
        assert "@Manager" in combined, "Rendered output must contain @Manager"
        # Verify actual message content appears (TestModel returns "Hello from TestModel")
        assert len(combined.strip()) > len("@Manager"), (
            "Rendered output must contain message content, not just agent name"
        )
    finally:
        if team_id:
            _delete_team(smoke_client, team_id)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _is_start_message(event_data: dict[str, Any]) -> bool:
    """Check if an event dict is a StartMessage."""
    model = event_data.get("__model__", "")
    short = model.rsplit(".", 1)[-1] if model else ""
    return short == "StartMessage"


class _StringCapture:
    """Minimal file-like object that captures written text."""

    def __init__(self) -> None:
        self._parts: list[str] = []

    def write(self, s: str) -> int:
        self._parts.append(s)
        return len(s)

    def flush(self) -> None:
        pass

    def getvalue(self) -> str:
        return "".join(self._parts)
