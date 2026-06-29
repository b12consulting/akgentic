"""Tests for workspace file access endpoints."""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from akgentic.infra.server.auth import RequestUser, get_request_user
from akgentic.infra.server.settings import ServerSettings


@pytest.fixture()
def team_with_workspace(client: TestClient, seeded_settings: ServerSettings) -> uuid.UUID:
    """Create a team via REST and seed workspace files."""
    resp = client.post("/teams/", json={"catalog_namespace": "test-team"})
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


def test_workspace_tree_traversal_attack(
    client: TestClient, team_with_workspace: uuid.UUID
) -> None:
    """GET /workspace/{team_id}/tree rejects in-root path traversal with 403."""
    resp = client.get(
        f"/workspace/{team_with_workspace}/tree", params={"path": "../../etc"}
    )
    assert resp.status_code == 403


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


# --- workspace_id selector tests (Story 33.1) ---

# Values that _validate_workspace_id must reject with HTTP 400: empty, the dot
# segments, anything containing a path separator, absolute paths, and an
# over-length (129-char) value.
_REJECTED_WORKSPACE_IDS = [
    "../x",
    "a/b",
    "a\\b",
    "/abs",
    "..",
    ".",
    "",
    "a" * 129,
]


def test_workspace_tree_honours_selector(
    client: TestClient,
    team_with_workspace: uuid.UUID,
    seeded_settings: ServerSettings,
) -> None:
    """GET .../tree with ?workspace_id=alt-ws lists <root>/alt-ws, isolated from the team dir."""
    alt_root = seeded_settings.workspaces_root / "alt-ws"
    alt_root.mkdir(parents=True, exist_ok=True)
    (alt_root / "alt-only.txt").write_text("alt content")

    resp = client.get(
        f"/workspace/{team_with_workspace}/tree", params={"workspace_id": "alt-ws"}
    )
    assert resp.status_code == 200
    names = [e["name"] for e in resp.json()["entries"]]
    assert "alt-only.txt" in names
    # Isolation: the team directory's seeded files are NOT visible under alt-ws.
    assert "output.txt" not in names
    assert "subdir" not in names


def test_workspace_file_read_honours_selector(
    client: TestClient,
    team_with_workspace: uuid.UUID,
    seeded_settings: ServerSettings,
) -> None:
    """GET .../file with ?workspace_id=alt-ws reads from <root>/alt-ws."""
    alt_root = seeded_settings.workspaces_root / "alt-ws"
    alt_root.mkdir(parents=True, exist_ok=True)
    (alt_root / "alt.txt").write_text("from alt")

    resp = client.get(
        f"/workspace/{team_with_workspace}/file",
        params={"path": "alt.txt", "workspace_id": "alt-ws"},
    )
    assert resp.status_code == 200
    assert resp.content == b"from alt"

    # Isolation: the same filename does NOT resolve in the team directory.
    team_resp = client.get(
        f"/workspace/{team_with_workspace}/file", params={"path": "alt.txt"}
    )
    assert team_resp.status_code == 404


def test_workspace_file_upload_honours_selector(
    client: TestClient,
    team_with_workspace: uuid.UUID,
    seeded_settings: ServerSettings,
) -> None:
    """POST .../file with ?workspace_id=alt-ws writes to <root>/alt-ws, isolated from team dir."""
    resp = client.post(
        f"/workspace/{team_with_workspace}/file",
        params={"workspace_id": "alt-ws"},
        data={"path": "uploaded-alt.txt"},
        files={"file": ("uploaded-alt.txt", b"alt upload", "text/plain")},
    )
    assert resp.status_code == 201
    assert resp.json()["path"] == "uploaded-alt.txt"

    # The write landed under <root>/alt-ws ...
    alt_file = seeded_settings.workspaces_root / "alt-ws" / "uploaded-alt.txt"
    assert alt_file.exists()
    assert alt_file.read_bytes() == b"alt upload"

    # ... and NOT under the team directory (isolation both ways).
    team_file = seeded_settings.workspaces_root / str(team_with_workspace) / "uploaded-alt.txt"
    assert not team_file.exists()
    read_back = client.get(
        f"/workspace/{team_with_workspace}/file", params={"path": "uploaded-alt.txt"}
    )
    assert read_back.status_code == 404


