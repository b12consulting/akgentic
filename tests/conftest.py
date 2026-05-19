"""Shared test fixtures for akgentic-infra tests."""

from __future__ import annotations

import os
from collections.abc import Generator
from pathlib import Path

import pytest
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient

from akgentic.infra.server.app import create_app
from akgentic.infra.server.deps import CommunityServices
from akgentic.infra.server.services.team_service import TeamService
from akgentic.infra.server.settings import CommunitySettings
from akgentic.infra.wiring import wire_community


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Auto-skip tests marked with ``llm`` when OPENAI_API_KEY is not set."""
    if not os.environ.get("OPENAI_API_KEY"):
        skip_llm = pytest.mark.skip(reason="OPENAI_API_KEY not set")
        for item in items:
            if "llm" in item.keywords:
                item.add_marker(skip_llm)


def _write_yaml(path: Path, data: dict[str, object]) -> None:
    """Write a single YAML entry file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.dump(data, default_flow_style=False))


def _seed_catalog(catalog_root: Path) -> None:
    """Create minimal YAML catalog entries for a test team.

    Seeds the v2 per-namespace layout (``{catalog_root}/{namespace}/{kind}/{id}.yaml``)
    only. After Story 18.3 the community-tier wiring exposes a single unified
    ``Catalog`` — the legacy v1 per-kind layout is no longer consumed.
    Namespace ``test-team`` matches what tests post via
    ``catalog_namespace="test-team"``.
    """
    _seed_v2_namespace(catalog_root, namespace="test-team")


_TEAM_CARD_TYPE = "akgentic.team.models.TeamCard"


def _seed_v2_namespace(catalog_root: Path, namespace: str) -> None:
    """Write a minimal v2 team-namespace bundle into ``catalog_root``.

    The ``TeamCard`` payload shape is taken from
    ``akgentic.team.models.TeamCard``; every agent_class / model_type
    string satisfies the v2 allowlist (``akgentic.*``).

    The member configs use plain ``akgentic.core.agent.Akgent`` (which
    expects ``BaseConfig``) because the v2 resolver hydrates
    ``AgentCard.config`` against the declared annotation (``BaseConfig``).
    Upgrading v2 to per-agent-class config types is tracked as part of
    Epic 19's v1 removal. Tests that only need the agents to *exist*
    and route messages by name work with the plain base class.
    """
    team_payload = {
        "name": "Test Team",
        "description": "v2 test team for infra tests",
        "entry_point": {
            "card": {
                "role": "Human",
                "description": "Human user interface",
                "skills": [],
                "agent_class": "akgentic.core.agent.Akgent",
                "config": {"name": "@Human", "role": "Human"},
                "routes_to": ["@Manager"],
            },
            "headcount": 1,
            "members": [],
        },
        "members": [
            {
                "card": {
                    "role": "Manager",
                    "description": "Test manager agent",
                    "skills": ["coordination"],
                    "agent_class": "akgentic.core.agent.Akgent",
                    "config": {"name": "@Manager", "role": "Manager"},
                    "routes_to": [],
                },
                "headcount": 1,
                "members": [],
            },
        ],
        "message_types": [{"__type__": "akgentic.core.messages.UserMessage"}],
        "agent_profiles": [],
    }
    _write_yaml(
        catalog_root / namespace / "team" / "team.yaml",
        {
            "id": "team",
            "kind": "team",
            "namespace": namespace,
            "model_type": _TEAM_CARD_TYPE,
            "description": "v2 test team namespace bundle",
            "payload": team_payload,
        },
    )


@pytest.fixture(autouse=True)
def _ensure_openai_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """Set a dummy OPENAI_API_KEY so BaseAgent actors can initialise in unit tests."""
    if not os.environ.get("OPENAI_API_KEY"):
        monkeypatch.setenv("OPENAI_API_KEY", "test-dummy-key")


@pytest.fixture()
def server_settings(tmp_path: Path) -> CommunitySettings:
    """Server settings with tmp_path-based workspaces."""
    return CommunitySettings(
        workspaces_root=tmp_path / "workspaces",
        event_store_path=tmp_path / "event_store",
        catalog_path=tmp_path / "catalog",
    )


@pytest.fixture()
def seeded_settings(tmp_path: Path) -> CommunitySettings:
    """Server settings with pre-seeded catalog YAML files."""
    settings = CommunitySettings(
        workspaces_root=tmp_path / "workspaces",
        event_store_path=tmp_path / "event_store",
        catalog_path=tmp_path / "catalog",
    )
    _seed_catalog(settings.catalog_path)
    return settings


@pytest.fixture()
def community_services(
    seeded_settings: CommunitySettings,
) -> Generator[CommunityServices, None, None]:
    """Wired community services with seeded catalog data."""
    services = wire_community(seeded_settings)
    yield services
    services.actor_system.shutdown()


@pytest.fixture()
def team_service(
    community_services: CommunityServices,
    seeded_settings: CommunitySettings,
) -> TeamService:
    """TeamService wired to community services."""
    return TeamService(
        services=community_services,
        workspaces_root=seeded_settings.workspaces_root,
    )


@pytest.fixture()
def app(
    seeded_settings: CommunitySettings,
    community_services: CommunityServices,
) -> Generator[FastAPI, None, None]:
    """FastAPI app via the two-arg factory."""
    application = create_app(community_services, seeded_settings)
    yield application
    application.state.services.actor_system.shutdown()


@pytest.fixture()
def client(app: FastAPI) -> TestClient:
    """Sync HTTP test client."""
    return TestClient(app)


@pytest.fixture()
def v1_seeded_settings(tmp_path: Path) -> CommunitySettings:
    """Server settings with V1 frontend adapter enabled and pre-seeded catalog."""
    settings = CommunitySettings(
        workspaces_root=tmp_path / "workspaces",
        event_store_path=tmp_path / "event_store",
        catalog_path=tmp_path / "catalog",
        frontend_adapter=(
            "akgentic.infra.server.routes.frontend_adapter.angular_v1.AngularV1Adapter"
        ),
    )
    _seed_catalog(settings.catalog_path)
    return settings


@pytest.fixture()
def v1_client(
    v1_seeded_settings: CommunitySettings,
) -> Generator[TestClient, None, None]:
    """Sync HTTP test client with V1 frontend adapter routes mounted."""
    services = wire_community(v1_seeded_settings)
    application = create_app(services, v1_seeded_settings)
    yield TestClient(application)
    services.actor_system.shutdown()
