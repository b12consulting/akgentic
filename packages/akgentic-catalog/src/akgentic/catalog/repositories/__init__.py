"""Public API surface for catalog repositories.

Re-exports abstract repository interfaces (TemplateCatalogRepository,
ToolCatalogRepository, AgentCatalogRepository, TeamCatalogRepository)
and their YAML-backed implementations. MongoDB backend exports are
conditionally available when pymongo is installed.
"""

from __future__ import annotations

from akgentic.catalog.repositories.base import (
    AgentCatalogRepository,
    TeamCatalogRepository,
    TemplateCatalogRepository,
    ToolCatalogRepository,
)
from akgentic.catalog.repositories.yaml.agent_repo import YamlAgentCatalogRepository
from akgentic.catalog.repositories.yaml.team_repo import YamlTeamCatalogRepository
from akgentic.catalog.repositories.yaml.template_repo import YamlTemplateCatalogRepository
from akgentic.catalog.repositories.yaml.tool_repo import YamlToolCatalogRepository

__all__ = [
    "AgentCatalogRepository",
    "TeamCatalogRepository",
    "TemplateCatalogRepository",
    "ToolCatalogRepository",
    "YamlAgentCatalogRepository",
    "YamlTeamCatalogRepository",
    "YamlTemplateCatalogRepository",
    "YamlToolCatalogRepository",
]

try:
    from akgentic.catalog.repositories.mongo import (
        MongoAgentCatalogRepository,
        MongoCatalogConfig,
        MongoTeamCatalogRepository,
        MongoTemplateCatalogRepository,
        MongoToolCatalogRepository,
        from_document,
        to_document,
    )

    __all__ += [
        "MongoAgentCatalogRepository",
        "MongoCatalogConfig",
        "MongoTeamCatalogRepository",
        "MongoTemplateCatalogRepository",
        "MongoToolCatalogRepository",
        "from_document",
        "to_document",
    ]
except ImportError:
    pass
