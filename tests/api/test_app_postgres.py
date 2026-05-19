"""End-to-end API round-trip tests for the Postgres backend.

Exercises ``create_app(backend="postgres", postgres_config=...)`` against
the shared Postgres testcontainer (fixture :func:`postgres_clean_dsn`
from ``tests/conftest.py``). Every test goes through the HTTP boundary
via :class:`fastapi.testclient.TestClient` so the full
``HTTP → FastAPI router → Catalog → PostgresEntryRepository`` stack is
covered end-to-end.

Skip discipline: ``fastapi`` is guarded via ``pytest.importorskip`` inside
each test body (module-level skip would also be acceptable, but Epic 22
matches the ``tests/v2/test_api_router.py`` pattern for cross-test
consistency). The Postgres testcontainer + DSN fixture skips cleanly when
the ``[postgres]`` extra or Docker is absent.
"""

from __future__ import annotations

from typing import Any

import pytest

_TEAM_TYPE = "akgentic.team.models.TeamCard"
_AGENT_TYPE = "akgentic.core.agent_card.AgentCard"


def _team_payload() -> dict[str, Any]:
    """Return a minimal valid ``TeamCard`` payload.

    ``AgentCard.role`` is a derived property (reads ``config.role``) rather
    than a declared field, so inlining ``"role": ...`` at the card level
    would be stripped by Pydantic on validation and break a strict
    round-trip equality check. Use ``config.role`` exclusively.
    """
    return {
        "name": "team",
        "description": "",
        "entry_point": {
            "card": {
                "description": "",
                "skills": [],
                "agent_class": "akgentic.core.agent.Akgent",
                "config": {"name": "entry", "role": "entry"},
            },
            "headcount": 1,
            "members": [],
        },
        "members": [],
        "agent_profiles": [],
    }


def _team_payload_with_nested_ref() -> dict[str, Any]:
    """Return a ``TeamCard`` payload whose ``entry_point.card`` is a ref marker.

    The ref sentinel (``__ref__`` / ``__type__``) is the intent-preservation
    canary — it MUST round-trip byte-for-byte through the Postgres JSONB
    column without any resolver-layer mutation. The ref is nested at
    ``entry_point.card`` (not directly at ``entry_point``) because
    ``TeamCard.entry_point`` expects a ``TeamCardMember`` model; it is the
    ``card`` sub-field on that member that accepts an ``AgentCard`` /
    ref-marker.
    """
    return {
        "name": "team",
        "description": "",
        "entry_point": {
            "card": {
                "__ref__": "lead-agent",
                "__type__": "akgentic.core.agent_card.AgentCard",
            },
            "headcount": 1,
            "members": [],
        },
        "members": [],
        "agent_profiles": [],
    }


def test_create_and_round_trip_team(postgres_clean_dsn: str) -> None:
    """Exercise POST → GET → PUT → DELETE through the Postgres-backed API."""
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from akgentic.catalog.api._settings import CatalogRouterSettings
    from akgentic.catalog.api.app import create_app
    from akgentic.catalog.repositories.postgres import PostgresCatalogConfig

    app = create_app(
        backend="postgres",
        postgres_config=PostgresCatalogConfig(connection_string=postgres_clean_dsn),
        router_settings=CatalogRouterSettings(expose_generic_kind_crud=True),
    )
    client = TestClient(app)

    # POST — create a team.
    body = {
        "id": "team",
        "kind": "team",
        "namespace": "ns-pg",
        "model_type": _TEAM_TYPE,
        "description": "initial",
        "payload": _team_payload(),
    }
    response = client.post("/catalog/team", json=body)
    assert response.status_code == 201, response.text
    created = response.json()
    assert created["id"] == "team"
    assert created["namespace"] == "ns-pg"
    assert created["description"] == "initial"

    # GET — fetch the entry back.
    response = client.get("/catalog/team/team", params={"namespace": "ns-pg"})
    assert response.status_code == 200, response.text
    fetched = response.json()
    assert fetched["description"] == "initial"
    assert fetched["payload"] == _team_payload()

    # PUT — update the description.
    update_body = {
        "id": "team",
        "kind": "team",
        "namespace": "ns-pg",
        "model_type": _TEAM_TYPE,
        "description": "updated",
        "payload": _team_payload(),
    }
    response = client.put("/catalog/team/team", params={"namespace": "ns-pg"}, json=update_body)
    assert response.status_code == 200, response.text
    updated = response.json()
    assert updated["description"] == "updated"

    # Verify the update persisted via a fresh GET.
    response = client.get("/catalog/team/team", params={"namespace": "ns-pg"})
    assert response.status_code == 200
    assert response.json()["description"] == "updated"

    # DELETE — remove the entry.
    response = client.delete("/catalog/team/team", params={"namespace": "ns-pg"})
    assert response.status_code == 204

    # Final GET — 404 after delete.
    response = client.get("/catalog/team/team", params={"namespace": "ns-pg"})
    assert response.status_code == 404