@pytest.mark.parametrize("bad_value", _REJECTED_WORKSPACE_IDS)
def test_workspace_tree_rejects_bad_selector(
    client: TestClient,
    team_with_workspace: uuid.UUID,
    seeded_settings: ServerSettings,
    bad_value: str,
) -> None:
    """GET .../tree returns 400 for any malformed workspace_id and creates no stray dir."""
    before = set(seeded_settings.workspaces_root.iterdir())
    resp = client.get(
        f"/workspace/{team_with_workspace}/tree", params={"workspace_id": bad_value}
    )
    assert resp.status_code == 400
    # No directory was created or read outside the existing workspace roots.
    assert set(seeded_settings.workspaces_root.iterdir()) == before


@pytest.mark.parametrize("bad_value", _REJECTED_WORKSPACE_IDS)
def test_workspace_file_read_rejects_bad_selector(
    client: TestClient,
    team_with_workspace: uuid.UUID,
    seeded_settings: ServerSettings,
    bad_value: str,
) -> None:
    """GET .../file returns 400 for any malformed workspace_id and creates no stray dir."""
    before = set(seeded_settings.workspaces_root.iterdir())
    resp = client.get(
        f"/workspace/{team_with_workspace}/file",
        params={"path": "output.txt", "workspace_id": bad_value},
    )
    assert resp.status_code == 400
    assert set(seeded_settings.workspaces_root.iterdir()) == before


@pytest.mark.parametrize("bad_value", _REJECTED_WORKSPACE_IDS)
def test_workspace_file_upload_rejects_bad_selector(
    client: TestClient,
    team_with_workspace: uuid.UUID,
    seeded_settings: ServerSettings,
    bad_value: str,
) -> None:
    """POST .../file returns 400 for any malformed workspace_id and creates no stray dir."""
    before = set(seeded_settings.workspaces_root.iterdir())
    resp = client.post(
        f"/workspace/{team_with_workspace}/file",
        params={"workspace_id": bad_value},
        data={"path": "evil.txt"},
        files={"file": ("evil.txt", b"data", "text/plain")},
    )
    assert resp.status_code == 400
    assert set(seeded_settings.workspaces_root.iterdir()) == before


# --- Route-level authorization: path team_id (ADR-034 §Layered authz, AC1-AC5) ---


def _identity(app: FastAPI, user: RequestUser) -> TestClient:
    """A TestClient whose request-user seam resolves to ``user``."""
    app.dependency_overrides[get_request_user] = lambda: user
    return TestClient(app)


def _owned_team_with_file(owner_client: TestClient, ws_root_parent: Path) -> uuid.UUID:
    """Create a team via REST under the owner's identity and seed ``output.txt``."""
    resp = owner_client.post("/teams/", json={"catalog_namespace": "test-team"})
    assert resp.status_code == 201
    team_id = uuid.UUID(resp.json()["team_id"])
    ws_root = ws_root_parent / str(team_id)
    ws_root.mkdir(parents=True, exist_ok=True)
    (ws_root / "output.txt").write_text("hello world")
    return team_id


