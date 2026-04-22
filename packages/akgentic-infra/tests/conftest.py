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
    """Create minimal YAML catalog entries for a test team."""
    _write_yaml(
        catalog_root / "agents" / "human-proxy.yaml",
        {
            "id": "human-proxy",
            "tool_ids": [],
            "card": {
                "role": "Human",
                "description": "Human user interface",
                "skills": [],
                "agent_class": "akgentic.agent.HumanProxy",
                "config": {"name": "@Human", "role": "Human"},
                "routes_to": ["@Manager"],
            },
        },
    )
    _write_yaml(
        catalog_root / "agents" / "manager.yaml",
        {
            "id": "manager",
            "tool_ids": [],
            "card": {
                "role": "Manager",
                "description": "Test manager agent",
                "skills": ["coordination"],
                "agent_class": "akgentic.agent.BaseAgent",
                "config": {"name": "@Manager", "role": "Manager"},
                "routes_to": [],
            },
        },
    )
    _write_yaml(
        catalog_root / "teams" / "test-team.yaml",
        {
            "id": "test-team",
            "name": "Test Team",
            "entry_point": "human-proxy",
            "message_types": ["akgentic.core.messages.UserMessage"],
            "members": [
                {"agent_id": "human-proxy"},
                {"agent_id": "manager"},
            ],
            "profiles": [],
        },
    )
    # Empty dirs for templates and tools (no entries needed for this team)
    (catalog_root / "templates").mkdir(parents=True, exist_ok=True)
    (catalog_root / "tools").mkdir(parents=True, exist_ok=True)


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
def team_service(community_services: CommunityServices) -> TeamService:
    """TeamService wired to community services."""
    return TeamService(services=community_services)


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