def test_nested_ref_marker_round_trips(postgres_clean_dsn: str) -> None:
    """AC27: a nested ``__ref__`` / ``__type__`` sentinel survives the Postgres boundary.

    Seeds the referenced agent first so the team's ref validates, then posts
    the team carrying a ``__ref__`` sentinel for ``entry_point``, then GETs
    the team back and asserts the sentinel is stored verbatim in the JSONB
    column (no resolver-layer expansion at the repository boundary).
    """
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from akgentic.catalog.api._settings import CatalogRouterSettings
    from akgentic.catalog.api.app import create_app
    from akgentic.catalog.repositories.postgres import PostgresCatalogConfig

    app = create_app(
        backend="postgres",
        postgres_config=PostgresCatalogConfig(connection_string=postgres_clean_dsn),
        router_settings=CatalogRouterSettings(expose_generic_kind_crud=True),
    )
    client = TestClient(app)

    # Seed the team with an inline entry_point first (so the namespace has a
    # team to hang sub-entries off).
    team_body = {
        "id": "team",
        "kind": "team",
        "namespace": "ns-ref",
        "model_type": _TEAM_TYPE,
        "payload": _team_payload(),
    }
    response = client.post("/catalog/team", json=team_body)
    assert response.status_code == 201, response.text

    # Seed the referenced agent.
    agent_body = {
        "id": "lead-agent",
        "kind": "agent",
        "namespace": "ns-ref",
        "model_type": _AGENT_TYPE,
        "payload": {
            "description": "",
            "skills": [],
            "agent_class": "akgentic.core.agent.Akgent",
            "config": {"name": "lead", "role": "lead"},
            "routes_to": [],
            "metadata": {},
        },
    }
    response = client.post("/catalog/agent", json=agent_body)
    assert response.status_code == 201, response.text

    # Update the team so its entry_point is a ref marker pointing at the agent.
    ref_payload = _team_payload_with_nested_ref()
    update_body = {
        "id": "team",
        "kind": "team",
        "namespace": "ns-ref",
        "model_type": _TEAM_TYPE,
        "payload": ref_payload,
    }
    response = client.put("/catalog/team/team", params={"namespace": "ns-ref"}, json=update_body)
    assert response.status_code == 200, response.text

    response = client.get("/catalog/team/team", params={"namespace": "ns-ref"})
    assert response.status_code == 200
    # Byte-exact round-trip: the __ref__ / __type__ sentinel is preserved,
    # not expanded into a resolved AgentCard.
    assert response.json()["payload"] == ref_payload


def test_create_app_postgres_without_config_raises() -> None:
    """AC3: missing ``postgres_config`` yields the pinned ``ValueError`` message."""
    pytest.importorskip("fastapi")

    from akgentic.catalog.api.app import create_app

    with pytest.raises(ValueError) as exc_info:
        create_app(backend="postgres")
    assert "postgres_config is required when backend='postgres'" in str(exc_info.value)
