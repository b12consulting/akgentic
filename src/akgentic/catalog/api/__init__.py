"""FastAPI REST API for the Akgentic catalog.

Provides routers for all four catalog types (templates, tools, agents, teams)
with CRUD and search endpoints.

Requires the ``api`` extra: ``pip install akgentic-catalog[api]``.
"""

from __future__ import annotations

try:
    import fastapi as _fastapi  # noqa: F401
except ImportError as exc:
    raise ImportError(
        "FastAPI is required. Install with: pip install akgentic-catalog[api]"
    ) from exc

from akgentic.catalog.api._errors import ErrorResponse, add_exception_handlers
from akgentic.catalog.api.agent_router import router as agent_router
from akgentic.catalog.api.app import create_app
from akgentic.catalog.api.team_router import router as team_router
from akgentic.catalog.api.template_router import router as template_router
from akgentic.catalog.api.tool_router import router as tool_router

__all__ = [
    "ErrorResponse",
    "add_exception_handlers",
    "agent_router",
    "create_app",
    "team_router",
    "template_router",
    "tool_router",
]
