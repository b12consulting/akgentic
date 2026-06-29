"""Regression test: ``upload_workspace_file`` must not stall the event loop.

The ``POST /workspace/{team_id}/file`` route does its blocking work off the
event loop in two ways: the synchronous ``require_team_access`` authorization
dependency is offloaded to the threadpool by FastAPI itself, and
``Filesystem.write`` is offloaded via ``asyncio.to_thread`` (ADR-026 / Story
29.1). This module pins both in place: while the upload is suspended on a
deliberately slow stub of one of those two sync calls, a concurrent
``GET /readiness`` request MUST return in under 100 ms, demonstrating the
event loop is not blocked.

The two cases (slow team-access gate and slow ``Filesystem.write``) give
symmetric coverage of both offloads. If a future refactor makes either run
inline on the loop, the corresponding case fails with a ``< 0.1s`` latency
assertion well outside any reasonable CI-scheduler / container-startup timing
jitter (the slow stub sleeps for 2 s — a 20x margin from the 100 ms ceiling).

See also the sibling pattern in ``akgentic-infra-department`` Epic 13 /
Story 13.2 (same bug class, different submodule).
"""

from __future__ import annotations

import concurrent.futures
import time
import uuid
from collections.abc import Callable

import pytest
from akgentic.tool.workspace import Filesystem
from fastapi.testclient import TestClient

from akgentic.infra.server.services.team_service import TeamService
from akgentic.infra.server.settings import ServerSettings


@pytest.fixture()
def team_for_upload(client: TestClient, seeded_settings: ServerSettings) -> uuid.UUID:
    """Create a team via REST and ensure its workspace directory exists.

    Mirrors the local fixture in ``test_workspace_routes.py``; duplicated
    here so this module is self-contained and does not depend on import
    order.
    """
    resp = client.post("/teams/", json={"catalog_namespace": "test-team"})
    assert resp.status_code == 201
    team_id = uuid.UUID(resp.json()["team_id"])
    ws_root = seeded_settings.workspaces_root / str(team_id)
    ws_root.mkdir(parents=True, exist_ok=True)
    return team_id


