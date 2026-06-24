"""Regression test: ``upload_workspace_file`` must not stall the event loop.

The ``POST /workspace/{team_id}/file`` route invokes the synchronous
``_validate_team`` and ``Filesystem.write`` calls via ``asyncio.to_thread``
(per ADR-026 / Story 29.1). This module pins that offload behaviour in
place with an executable regression guard: while the upload coroutine is
suspended on a deliberately slow stub of one of those two sync calls, a
concurrent ``GET /readiness`` request MUST return in under 100 ms,
demonstrating the FastAPI event loop is not blocked.

The two cases (slow ``_validate_team`` and slow ``Filesystem.write``)
provide symmetric coverage of both ``to_thread`` call sites. If a future
refactor reverts either wrapping back to a direct sync call, the
corresponding case in this test fails with a ``< 0.1s`` latency
assertion well outside any reasonable CI-scheduler / container-startup
timing jitter (the slow stub sleeps for 2 s — a 20x margin from the
100 ms ceiling).

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

import akgentic.infra.server.routes.workspace as workspace_module
from akgentic.infra.server.settings import ServerSettings


@pytest.fixture()
def team_for_upload(client: TestClient, seeded_settings: ServerSettings) -> uuid.UUID:
    """Create a team via REST and ensure its workspace directory exists.

    Mirrors the local fixture in ``test_workspace_routes.py``; duplicated
    here so this module is self-contained and does not depend on import
    order.
    """
    resp = client.post("/teams/", json={"catalog_entry_id": "test-team"})
    assert resp.status_code == 201
    team_id = uuid.UUID(resp.json()["team_id"])
    ws_root = seeded_settings.workspaces_root / str(team_id)
    ws_root.mkdir(parents=True, exist_ok=True)
    return team_id


def _patch_slow_validate_team(monkeypatch: pytest.MonkeyPatch) -> None:
    """Patch the module-level ``_validate_team`` to sleep for 2 s.

    ``asyncio.to_thread(_validate_team, ...)`` reads the module-level
    name at call time, so patching the attribute on the module object
    intercepts every dispatch for the test's lifetime.
    """

    def slow_validate(team_id: uuid.UUID, request: object) -> None:
        time.sleep(2.0)

    monkeypatch.setattr(workspace_module, "_validate_team", slow_validate)


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
        ("slow _validate_team", _patch_slow_validate_team),
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

    The two parametrised cases stub each of the two ``asyncio.to_thread``
    call sites independently. Both must satisfy the latency ceiling — if
    either ``to_thread`` wrapping is ever reverted, the corresponding
    case fails with the AC #4 message identifying which call site
    regressed.
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
