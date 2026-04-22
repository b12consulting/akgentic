"""Public API surface for catalog services.

Re-exports TemplateCatalog, ToolCatalog, AgentCatalog, and TeamCatalog.
Services wrap repositories with CRUD operations, cross-catalog reference
validation, and delete-protection for entries referenced downstream.
"""

from __future__ import annotations

from akgentic.catalog.services.agent_catalog import AgentCatalog
from akgentic.catalog.services.team_catalog import TeamCatalog
from akgentic.catalog.services.template_catalog import TemplateCatalog
from akgentic.catalog.services.tool_catalog import ToolCatalog

__all__ = [
    "AgentCatalog",
    "TeamCatalog",
    "TemplateCatalog",
    "ToolCatalog",
]
