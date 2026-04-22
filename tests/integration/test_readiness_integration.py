"""Integration tests — /readiness endpoint (Story 14.5, AC #4).

Verifies the readiness probe returns correct status codes using the
real FastAPI app with TestModel LLM injection.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

pytestmark = [pytest.mark.smoke]


# ---------------------------------------------------------------------------
# AC #4 — /readiness returns 200 normally and 503 when draining
# ---------------------------------------------------------------------------


def test_readiness_200_on_healthy_app(
    smoke_client: TestClient,
) -> None:
    """GET /readiness returns 200 with status=ready on a healthy app."""
    resp = smoke_client.get("/readiness")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ready"}


def test_readiness_503_when_draining(
    smoke_app: FastAPI,
    smoke_client: TestClient,
) -> None:
    """GET /readiness returns 503 with status=draining when app is draining."""
    smoke_app.state.draining = True
    try:
        resp = smoke_client.get("/readiness")
        assert resp.status_code == 503
        assert resp.json() == {"status": "draining"}
    finally:
        smoke_app.state.draining = False


def test_readiness_transitions_during_lifecycle(
    smoke_app: FastAPI,
    smoke_client: TestClient,
) -> None:
    """Verify readiness transitions from 200 to 503 when draining is set."""
    # Initially ready
    resp = smoke_client.get("/readiness")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ready"

    # Set draining
    smoke_app.state.draining = True
    try:
        resp = smoke_client.get("/readiness")
        assert resp.status_code == 503
        assert resp.json()["status"] == "draining"
    finally:
        smoke_app.state.draining = False
