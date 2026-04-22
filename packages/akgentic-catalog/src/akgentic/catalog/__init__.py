"""Public API surface for akgentic-catalog.

Re-exports entry models (TemplateEntry, ToolEntry, AgentEntry, TeamEntry),
query models, error types, abstract and YAML repository interfaces,
catalog services, and the resolve_env_vars utility. MongoDB backend exports
are conditionally available when pymongo is installed.
"""

from __future__ import annotations

from akgentic.catalog.env import resolve_env_vars
from akgentic.catalog.models.agent import AgentEntry
from akgentic.catalog.models.errors import CatalogValidationError, EntryNotFoundError
from akgentic.catalog.models.queries import AgentQuery, TeamQuery, TemplateQuery, ToolQuery
from akgentic.catalog.models.team import TeamEntry, TeamMemberSpec
from akgentic.catalog.models.template import TemplateEntry
from akgentic.catalog.models.tool import ToolEntry
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
from akgentic.catalog.services.agent_catalog import AgentCatalog
from akgentic.catalog.services.team_catalog import TeamCatalog
from akgentic.catalog.services.template_catalog import TemplateCatalog
from akgentic.catalog.services.tool_catalog import ToolCatalog

__all__ = [
    "AgentCatalog",
    "AgentCatalogRepository",
    "AgentEntry",
    "AgentQuery",
    "CatalogValidationError",
    "EntryNotFoundError",
    "TeamCatalog",
    "TeamCatalogRepository",
    "TeamMemberSpec",
    "TeamQuery",
    "TeamEntry",
    "TemplateCatalog",
    "TemplateCatalogRepository",
    "TemplateEntry",
    "TemplateQuery",
    "ToolCatalog",
    "ToolCatalogRepository",
    "ToolEntry",
    "ToolQuery",
    "YamlAgentCatalogRepository",
    "YamlTeamCatalogRepository",
    "YamlTemplateCatalogRepository",
    "YamlToolCatalogRepository",
    "resolve_env_vars",
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

try:
    from akgentic.catalog.repositories.postgres import (
        NagraAgentCatalogRepository,
        NagraTeamCatalogRepository,
        NagraTemplateCatalogRepository,
        NagraToolCatalogRepository,
        init_db,
    )

    __all__ += [
        "NagraAgentCatalogRepository",
        "NagraTeamCatalogRepository",
        "NagraTemplateCatalogRepository",
        "NagraToolCatalogRepository",
        "init_db",
    ]
except ImportError:
    pass

try:
    from akgentic.catalog.api import (
        ErrorResponse,
        add_exception_handlers,
        agent_router,
        create_app,
        team_router,
        template_router,
        tool_router,
    )

    __all__ += [
        "ErrorResponse",
        "add_exception_handlers",
        "agent_router",
        "create_app",
        "team_router",
        "template_router",
        "tool_router",
    ]
except ImportError:
    pass

try:
    from akgentic.catalog.cli import app as cli_app

    __all__ += [
        "cli_app",
    ]
except ImportError:
    pass