def test_workspace_routes_deny_non_owner_404(
    app: FastAPI, seeded_settings: ServerSettings
) -> None:
    """A non-owner non-admin gets 404 on tree/file/upload (no existence leak) — AC3."""
    owner = _identity(app, RequestUser(user_id="alice"))
    team_id = _owned_team_with_file(owner, seeded_settings.workspaces_root)
    # The owner reaches the route (sanity).
    assert owner.get(f"/workspace/{team_id}/tree").status_code == 200

    intruder = _identity(app, RequestUser(user_id="bob"))
    assert intruder.get(f"/workspace/{team_id}/tree").status_code == 404
    assert (
        intruder.get(f"/workspace/{team_id}/file", params={"path": "output.txt"}).status_code
        == 404
    )
    upload = intruder.post(
        f"/workspace/{team_id}/file",
        data={"path": "x.txt"},
        files={"file": ("x.txt", b"data", "text/plain")},
    )
    assert upload.status_code == 404
    app.dependency_overrides.clear()


def test_workspace_routes_admin_non_owner_allowed(
    app: FastAPI, seeded_settings: ServerSettings
) -> None:
    """An ``admin`` bypasses ownership on tree/file/upload — AC4."""
    owner = _identity(app, RequestUser(user_id="alice"))
    team_id = _owned_team_with_file(owner, seeded_settings.workspaces_root)

    admin = _identity(app, RequestUser(user_id="root", roles=["admin"]))
    assert admin.get(f"/workspace/{team_id}/tree").status_code == 200
    assert (
        admin.get(f"/workspace/{team_id}/file", params={"path": "output.txt"}).status_code == 200
    )
    upload = admin.post(
        f"/workspace/{team_id}/file",
        data={"path": "by-admin.txt"},
        files={"file": ("by-admin.txt", b"admin data", "text/plain")},
    )
    assert upload.status_code == 201
    app.dependency_overrides.clear()


def test_workspace_file_read_missing_team_404(client: TestClient) -> None:
    """GET .../file 404s for a non-existent team (now via the gate) — AC5."""
    fake_id = uuid.uuid4()
    resp = client.get(f"/workspace/{fake_id}/file", params={"path": "output.txt"})
    assert resp.status_code == 404


# --- ?workspace_id= foreign-team check (ADR-034 open question #5, AC7) ---


def test_workspace_id_foreign_team_is_404(
    app: FastAPI, seeded_settings: ServerSettings
) -> None:
    """A ?workspace_id= naming a foreign team's id is rejected with 404 — AC7."""
    alice = _identity(app, RequestUser(user_id="alice"))
    alice_team = _owned_team_with_file(alice, seeded_settings.workspaces_root)

    bob = _identity(app, RequestUser(user_id="bob"))
    bob_team = _owned_team_with_file(bob, seeded_settings.workspaces_root)
    # bob owns bob_team (path passes) but points workspace_id at alice's team id.
    resp = bob.get(
        f"/workspace/{bob_team}/tree", params={"workspace_id": str(alice_team)}
    )
    assert resp.status_code == 404
    app.dependency_overrides.clear()


def test_workspace_id_unknown_uuid_is_allowed(
    client: TestClient,
    team_with_workspace: uuid.UUID,
    seeded_settings: ServerSettings,
) -> None:
    """A ?workspace_id= UUID that names no team is a shared segment — allowed (AC7)."""
    stray = uuid.uuid4()
    alt_root = seeded_settings.workspaces_root / str(stray)
    alt_root.mkdir(parents=True, exist_ok=True)
    (alt_root / "shared.txt").write_text("shared content")
    resp = client.get(
        f"/workspace/{team_with_workspace}/tree", params={"workspace_id": str(stray)}
    )
    assert resp.status_code == 200
    names = [e["name"] for e in resp.json()["entries"]]
    assert "shared.txt" in names


def test_workspace_id_own_team_is_allowed(
    client: TestClient, team_with_workspace: uuid.UUID
) -> None:
    """A ?workspace_id= equal to the caller's own team id is allowed (AC7)."""
    resp = client.get(
        f"/workspace/{team_with_workspace}/tree",
        params={"workspace_id": str(team_with_workspace)},
    )
    assert resp.status_code == 200
    names = [e["name"] for e in resp.json()["entries"]]
    assert "output.txt" in names
