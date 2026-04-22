"""Public API surface for catalog data models.

Re-exports entry models (TemplateEntry, ToolEntry, AgentEntry, TeamEntry,
TeamMemberSpec), query filter models (TemplateQuery, ToolQuery, AgentQuery,
TeamQuery), and error types (CatalogValidationError, EntryNotFoundError).
"""

from __future__ import annotations

from akgentic.catalog.models.agent import AgentEntry
from akgentic.catalog.models.errors import CatalogValidationError, EntryNotFoundError
from akgentic.catalog.models.queries import AgentQuery, TeamQuery, TemplateQuery, ToolQuery
from akgentic.catalog.models.team import TeamEntry, TeamMemberSpec
from akgentic.catalog.models.template import TemplateEntry
from akgentic.catalog.models.tool import ToolEntry

__all__ = [
    "AgentEntry",
    "AgentQuery",
    "CatalogValidationError",
    "EntryNotFoundError",
    "TeamMemberSpec",
    "TeamQuery",
    "TeamEntry",
    "TemplateEntry",
    "TemplateQuery",
    "ToolEntry",
    "ToolQuery",
]
