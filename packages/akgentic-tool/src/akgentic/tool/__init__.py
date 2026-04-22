"""akgentic-tool public API."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .knowledge_graph.models import KnowledgeGraphStateEvent as KnowledgeGraphStateEvent

# Submodules with their own __init__ files
from . import mcp, planning, sandbox, search, team, workspace  # noqa: F401
from .core import (  # noqa: F401
    COMMAND,
    SYSTEM_PROMPT,
    TOOL_CALL,
    BaseToolParam,
    Channels,
    ToolCard,
    ToolFactory,
)
from .errors import RetriableError  # noqa: F401
from .event import (  # noqa: F401
    ActorToolObserver,
    TeamManagementToolObserver,
    ToolObserver,
    ToolStateEvent,
    ToolStatePayload,
)
from .sandbox.bwrap import BwrapSandboxActor  # noqa: F401
from .sandbox.seatbelt import SeatbeltSandboxActor  # noqa: F401
from .sandbox.tool import ExecTool  # noqa: F401
from .workspace.tool import WorkspaceTool  # noqa: F401

try:
    from .vector import EmbeddingService, VectorEntry, VectorIndex  # noqa: F401

    _VECTOR_SEARCH_AVAILABLE = True
except ImportError:
    _VECTOR_SEARCH_AVAILABLE = False

__all__ = [
    # Core abstractions
    "BaseToolParam",
    "ToolCard",
    "ToolFactory",
    # Expose channel constants
    "COMMAND",
    "SYSTEM_PROMPT",
    "TOOL_CALL",
    "Channels",
    # Errors
    "RetriableError",
    # Events and observers
    "ToolObserver",
    "ActorToolObserver",
    "TeamManagementToolObserver",
    "ToolStateEvent",
    "ToolStatePayload",
    "KnowledgeGraphStateEvent",
    # Submodules
    "mcp",
    "planning",
    "sandbox",
    "search",
    "team",
    "workspace",
    "BwrapSandboxActor",
    "ExecTool",
    "SeatbeltSandboxActor",
    "WorkspaceTool",
]

if _VECTOR_SEARCH_AVAILABLE:
    __all__ += ["VectorEntry", "EmbeddingService", "VectorIndex"]


def __getattr__(name: str) -> Any:
    """Lazy re-export of the KG delta payload (Story 17.1).

    ``KnowledgeGraphStateEvent`` lives in ``akgentic.tool.knowledge_graph.models``
    and pulls the ``[vector_search]`` optional dependency chain when imported.
    Exposing it via module ``__getattr__`` keeps the bare ``akgentic.tool``
    import cheap (see ``test_tool_import_does_not_trigger_kg_import``) while
    still honoring AC #5 of Story 17.1.
    """
    if name == "KnowledgeGraphStateEvent":
        from .knowledge_graph.models import KnowledgeGraphStateEvent

        return KnowledgeGraphStateEvent
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