def _patch_slow_team_access(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make the ``require_team_access`` gate slow by stalling its team lookup.

    The gate is a synchronous FastAPI dependency that calls
    ``TeamService.get_team``. Wrapping that lookup with a 2 s sleep (then
    delegating to the original, so the owner check still passes and the
    upload returns 201) proves FastAPI offloads the sync gate to the
    threadpool instead of running it on the event loop.
    """
    original = TeamService.get_team

    def slow_get_team(self: TeamService, team_id: uuid.UUID) -> object:
        time.sleep(2.0)
        return original(self, team_id)

    monkeypatch.setattr(TeamService, "get_team", slow_get_team)


def _patch_slow_filesystem_write(monkeypatch: pytest.MonkeyPatch) -> None:
    """Patch ``Filesystem.write`` to sleep for 2 s (and skip the real write).

    ``ws.write(path, data)`` dispatches through the class attribute, so a
    class-level ``setattr`` intercepts every existing and future
    ``Filesystem`` instance. The handler returns 201 because no
    exception was raised; the file is not actually written to disk
    (the assertion only requires the response shape, AC #7).
    """

    def slow_write(self: Filesystem, path: str, data: bytes) -> None:
        time.sleep(2.0)

    monkeypatch.setattr(Filesystem, "write", slow_write)


@pytest.mark.parametrize(
    ("case_name", "patch_fn"),
    [
        ("slow require_team_access gate", _patch_slow_team_access),
        ("slow Filesystem.write", _patch_slow_filesystem_write),
    ],
)
def test_upload_does_not_block_event_loop(
    client: TestClient,
    team_for_upload: uuid.UUID,
    monkeypatch: pytest.MonkeyPatch,
    case_name: str,
    patch_fn: Callable[[pytest.MonkeyPatch], None],
) -> None:
    """A concurrent ``/readiness`` probe must return in <100 ms while a
    slow ``upload_workspace_file`` request is in flight.

    The two parametrised cases stub each off-loop hand-off independently —
    the synchronous ``require_team_access`` gate (offloaded by FastAPI) and
    the ``asyncio.to_thread(Filesystem.write)`` call. Both must satisfy the
    latency ceiling — if either runs inline on the loop, the corresponding
    case fails with a message identifying which hand-off regressed.
    """
    patch_fn(monkeypatch)

    payload = b"slow data"
    team_id = team_for_upload

    def fire_slow_upload() -> object:
        return client.post(
            f"/workspace/{team_id}/file",
            data={"path": "slow.txt"},
            files={"file": ("slow.txt", payload, "text/plain")},
        )

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        slow_future = executor.submit(fire_slow_upload)

        # Settle so the slow request reaches its asyncio.to_thread
        # dispatch and the upload coroutine is parked on a worker
        # thread. Fixed 50 ms; do NOT poll (per AC #6).
        time.sleep(0.05)

        # Probe runs on the main test thread, racing the in-flight
        # upload. Sample latency around the single sync call.
        start = time.perf_counter()
        probe_resp = client.get("/readiness")
        elapsed_s = time.perf_counter() - start

        assert probe_resp.status_code == 200
        assert elapsed_s < 0.1, (
            f"GET /readiness took {elapsed_s:.3f}s while {case_name} was in flight; "
            "expected < 0.1s (loop is stalled by sync work inside the upload handler)"
        )

        slow_resp = slow_future.result(timeout=10.0)

    # Slow upload still completes normally — the offload allowed the
    # request to finish, not just unblocked the loop.
    assert slow_resp.status_code == 201, (  # type: ignore[attr-defined]
        f"slow upload returned {slow_resp.status_code} for case {case_name!r}"  # type: ignore[attr-defined]
    )
    body = slow_resp.json()  # type: ignore[attr-defined]
    assert body["path"] == "slow.txt"
    assert body["size"] == len(payload)


def test_upload_with_workspace_id_does_not_block_event_loop(
    client: TestClient,
    team_for_upload: uuid.UUID,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A selector-bearing upload (Story 33.1) keeps the same offload guarantee.

    Passing ``?workspace_id=alt-ws`` only changes which directory the
    ``Filesystem`` is rooted at; the two ``asyncio.to_thread`` offloads in
    ``upload_workspace_file`` are unchanged. While a deliberately slow
    ``Filesystem.write`` is in flight, a concurrent ``/readiness`` probe must
    still return in under 100 ms, and the upload must still return 201.
    """
    _patch_slow_filesystem_write(monkeypatch)

    payload = b"slow alt data"
    team_id = team_for_upload

    def fire_slow_upload() -> object:
        return client.post(
            f"/workspace/{team_id}/file",
            params={"workspace_id": "alt-ws"},
            data={"path": "slow.txt"},
            files={"file": ("slow.txt", payload, "text/plain")},
        )

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        slow_future = executor.submit(fire_slow_upload)
        time.sleep(0.05)

        start = time.perf_counter()
        probe_resp = client.get("/readiness")
        elapsed_s = time.perf_counter() - start

        assert probe_resp.status_code == 200
        assert elapsed_s < 0.1, (
            f"GET /readiness took {elapsed_s:.3f}s while a workspace_id-bearing upload "
            "was in flight; expected < 0.1s (loop is stalled by sync work in the handler)"
        )

        slow_resp = slow_future.result(timeout=10.0)

    assert slow_resp.status_code == 201, (  # type: ignore[attr-defined]
        f"selector upload returned {slow_resp.status_code}"  # type: ignore[attr-defined]
    )
    body = slow_resp.json()  # type: ignore[attr-defined]
    assert body["path"] == "slow.txt"
    assert body["size"] == len(payload)
