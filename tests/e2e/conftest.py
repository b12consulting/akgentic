"""E2E test fixtures — real running server, real LLM, real CLI binary.

Run locally with:
    source .env && uvicorn akgentic.infra.server.app:app --port 8010 &
    pytest tests/e2e/ -v

All E2E tests require:
  1. A running akgentic-infra server (default http://localhost:8010)
  2. OPENAI_API_KEY set in the environment (or loaded from .env)

Tests are skipped when either condition is not met.
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable, Generator
from pathlib import Path
from typing import Any

import httpx
import pytest
from dotenv import load_dotenv

_PROJECT_ROOT = Path(__file__).resolve().parents[4]

CATALOG_ENTRY_ID = "test-team"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def poll_until(
    predicate: Callable[[], bool],
    timeout: float = 60.0,
    interval: float = 1.0,
    message: str = "Condition not met within timeout",
) -> None:
    """Poll predicate until True or raise TimeoutError."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(interval)
    raise TimeoutError(message)


def create_team(client: httpx.Client) -> str:
    """Create a team and return team_id."""
    resp = client.post("/teams/", json={"catalog_namespace": CATALOG_ENTRY_ID})
    assert resp.status_code == 201, f"Expected 201, got {resp.status_code}: {resp.text}"
    data: dict[str, Any] = resp.json()
    assert data["status"] == "running"
    team_id: str = data["team_id"]
    return team_id


def delete_team(client: httpx.Client, team_id: str) -> None:
    """Best-effort team cleanup."""
    try:
        client.delete(f"/teams/{team_id}")
    except Exception:  # noqa: BLE001
        pass


def send_message(client: httpx.Client, team_id: str, content: str = "hello") -> None:
    """Send a message to a team."""
    resp = client.post(f"/teams/{team_id}/message", json={"content": content})
    assert resp.status_code == 204, f"Expected 204, got {resp.status_code}: {resp.text}"


def get_events(client: httpx.Client, team_id: str) -> list[dict[str, Any]]:
    """Fetch events from a team."""
    resp = client.get(f"/teams/{team_id}/events")
    assert resp.status_code == 200
    events: list[dict[str, Any]] = resp.json()["events"]
    return events


def has_manager_response(events: list[dict[str, Any]]) -> bool:
    """Check if @Manager has responded with content."""
    for ev_wrapper in events:
        ev = ev_wrapper.get("event", {})
        if not isinstance(ev, dict):
            continue
        model = ev.get("__model__", "")
        short = model.rsplit(".", 1)[-1] if model else ""
        if short != "SentMessage":
            continue
        sender = ev.get("sender", {})
        if not isinstance(sender, dict):
            continue
        if sender.get("name") != "@Manager":
            continue
        msg = ev.get("message", {})
        if isinstance(msg, dict) and isinstance(msg.get("content"), str) and msg["content"]:
            return True
    return False


def wait_for_manager_response(
    client: httpx.Client,
    team_id: str,
    timeout: float = 60.0,
) -> list[dict[str, Any]]:
    """Poll events until @Manager responds."""
    events: list[dict[str, Any]] = []

    def _check() -> bool:
        nonlocal events
        events = get_events(client, team_id)
        return has_manager_response(events)

    poll_until(
        _check, timeout=timeout, interval=1.0, message="Timed out waiting for @Manager LLM response"
    )
    return events


# ---------------------------------------------------------------------------
# Session-scoped fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session", autouse=True)
def _load_dotenv() -> None:
    """Load .env once per session (makes OPENAI_API_KEY available)."""
    load_dotenv(_PROJECT_ROOT / ".env")


@pytest.fixture(scope="session")
def e2e_api_key(_load_dotenv: None) -> str:
    """Return OPENAI_API_KEY or skip all E2E tests."""
    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        pytest.skip("OPENAI_API_KEY not set — required for E2E tests")
    return key


@pytest.fixture(scope="session")
def e2e_server_url() -> str:
    """Return the server URL (configurable via AKGENTIC_SERVER_URL)."""
    return os.environ.get("AKGENTIC_SERVER_URL", "http://localhost:8010")


@pytest.fixture(scope="session")
def e2e_ws_url(e2e_server_url: str) -> str:
    """Return the WebSocket URL derived from the server URL."""
    return e2e_server_url.replace("http://", "ws://").replace("https://", "wss://")


@pytest.fixture(scope="session")
def e2e_server_ready(e2e_server_url: str, e2e_api_key: str) -> str:
    """Verify the server is reachable, skip all E2E tests if not."""
    try:
        resp = httpx.get(f"{e2e_server_url}/teams/", timeout=5.0)
        if resp.status_code not in (200, 401, 403):
            pytest.skip(f"Server at {e2e_server_url} returned unexpected status {resp.status_code}")
    except httpx.ConnectError:
        pytest.skip(f"Server not reachable at {e2e_server_url} — start it before running E2E tests")
    return e2e_server_url


@pytest.fixture(scope="session")
def e2e_http_client(e2e_server_ready: str) -> Generator[httpx.Client, None, None]:
    """Session-scoped httpx.Client with base_url set to the running server."""
    client = httpx.Client(base_url=e2e_server_ready, timeout=30.0)
    yield client
    client.close()
