"""Tests for GET /teams/{id}/agent-states using FastAPI TestClient.

Covers the thin DB read of the per-agent snapshot store: snapshots are
returned exactly as persisted (no liveness filtering, no name->UUID
resolution), for running and stopped teams alike (Epic 35 / story 35-1).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from akgentic.agent.config import AgentState
from akgentic.team.models import AgentStateSnapshot
from fastapi.testclient import TestClient

from akgentic.infra.server.deps import CommunityServices


def _seed_snapshot(
    services: CommunityServices,
    team_id: uuid.UUID,
    *,
    agent_id: str,
    name: str | None,
    backstory: str,
) -> AgentStateSnapshot:
    """Persist one agent-state snapshot directly into the team's snapshot store."""
    snapshot = AgentStateSnapshot(
        team_id=team_id,
        agent_id=agent_id,
        name=name,
        state=AgentState(backstory=backstory),
        updated_at=datetime.now(UTC),
    )
    services.event_store.save_agent_state(snapshot)
    return snapshot


def test_get_agent_states_success(
    client: TestClient, community_services: CommunityServices
) -> None:
    """Returns one entry per persisted snapshot with fields echoed as stored."""
    create_resp = client.post("/teams/", json={"catalog_namespace": "test-team"})
    team_id = uuid.UUID(create_resp.json()["team_id"])
    agent_uuid = str(uuid.uuid4())
    _seed_snapshot(
        community_services,
        team_id,
        agent_id=agent_uuid,
        name="@Manager",
        backstory="You coordinate the team.",
    )

    resp = client.get(f"/teams/{team_id}/agent-states")

    assert resp.status_code == 200
    data = resp.json()
    assert len(data["states"]) == 1
    entry = data["states"][0]
    assert entry["agent_id"] == agent_uuid
    assert entry["name"] == "@Manager"
    assert entry["state"]["backstory"] == "You coordinate the team."
    assert entry["updated_at"] is not None


def test_get_agent_states_legacy_name_keyed_snapshot(
    client: TestClient, community_services: CommunityServices
) -> None:
    """A pre-Epic-23 snapshot (agent_id holds a name, name is None) is passed through as-is."""
    create_resp = client.post("/teams/", json={"catalog_namespace": "test-team"})
    team_id = uuid.UUID(create_resp.json()["team_id"])
    _seed_snapshot(
        community_services,
        team_id,
        agent_id="@Manager",
        name=None,
        backstory="legacy backstory",
    )

    resp = client.get(f"/teams/{team_id}/agent-states")

    assert resp.status_code == 200
    entry = resp.json()["states"][0]
    assert entry["agent_id"] == "@Manager"
    assert entry["name"] is None


def test_get_agent_states_stopped_team_returns_snapshots(
    client: TestClient, community_services: CommunityServices
) -> None:
    """The load-bearing case: snapshots are returned regardless of agent liveness.

    A stopped team has no live (Start - Stop) agent set, yet the endpoint must
    still return its persisted snapshots — there is no live-set filtering.
    """
    create_resp = client.post("/teams/", json={"catalog_namespace": "test-team"})
    team_id = uuid.UUID(create_resp.json()["team_id"])
    agent_uuid = str(uuid.uuid4())
    _seed_snapshot(
        community_services,
        team_id,
        agent_id=agent_uuid,
        name="@Manager",
        backstory="still here after stop",
    )
    client.post(f"/teams/{team_id}/stop")

    resp = client.get(f"/teams/{team_id}/agent-states")

    assert resp.status_code == 200
    states = resp.json()["states"]
    assert [s["agent_id"] for s in states] == [agent_uuid]
    assert states[0]["state"]["backstory"] == "still here after stop"


def test_get_agent_states_empty_is_200(client: TestClient) -> None:
    """An existing team with no persisted snapshots returns 200 with an empty list."""
    create_resp = client.post("/teams/", json={"catalog_namespace": "test-team"})
    team_id = create_resp.json()["team_id"]

    resp = client.get(f"/teams/{team_id}/agent-states")

    assert resp.status_code == 200
    assert resp.json()["states"] == []


def test_get_agent_states_unknown_team_is_404(client: TestClient) -> None:
    """An unknown team_id returns 404 with detail 'Team not found'."""
    resp = client.get(f"/teams/{uuid.uuid4()}/agent-states")

    assert resp.status_code == 404
    assert resp.json()["detail"] == "Team not found"
