"""Catalog service wiring for the CLI.

Provides ``build_catalogs()`` for YAML backend, ``build_mongo_catalogs()``
for MongoDB, ``build_postgres_catalogs()`` for Postgres (Nagra), and
``build_catalogs_from_state()`` to dispatch based on ``GlobalState.backend``.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

    from akgentic.catalog.cli.main import GlobalState
    from akgentic.catalog.services.agent_catalog import AgentCatalog
    from akgentic.catalog.services.team_catalog import TeamCatalog
    from akgentic.catalog.services.template_catalog import TemplateCatalog
    from akgentic.catalog.services.tool_catalog import ToolCatalog

# Type alias for the four-catalog tuple returned by all wiring functions
CatalogTuple = tuple["TemplateCatalog", "ToolCatalog", "AgentCatalog", "TeamCatalog"]

__all__ = [
    "build_catalogs",
    "build_catalogs_from_state",
    "build_mongo_catalogs",
    "build_postgres_catalogs",
]

logger = logging.getLogger(__name__)


def build_catalogs_from_state(
    state: GlobalState,
) -> CatalogTuple:
    """Dispatch to the YAML, MongoDB, or Postgres backend based on global state.

    Args:
        state: The CLI global state containing backend selection and
            connection parameters.

    Returns:
        Tuple of (TemplateCatalog, ToolCatalog, AgentCatalog, TeamCatalog).
    """
    if state.backend == "mongodb":
        assert state.mongo_uri is not None, "mongo_uri must be set for mongodb backend"
        assert state.mongo_db is not None, "mongo_db must be set for mongodb backend"
        return build_mongo_catalogs(state.mongo_uri, state.mongo_db)
    if state.backend == "postgres":
        assert state.postgres_conn_string is not None, (
            "postgres_conn_string must be set for postgres backend"
        )
        return build_postgres_catalogs(state.postgres_conn_string)
    return build_catalogs(state.catalog_dir)


def build_catalogs(
    catalog_dir: Path,
) -> CatalogTuple:
    """Wire all four catalog services from a YAML base directory.

    Creates subdirectories (``templates/``, ``tools/``, ``agents/``,
    ``teams/``) if they do not exist, then wires YAML repositories and
    catalog services in dependency order with back-references for delete
    protection.

    Args:
        catalog_dir: Root directory containing catalog YAML files.

    Returns:
        Tuple of (TemplateCatalog, ToolCatalog, AgentCatalog, TeamCatalog).
    """
    from akgentic.catalog.repositories.yaml.agent_repo import YamlAgentCatalogRepository
    from akgentic.catalog.repositories.yaml.team_repo import YamlTeamCatalogRepository
    from akgentic.catalog.repositories.yaml.template_repo import YamlTemplateCatalogRepository
    from akgentic.catalog.repositories.yaml.tool_repo import YamlToolCatalogRepository
    from akgentic.catalog.services.agent_catalog import AgentCatalog
    from akgentic.catalog.services.team_catalog import TeamCatalog
    from akgentic.catalog.services.template_catalog import TemplateCatalog
    from akgentic.catalog.services.tool_catalog import ToolCatalog

    # Create subdirectories if absent
    for name in ("templates", "tools", "agents", "teams"):
        (catalog_dir / name).mkdir(parents=True, exist_ok=True)

    # Create repositories
    template_repo = YamlTemplateCatalogRepository(catalog_dir / "templates")
    tool_repo = YamlToolCatalogRepository(catalog_dir / "tools")
    agent_repo = YamlAgentCatalogRepository(catalog_dir / "agents")
    team_repo = YamlTeamCatalogRepository(catalog_dir / "teams")

    # Create services in dependency order
    template_catalog = TemplateCatalog(template_repo)
    tool_catalog = ToolCatalog(tool_repo)
    agent_catalog = AgentCatalog(agent_repo, template_catalog, tool_catalog)
    team_catalog = TeamCatalog(team_repo, agent_catalog)

    # Wire downstream back-references for delete protection
    template_catalog.agent_catalog = agent_catalog
    tool_catalog.agent_catalog = agent_catalog
    agent_catalog.team_catalog = team_catalog

    logger.info("Wired catalog services from %s", catalog_dir)
    return template_catalog, tool_catalog, agent_catalog, team_catalog


def build_mongo_catalogs(
    mongo_uri: str,
    mongo_db: str,
) -> CatalogTuple:
    """Wire all four catalog services from a MongoDB backend.

    Imports MongoDB dependencies lazily to avoid requiring pymongo when
    using the YAML backend.

    Args:
        mongo_uri: MongoDB connection URI.
        mongo_db: MongoDB database name.

    Returns:
        Tuple of (TemplateCatalog, ToolCatalog, AgentCatalog, TeamCatalog).
    """
    from akgentic.catalog.repositories.mongo import (
        MongoAgentCatalogRepository,
        MongoCatalogConfig,
        MongoTeamCatalogRepository,
        MongoTemplateCatalogRepository,
        MongoToolCatalogRepository,
    )
    from akgentic.catalog.services.agent_catalog import AgentCatalog
    from akgentic.catalog.services.team_catalog import TeamCatalog
    from akgentic.catalog.services.template_catalog import TemplateCatalog
    from akgentic.catalog.services.tool_catalog import ToolCatalog

    config = MongoCatalogConfig(connection_string=mongo_uri, database=mongo_db)
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

    # Wire downstream back-references for delete protection
    template_catalog.agent_catalog = agent_catalog
    tool_catalog.agent_catalog = agent_catalog
    agent_catalog.team_catalog = team_catalog

    logger.info("Wired catalog services from MongoDB %s", mongo_db)
    return template_catalog, tool_catalog, agent_catalog, team_catalog


def build_postgres_catalogs(
    conn_string: str,
) -> CatalogTuple:
    """Wire all four catalog services from a Postgres backend.

    Imports Nagra dependencies lazily so the YAML backend remains usable
    without the ``[postgres]`` extra.

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
    from akgentic.catalog.services.agent_catalog import AgentCatalog
    from akgentic.catalog.services.team_catalog import TeamCatalog
    from akgentic.catalog.services.template_catalog import TemplateCatalog
    from akgentic.catalog.services.tool_catalog import ToolCatalog

    template_repo = NagraTemplateCatalogRepository(conn_string)
    tool_repo = NagraToolCatalogRepository(conn_string)
    agent_repo = NagraAgentCatalogRepository(conn_string)
    team_repo = NagraTeamCatalogRepository(conn_string)

    # Create services in dependency order
    template_catalog = TemplateCatalog(template_repo)
    tool_catalog = ToolCatalog(tool_repo)
    agent_catalog = AgentCatalog(agent_repo, template_catalog, tool_catalog)
    team_catalog = TeamCatalog(team_repo, agent_catalog)

    # Wire downstream back-references for delete protection
    template_catalog.agent_catalog = agent_catalog
    tool_catalog.agent_catalog = agent_catalog
    agent_catalog.team_catalog = team_catalog

    # Log without the conn string — secrets hygiene.
    logger.info("Wired catalog services from Postgres")
    return template_catalog, tool_catalog, agent_catalog, team_catalog
