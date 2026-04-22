"""FastAPI application factory for the Akgentic catalog API.

Provides ``create_app()`` which assembles a fully wired FastAPI application
with configurable storage backend (YAML files, MongoDB, or PostgreSQL via
Nagra).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Literal

from fastapi import FastAPI

from akgentic.catalog.api._errors import add_exception_handlers
from akgentic.catalog.api.agent_router import router as agent_router
from akgentic.catalog.api.agent_router import set_catalog as set_agent_catalog
from akgentic.catalog.api.team_router import router as team_router
from akgentic.catalog.api.team_router import set_catalog as set_team_catalog
from akgentic.catalog.api.template_router import router as template_router
from akgentic.catalog.api.template_router import set_catalog as set_template_catalog
from akgentic.catalog.api.tool_router import router as tool_router
from akgentic.catalog.api.tool_router import set_catalog as set_tool_catalog
from akgentic.catalog.services.agent_catalog import AgentCatalog
from akgentic.catalog.services.team_catalog import TeamCatalog
from akgentic.catalog.services.template_catalog import TemplateCatalog
from akgentic.catalog.services.tool_catalog import ToolCatalog

if TYPE_CHECKING:
    from pathlib import Path

    from akgentic.catalog.repositories.mongo._config import MongoCatalogConfig

__all__ = ["create_app"]

logger = logging.getLogger(__name__)


def create_app(
    *,
    backend: Literal["yaml", "mongodb", "postgres"] = "yaml",
    yaml_base_path: Path | None = None,
    mongo_config: MongoCatalogConfig | None = None,
    postgres_conn_string: str | None = None,
) -> FastAPI:
    """Create a fully wired FastAPI application for the catalog API.

    Args:
        backend: Storage backend to use — ``"yaml"`` for file-based,
            ``"mongodb"`` for MongoDB, or ``"postgres"`` for Nagra-backed
            PostgreSQL.
        yaml_base_path: Root directory for YAML catalog files. Required when
            ``backend="yaml"``. Subdirectories are created if absent.
        mongo_config: MongoDB connection configuration. Required when
            ``backend="mongodb"``.
        postgres_conn_string: Postgres libpq connection URL. Required when
            ``backend="postgres"``.

    Returns:
        A configured FastAPI application with all routers and exception
        handlers registered.

    Raises:
        ValueError: If required configuration for the chosen backend is missing.
    """
    if backend == "yaml":
        if yaml_base_path is None:
            msg = "yaml_base_path is required when backend='yaml'"
            raise ValueError(msg)
        template_catalog, tool_catalog, agent_catalog, team_catalog = _wire_yaml_backend(
            yaml_base_path
        )
    elif backend == "mongodb":
        if mongo_config is None:
            msg = "mongo_config is required when backend='mongodb'"
            raise ValueError(msg)
        template_catalog, tool_catalog, agent_catalog, team_catalog = _wire_mongodb_backend(
            mongo_config
        )
    elif backend == "postgres":
        if postgres_conn_string is None:
            msg = "postgres_conn_string is required when backend='postgres'"
            raise ValueError(msg)
        template_catalog, tool_catalog, agent_catalog, team_catalog = _wire_postgres_backend(
            postgres_conn_string
        )
    else:
        msg = f"Unknown backend: {backend!r}. Must be 'yaml', 'mongodb', or 'postgres'."
        raise ValueError(msg)

    # Wire downstream back-references for delete protection
    template_catalog.agent_catalog = agent_catalog
    tool_catalog.agent_catalog = agent_catalog
    agent_catalog.team_catalog = team_catalog

    # Inject catalogs into router modules
    set_template_catalog(template_catalog)
    set_tool_catalog(tool_catalog)
    set_agent_catalog(agent_catalog)
    set_team_catalog(team_catalog)

    # Assemble FastAPI app
    app = FastAPI(title="Akgentic Org API")
    app.include_router(template_router)
    app.include_router(tool_router)
    app.include_router(agent_router)
    app.include_router(team_router)
    add_exception_handlers(app)

    logger.info("Created Akgentic Org API with %s backend", backend)
    return app


def _wire_yaml_backend(
    base_path: Path,
) -> tuple[TemplateCatalog, ToolCatalog, AgentCatalog, TeamCatalog]:
    """Create catalog services wired with YAML repositories.

    Args:
        base_path: Root directory containing ``templates/``, ``tools/``,
            ``agents/``, and ``teams/`` subdirectories.

    Returns:
        Tuple of (TemplateCatalog, ToolCatalog, AgentCatalog, TeamCatalog).
    """
    from akgentic.catalog.repositories.yaml.agent_repo import YamlAgentCatalogRepository
    from akgentic.catalog.repositories.yaml.team_repo import YamlTeamCatalogRepository
    from akgentic.catalog.repositories.yaml.template_repo import YamlTemplateCatalogRepository
    from akgentic.catalog.repositories.yaml.tool_repo import YamlToolCatalogRepository

    # Create subdirectories if absent
    for name in ("templates", "tools", "agents", "teams"):
        (base_path / name).mkdir(parents=True, exist_ok=True)

    # Create repositories
    template_repo = YamlTemplateCatalogRepository(base_path / "templates")
    tool_repo = YamlToolCatalogRepository(base_path / "tools")
    agent_repo = YamlAgentCatalogRepository(base_path / "agents")
    team_repo = YamlTeamCatalogRepository(base_path / "teams")

    # Create services in dependency order
    template_catalog = TemplateCatalog(template_repo)
    tool_catalog = ToolCatalog(tool_repo)
    agent_catalog = AgentCatalog(agent_repo, template_catalog, tool_catalog)
    team_catalog = TeamCatalog(team_repo, agent_catalog)

    return template_catalog, tool_catalog, agent_catalog, team_catalog


def _wire_postgres_backend(
    conn_string: str,
) -> tuple[TemplateCatalog, ToolCatalog, AgentCatalog, TeamCatalog]:
    """Create catalog services wired with Nagra Postgres repositories.

    Schema creation (``init_db``) is a deployment concern and is NOT called
    here — repositories instantiate safely without touching the database.

    Args:
        conn_string: Nagra-compatible Postgres connection string.

    Returns:
        Tuple of (TemplateCatalog, ToolCatalog, AgentCatalog, TeamCatalog).
    """
    from akgentic.catalog.repositories.postgres import (
        NagraAgentCatalogRepository,
        NagraTeamCatalogRepository,
        NagraTemplateCatalogRepository,
        NagraToolCatalogRepository,
    )

    template_repo = NagraTemplateCatalogRepository(conn_string)
    tool_repo = NagraToolCatalogRepository(conn_string)
    agent_repo = NagraAgentCatalogRepository(conn_string)
    team_repo = NagraTeamCatalogRepository(conn_string)

    # Create services in dependency order
    template_catalog = TemplateCatalog(template_repo)
    tool_catalog = ToolCatalog(tool_repo)
    agent_catalog = AgentCatalog(agent_repo, template_catalog, tool_catalog)
    team_catalog = TeamCatalog(team_repo, agent_catalog)

    return template_catalog, tool_catalog, agent_catalog, team_catalog


def _wire_mongodb_backend(
    config: MongoCatalogConfig,
) -> tuple[TemplateCatalog, ToolCatalog, AgentCatalog, TeamCatalog]:
    """Create catalog services wired with MongoDB repositories.

    Args:
        config: MongoDB connection and collection configuration.

    Returns:
        Tuple of (TemplateCatalog, ToolCatalog, AgentCatalog, TeamCatalog).
    """
    from akgentic.catalog.repositories.mongo.agent_repo import MongoAgentCatalogRepository
    from akgentic.catalog.repositories.mongo.team_repo import MongoTeamCatalogRepository
    from akgentic.catalog.repositories.mongo.template_repo import MongoTemplateCatalogRepository
    from akgentic.catalog.repositories.mongo.tool_repo import MongoToolCatalogRepository

    client = config.create_client()

    template_repo = MongoTemplateCatalogRepository(
        config.get_collection(client, config.template_entries_collection)
    )
    tool_repo = MongoToolCatalogRepository(
        config.get_collection(client, config.tool_entries_collection)
    )
    agent_repo = MongoAgentCatalogRepository(
        config.get_collection(client, config.agent_entries_collection)
    )
    team_repo = MongoTeamCatalogRepository(
        config.get_collection(client, config.team_entries_collection)
    )

    # Create services in dependency order
    template_catalog = TemplateCatalog(template_repo)
    tool_catalog = ToolCatalog(tool_repo)
    agent_catalog = AgentCatalog(agent_repo, template_catalog, tool_catalog)
    team_catalog = TeamCatalog(team_repo, agent_catalog)

    return template_catalog, tool_catalog, agent_catalog, team_catalog
