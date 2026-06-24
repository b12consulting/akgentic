"""End-to-end tests for the single ServerError -> HTTP handler via POST /teams.

A stub ``PlacementStrategy`` raising a placement error is swapped into the
community services before building the app, so ``POST /teams`` drives the
status/code/Retry-After mapping infra owns. Community's ``LocalPlacement``
never naturally exhausts capacity, so the stub is the only way to exercise
these branches (ADR-031 §Decision 4 / §Validation).
"""

from __future__ import annotations

import uuid
from collections.abc import Generator

import pytest
from akgentic.infra.protocols.placement import (
    NoCapacityError,
    WorkerRejectedError,
)
from akgentic.infra.server.app import create_app
from akgentic.infra.server.deps import CommunityServices
from akgentic.infra.server.settings import CommunitySettings
from fastapi.testclient import TestClient


class _RaisingPlacement:
    """Stub PlacementStrategy whose create_team raises a fixed placement error."""

    def __init__(self, error: Exception) -> None:
        self._error = error
        self._instance_id = uuid.uuid4()

    @property
    def instance_id(self) -> uuid.UUID:
        return self._instance_id

    def create_team(
        self,
        team_card: object,
        user_id: str,
        user_email: str = "",
        team_id: uuid.UUID | None = None,
        catalog_namespace: str | None = None,
    ) -> object:
        raise self._error


def _client_with_placement(
    services: CommunityServices,
    settings: CommunitySettings,
    error: Exception,
) -> TestClient:
    """Swap a raising placement stub into the services, then build the app."""
    services.placement = _RaisingPlacement(error)  # type: ignore[assignment]
    return TestClient(create_app(services, settings))


@pytest.fixture()
def no_capacity_client(
    community_services: CommunityServices,
    seeded_settings: CommunitySettings,
) -> Generator[TestClient, None, None]:
    """Client whose placement raises NoCapacityError."""
    yield _client_with_placement(
        community_services,
        seeded_settings,
        NoCapacityError("No worker available with capacity to place team"),
    )


@pytest.fixture()
def worker_rejected_client(
    community_services: CommunityServices,
    seeded_settings: CommunitySettings,
) -> Generator[TestClient, None, None]:
    """Client whose placement raises WorkerRejectedError."""
    yield _client_with_placement(
        community_services,
        seeded_settings,
        WorkerRejectedError("Worker returned 500 from create"),
    )


def test_no_capacity_maps_to_503(no_capacity_client: TestClient) -> None:
    """AC #13: NoCapacityError -> 503 + Retry-After + code=no_worker_capacity."""
    resp = no_capacity_client.post("/teams/", json={"catalog_namespace": "test-team"})
    assert resp.status_code == 503
    assert "retry-after" in {k.lower() for k in resp.headers}
    body = resp.json()
    assert body["code"] == "no_worker_capacity"
    assert isinstance(body["detail"], str) and body["detail"]


def test_worker_rejected_maps_to_502(worker_rejected_client: TestClient) -> None:
    """AC #14: WorkerRejectedError -> 502 + code=worker_rejected."""
    resp = worker_rejected_client.post("/teams/", json={"catalog_namespace": "test-team"})
    assert resp.status_code == 502
    body = resp.json()
    assert body["code"] == "worker_rejected"
    assert isinstance(body["detail"], str) and body["detail"]
