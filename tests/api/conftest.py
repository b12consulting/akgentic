"""Shared fixtures for API router tests."""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from akgentic.catalog.api._errors import add_exception_handlers
from akgentic.catalog.api.agent_router import router as agent_router
from akgentic.catalog.api.agent_router import set_catalog as set_agent_catalog
from akgentic.catalog.api.team_router import router as team_router
from akgentic.catalog.api.team_router import set_catalog as set_team_catalog
from akgentic.catalog.api.template_router import router as template_router
from akgentic.catalog.api.template_router import set_catalog as set_template_catalog
from akgentic.catalog.api.tool_router import router as tool_router
from akgentic.catalog.api.tool_router import set_catalog as set_tool_catalog
from akgentic.catalog.repositories.yaml.agent_repo import YamlAgentCatalogRepository
from akgentic.catalog.repositories.yaml.team_repo import YamlTeamCatalogRepository
from akgentic.catalog.repositories.yaml.template_repo import YamlTemplateCatalogRepository
from akgentic.catalog.repositories.yaml.tool_repo import YamlToolCatalogRepository
from akgentic.catalog.services.agent_catalog import AgentCatalog
from akgentic.catalog.services.team_catalog import TeamCatalog
from akgentic.catalog.services.template_catalog import TemplateCatalog
from akgentic.catalog.services.tool_catalog import ToolCatalog


@pytest.fixture()
def catalog_dirs(tmp_path: Path) -> dict[str, Path]:
    """Create temporary directories for each catalog type."""
    dirs = {}
    for name in ("templates", "tools", "agents", "teams"):
        d = tmp_path / name
        d.mkdir()
        dirs[name] = d
    return dirs


@pytest.fixture()
def catalogs(catalog_dirs: dict[str, Path]) -> dict[str, object]:
    """Wire all four catalog services with YAML repos in dependency order."""
    template_catalog = TemplateCatalog(YamlTemplateCatalogRepository(catalog_dirs["templates"]))
    tool_catalog = ToolCatalog(YamlToolCatalogRepository(catalog_dirs["tools"]))
    agent_catalog = AgentCatalog(
        YamlAgentCatalogRepository(catalog_dirs["agents"]),
        template_catalog,
        tool_catalog,
    )
    team_catalog = TeamCatalog(
        YamlTeamCatalogRepository(catalog_dirs["teams"]),
        agent_catalog,
    )
    # Wire downstream references for delete protection
    template_catalog.agent_catalog = agent_catalog
    tool_catalog.agent_catalog = agent_catalog
    return {
        "template": template_catalog,
        "tool": tool_catalog,
        "agent": agent_catalog,
        "team": team_catalog,
    }


@pytest.fixture()
def test_app(catalogs: dict[str, object]) -> Generator[FastAPI, None, None]:
    """Create a FastAPI app wired with catalog services for testing."""
    app = FastAPI(title="Test Catalog API")

    # Inject catalogs into router modules
    set_template_catalog(catalogs["template"])  # type: ignore[arg-type]
    set_tool_catalog(catalogs["tool"])  # type: ignore[arg-type]
    set_agent_catalog(catalogs["agent"])  # type: ignore[arg-type]
    set_team_catalog(catalogs["team"])  # type: ignore[arg-type]

    # Register routers and exception handlers
    app.include_router(template_router)
    app.include_router(tool_router)
    app.include_router(agent_router)
    app.include_router(team_router)
    add_exception_handlers(app)

    yield app

    # Clean up module-level state
    set_template_catalog(None)  # type: ignore[arg-type]
    set_tool_catalog(None)  # type: ignore[arg-type]
    set_agent_catalog(None)  # type: ignore[arg-type]
    set_team_catalog(None)  # type: ignore[arg-type]


@pytest.fixture()
def client(test_app: FastAPI) -> TestClient:
    """Create a test client for the wired FastAPI app."""
    return TestClient(test_app)
