"""Integration test — DELETE /teams/{id} removes the team's workspace dir.

Story 24.1: ``TeamService.delete_team`` performs a best-effort recursive
removal of ``{workspaces_root}/{team_id}/`` as its final step. This test
exercises the full HTTP round-trip via ``TestClient`` against the smoke
(TestModel) app — no ``OPENAI_API_KEY`` required.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ._helpers import CATALOG_ENTRY_ID

pytestmark = [pytest.mark.integration, pytest.mark.smoke]


def _create_team(client: TestClient) -> str:
    """POST /teams and return the team_id."""
    resp = client.post("/teams/", json={"catalog_namespace": CATALOG_ENTRY_ID})
    assert resp.status_code == 201
    data = resp.json()
    assert data["status"] == "running"
    return str(data["team_id"])


def test_delete_team_removes_workspace_directory(
    smoke_client: TestClient,
    smoke_settings: object,
) -> None:
    """DELETE /teams/{id} returns 204 and removes the workspace directory."""
    workspaces_root = Path(smoke_settings.workspaces_root)  # type: ignore[attr-defined]
    team_id = _create_team(smoke_client)

    # Populate the team's workspace directory on disk, mirroring what the
    # Filesystem tool does when an agent writes a workspace file.
    team_dir = workspaces_root / team_id
    team_dir.mkdir(parents=True, exist_ok=True)
    (team_dir / "file.txt").write_text("workspace content")
    assert team_dir.exists()

    resp = smoke_client.delete(f"/teams/{team_id}")
    assert resp.status_code == 204

    # The workspace directory and its contents are gone.
    assert not team_dir.exists()

    # The team is no longer listed.
    listing = smoke_client.get("/teams/")
    assert listing.status_code == 200
    team_ids = {str(t["team_id"]) for t in listing.json()["teams"]}
    assert team_id not in team_ids
