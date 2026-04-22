"""Tests for workspace file access endpoints."""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from akgentic.infra.server.settings import ServerSettings


@pytest.fixture()
def team_with_workspace(client: TestClient, seeded_settings: ServerSettings) -> uuid.UUID:
    """Create a team via REST and seed workspace files."""
    resp = client.post("/teams/", json={"catalog_entry_id": "test-team"})
    assert resp.status_code == 201
    team_id = uuid.UUID(resp.json()["team_id"])
    ws_root = seeded_settings.workspaces_root / str(team_id)
    ws_root.mkdir(parents=True, exist_ok=True)
    (ws_root / "output.txt").write_text("hello world")
    (ws_root / "subdir").mkdir()
    (ws_root / "subdir" / "data.json").write_text('{"key": "value"}')
    return team_id


# --- Tree listing tests ---


def test_workspace_tree(client: TestClient, team_with_workspace: uuid.UUID) -> None:
    """GET /workspace/{team_id}/tree returns file listing."""
    resp = client.get(f"/workspace/{team_with_workspace}/tree")
    assert resp.status_code == 200
    body = resp.json()
    assert body["team_id"] == str(team_with_workspace)
    names = [e["name"] for e in body["entries"]]
    assert "output.txt" in names
    assert "subdir" in names


def test_workspace_tree_not_found(client: TestClient) -> None:
    """GET /workspace/{team_id}/tree returns 404 for non-existent team."""
    fake_id = uuid.uuid4()
    resp = client.get(f"/workspace/{fake_id}/tree")
    assert resp.status_code == 404


# --- File read tests ---


def test_workspace_file_read(client: TestClient, team_with_workspace: uuid.UUID) -> None:
    """GET /workspace/{team_id}/file returns file content."""
    resp = client.get(f"/workspace/{team_with_workspace}/file", params={"path": "output.txt"})
    assert resp.status_code == 200
    assert resp.content == b"hello world"
    assert "Content-Disposition" in resp.headers


def test_workspace_file_not_found(client: TestClient, team_with_workspace: uuid.UUID) -> None:
    """GET /workspace/{team_id}/file returns 404 for non-existent file."""
    resp = client.get(
        f"/workspace/{team_with_workspace}/file", params={"path": "does-not-exist.txt"}
    )
    assert resp.status_code == 404


def test_workspace_file_size_limit(
    client: TestClient,
    team_with_workspace: uuid.UUID,
    seeded_settings: ServerSettings,
) -> None:
    """GET /workspace/{team_id}/file returns 413 for files exceeding 10 MB."""
    ws_root = seeded_settings.workspaces_root / str(team_with_workspace)
    big_file = ws_root / "huge.bin"
    big_file.write_bytes(b"\x00" * (10_485_760 + 1))
    resp = client.get(f"/workspace/{team_with_workspace}/file", params={"path": "huge.bin"})
    assert resp.status_code == 413


def test_workspace_file_traversal_attack(
    client: TestClient, team_with_workspace: uuid.UUID
) -> None:
    """GET /workspace/{team_id}/file rejects path traversal attempts with 403."""
    resp = client.get(f"/workspace/{team_with_workspace}/file", params={"path": "../../etc/passwd"})
    assert resp.status_code == 403


# --- File upload tests ---


def test_workspace_file_upload(client: TestClient, team_with_workspace: uuid.UUID) -> None:
    """POST /workspace/{team_id}/file uploads and stores file."""
    resp = client.post(
        f"/workspace/{team_with_workspace}/file",
        data={"path": "uploaded.txt"},
        files={"file": ("uploaded.txt", b"upload content", "text/plain")},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["path"] == "uploaded.txt"
    assert body["size"] == len(b"upload content")

    # Verify file can be read back
    read_resp = client.get(
        f"/workspace/{team_with_workspace}/file", params={"path": "uploaded.txt"}
    )
    assert read_resp.status_code == 200
    assert read_resp.content == b"upload content"


def test_workspace_file_upload_team_not_found(client: TestClient) -> None:
    """POST /workspace/{team_id}/file returns 404 for non-existent team."""
    fake_id = uuid.uuid4()
    resp = client.post(
        f"/workspace/{fake_id}/file",
        data={"path": "test.txt"},
        files={"file": ("test.txt", b"data", "text/plain")},
    )
    assert resp.status_code == 404


def test_workspace_file_upload_size_limit(
    client: TestClient, team_with_workspace: uuid.UUID
) -> None:
    """POST /workspace/{team_id}/file returns 413 for uploads exceeding 10 MB."""
    big_data = b"\x00" * (10_485_760 + 1)
    resp = client.post(
        f"/workspace/{team_with_workspace}/file",
        data={"path": "huge-upload.bin"},
        files={"file": ("huge-upload.bin", big_data, "application/octet-stream")},
    )
    assert resp.status_code == 413


def test_workspace_file_upload_traversal_attack(
    client: TestClient, team_with_workspace: uuid.UUID
) -> None:
    """POST /workspace/{team_id}/file rejects path traversal with 403."""
    resp = client.post(
        f"/workspace/{team_with_workspace}/file",
        data={"path": "../../../etc/evil"},
        files={"file": ("evil.txt", b"malicious", "text/plain")},
    )
    assert resp.status_code == 403
