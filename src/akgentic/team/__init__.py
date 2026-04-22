"""Team lifecycle management — create, resume, stop, delete teams with event-sourced persistence.

Public API surface for the akgentic-team package. All public types are
re-exported here via explicit __all__.
"""

from __future__ import annotations

from akgentic.team.factory import TeamFactory
from akgentic.team.manager import TeamManager
from akgentic.team.models import (
    AgentStateSnapshot,
    PersistedEvent,
    Process,
    TeamCard,
    TeamCardMember,
    TeamRuntime,
    TeamStatus,
)
from akgentic.team.ports import (
    EventStore,
    NullServiceRegistry,
    ServiceRegistry,
)
from akgentic.team.repositories import YamlEventStore
from akgentic.team.restorer import TeamRestorer
from akgentic.team.subscriber import PersistenceSubscriber

__version__ = "1.0.0-alpha.1"

__all__: list[str] = [
    "__version__",
    "AgentStateSnapshot",
    "EventStore",
    "NullServiceRegistry",
    "PersistenceSubscriber",
    "PersistedEvent",
    "Process",
    "ServiceRegistry",
    "TeamCard",
    "TeamCardMember",
    "TeamFactory",
    "TeamManager",
    "TeamRestorer",
    "TeamRuntime",
    "TeamStatus",
    "YamlEventStore",
]

_mongo_available = False
try:
    from akgentic.team.repositories import MongoEventStore  # noqa: F401

    __all__.append("MongoEventStore")
    _mongo_available = True
except ImportError:
    pass
