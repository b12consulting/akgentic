"""Shared helpers for integration tests — real app, real LLM."""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from akgentic.infra.cli.client import ApiClient
from akgentic.infra.cli.formatters import OutputFormat
from akgentic.infra.cli.repl import ChatSession
from akgentic.infra.cli.ws_client import WsClient

CATALOG_ENTRY_ID = "test-team"
POLL_INTERVAL_S = 1.0
POLL_TIMEOUT_S = 60.0


class StubRenderer:
    """Lightweight renderer stub that captures events without mocking.

    Captures agent messages and errors for assertions. All other render methods
    are no-ops to satisfy the RichRenderer interface used by ChatSession,
    TeamSelector, and slash command handlers.
    """

    def __init__(self) -> None:
        self.agent_messages: list[str] = []
        self.errors: list[str] = []

    def render_agent_message(self, sender: str, content: str) -> None:
        self.agent_messages.append(f"{sender}: {content}")

    def render_error(self, content: str) -> None:
        self.errors.append(content)

    def render_system_message(self, *args: object, **kwargs: object) -> None:
        pass

    def render_tool_call(self, *args: object, **kwargs: object) -> None:
        pass

    def render_human_input_request(self, *args: object, **kwargs: object) -> None:
        pass

    def render_history_separator(self, *args: object, **kwargs: object) -> None:
        pass

    def render_border(self, *args: object, **kwargs: object) -> None:
        pass

    def render_status_bar(self, *args: object, **kwargs: object) -> None:
        pass

    def render_welcome_header(self, *args: object, **kwargs: object) -> None:
        pass

    def render_team_list(self, *args: object, **kwargs: object) -> None:
        pass

    def render_catalog_list(self, *args: object, **kwargs: object) -> None:
        pass

    def render_startup_hints(self, *args: object, **kwargs: object) -> None:
        pass

    def render_pagination_hints(self, *args: object, **kwargs: object) -> None:
        pass


def make_integration_session(cli_server_url: str, team_id: str) -> ChatSession:
    """Build a ChatSession for integration tests with StubRenderer."""
    api_client = ApiClient(base_url=cli_server_url)
    ws = WsClient(base_url=cli_server_url, team_id=team_id)
    renderer = StubRenderer()
    return ChatSession(
        api_client,
        ws,
        team_id,
        OutputFormat.table,
        server_url=cli_server_url,
        renderer=renderer,
    )


def create_team(client: TestClient) -> str:
    """POST /teams and return the team_id."""
    resp = client.post("/teams/", json={"catalog_entry_id": CATALOG_ENTRY_ID})
    assert resp.status_code == 201
    data = resp.json()
    assert data["status"] == "running"
    return data["team_id"]


def wait_for_llm_response(
    client: TestClient,
    team_id: str,
    timeout: float = POLL_TIMEOUT_S,
) -> list[dict[str, object]]:
    """Poll GET /teams/{team_id}/events until @Manager responds."""
    deadline = time.monotonic() + timeout
    events: list[dict[str, object]] = []
    while time.monotonic() < deadline:
        resp = client.get(f"/teams/{team_id}/events")
        assert resp.status_code == 200
        events = resp.json()["events"]
        if has_llm_content(events):
            return events
        time.sleep(POLL_INTERVAL_S)
    pytest.fail(
        f"Timed out after {timeout}s waiting for LLM response "
        f"(got {len(events)} events, none with LLM content)"
    )


def _write_yaml(path: Path, data: dict[str, object]) -> None:
    """Write a single YAML entry file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.dump(data, default_flow_style=False))


def seed_integration_catalog(catalog_root: Path) -> None:
    """Seed YAML catalog with an LLM-capable agent for integration testing.

    Uses gpt-4o-mini for fast, cheap LLM calls.
    """
    _write_yaml(
        catalog_root / "agents" / "human-proxy.yaml",
        {
            "id": "human-proxy",
            "tool_ids": [],
            "card": {
                "role": "Human",
                "description": "Human user interface",
                "skills": [],
                "agent_class": "akgentic.agent.HumanProxy",
                "config": {"name": "@Human", "role": "Human"},
                "routes_to": ["@Manager"],
            },
        },
    )
    _write_yaml(
        catalog_root / "agents" / "manager.yaml",
        {
            "id": "manager",
            "tool_ids": [],
            "card": {
                "role": "Manager",
                "description": "Integration test manager agent",
                "skills": ["coordination"],
                "agent_class": "akgentic.agent.BaseAgent",
                "config": {
                    "name": "@Manager",
                    "role": "Manager",
                    "prompt": {
                        "template": (
                            "You are a helpful assistant. Reply concisely in one or two sentences."
                        ),
                    },
                    "model_cfg": {
                        "provider": "openai",
                        "model": "gpt-4o-mini",
                        "temperature": 0.0,
                    },
                    "usage_limits": {
                        "request_limit": 5,
                        "total_tokens_limit": 10000,
                    },
                },
                "routes_to": [],
            },
        },
    )
    _write_yaml(
        catalog_root / "teams" / "test-team.yaml",
        {
            "id": "test-team",
            "name": "Integration Test Team",
            "entry_point": "human-proxy",
            "message_types": ["akgentic.agent.AgentMessage"],
            "members": [
                {"agent_id": "human-proxy"},
                {"agent_id": "manager"},
            ],
            "profiles": [],
        },
    )
    (catalog_root / "templates").mkdir(parents=True, exist_ok=True)
    (catalog_root / "tools").mkdir(parents=True, exist_ok=True)


def poll_until(
    predicate: Callable[[], bool],
    timeout: float = 5.0,
    interval: float = 0.1,
    message: str = "Condition not met within timeout",
) -> None:
    """Poll predicate until True or raise TimeoutError after timeout."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(interval)
    raise TimeoutError(message)


def has_llm_content(events: list[dict[str, object]]) -> bool:
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
