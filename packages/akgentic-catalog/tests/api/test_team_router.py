"""Tests for the team catalog API router."""

from __future__ import annotations

from fastapi.testclient import TestClient

from akgentic.catalog.models.team import TeamMemberSpec
from tests.conftest import make_agent, make_team


class TestTeamRouter:
    """AC-4: Team router CRUD and search endpoints."""

    def _seed_agent(self, client: TestClient, id: str = "agent-1") -> None:
        """Create a prerequisite agent so team references resolve."""
        agent = make_agent(id=id, name=id)
        client.post("/api/agents/", json=agent.model_dump())

    def test_create_returns_201(self, client: TestClient) -> None:
        """POST /api/teams creates entry and returns 201."""
        self._seed_agent(client)
        entry = make_team(id="team-1")
        resp = client.post("/api/teams/", json=entry.model_dump())
        assert resp.status_code == 201
        assert resp.json()["id"] == "team-1"

    def test_list_returns_entries(self, client: TestClient) -> None:
        """GET /api/teams lists all entries."""
        self._seed_agent(client, "a1")
        self._seed_agent(client, "a2")
        t1 = make_team(
            id="t1",
            entry_point="a1",
            members=[TeamMemberSpec(agent_id="a1")],
        )
        t2 = make_team(
            id="t2",
            entry_point="a2",
            members=[TeamMemberSpec(agent_id="a2")],
        )
        client.post("/api/teams/", json=t1.model_dump())
        client.post("/api/teams/", json=t2.model_dump())
        resp = client.get("/api/teams/")
        assert resp.status_code == 200
        assert len(resp.json()) == 2

    def test_get_by_id(self, client: TestClient) -> None:
        """GET /api/teams/{id} returns entry."""
        self._seed_agent(client)
        client.post("/api/teams/", json=make_team(id="t1").model_dump())
        resp = client.get("/api/teams/t1")
        assert resp.status_code == 200
        assert resp.json()["id"] == "t1"

    def test_get_nonexistent_returns_404(self, client: TestClient) -> None:
        """GET /api/teams/{id} returns 404 for missing entry."""
        resp = client.get("/api/teams/missing")
        assert resp.status_code == 404

    def test_search_with_query(self, client: TestClient) -> None:
        """POST /api/teams/search returns filtered results."""
        self._seed_agent(client)
        client.post("/api/teams/", json=make_team(id="t1", name="Alpha Team").model_dump())
        resp = client.post("/api/teams/search", json={"id": "t1"})
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["id"] == "t1"

    def test_search_empty_results(self, client: TestClient) -> None:
        """POST /api/teams/search returns empty array when no matches."""
        resp = client.post("/api/teams/search", json={"id": "nonexistent"})
        assert resp.status_code == 200
        assert resp.json() == []

    def test_update_entry(self, client: TestClient) -> None:
        """PUT /api/teams/{id} updates and returns updated entry."""
        self._seed_agent(client)
        client.post("/api/teams/", json=make_team(id="t1", name="Old Name").model_dump())
        updated = make_team(id="t1", name="New Name")
        resp = client.put("/api/teams/t1", json=updated.model_dump())
        assert resp.status_code == 200
        assert resp.json()["name"] == "New Name"

    def test_update_id_mismatch_returns_409(self, client: TestClient) -> None:
        """PUT /api/teams/{id} with mismatched body id returns 409."""
        self._seed_agent(client)
        client.post("/api/teams/", json=make_team(id="t1").model_dump())
        mismatched = make_team(id="t2")
        resp = client.put("/api/teams/t1", json=mismatched.model_dump())
        assert resp.status_code == 409

    def test_update_nonexistent_returns_404(self, client: TestClient) -> None:
        """PUT /api/teams/{id} returns 404 for missing entry."""
        self._seed_agent(client)
        entry = make_team(id="missing")
        resp = client.put("/api/teams/missing", json=entry.model_dump())
        assert resp.status_code == 404

    def test_delete_returns_204(self, client: TestClient) -> None:
        """DELETE /api/teams/{id} returns 204."""
        self._seed_agent(client)
        client.post("/api/teams/", json=make_team(id="t1").model_dump())
        resp = client.delete("/api/teams/t1")
        assert resp.status_code == 204

    def test_delete_nonexistent_returns_404(self, client: TestClient) -> None:
        """DELETE /api/teams/{id} returns 404 for missing entry."""
        resp = client.delete("/api/teams/missing")
        assert resp.status_code == 404

    def test_create_duplicate_returns_409(self, client: TestClient) -> None:
        """POST /api/teams with duplicate id returns 409."""
        self._seed_agent(client)
        entry = make_team(id="t1")
        client.post("/api/teams/", json=entry.model_dump())
        resp = client.post("/api/teams/", json=entry.model_dump())
        assert resp.status_code == 409
        assert "errors" in resp.json()

    def test_search_by_agent_id(self, client: TestClient) -> None:
        """POST /api/teams/search by agent_id returns matching teams."""
        self._seed_agent(client)
        client.post("/api/teams/", json=make_team(id="t1").model_dump())
        resp = client.post("/api/teams/search", json={"agent_id": "agent-1"})
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["id"] == "t1"
