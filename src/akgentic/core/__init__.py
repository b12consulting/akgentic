"""Akgentic v2: Actor framework for agent-based systems.

Phase 1 provides core actor primitives with minimal dependencies (pydantic for serialization).
"""

# Extend __path__ to allow sub-packages from other workspace distributions
# (e.g. akgentic-llm's akgentic.llm) to be discovered as part of this namespace.
from pkgutil import extend_path

__path__ = extend_path(__path__, __name__)

from akgentic.core.actor_address import ActorAddress
from akgentic.core.actor_address_impl import (
    ActorAddressImpl,
    ActorAddressProxy,
    ActorAddressStopped,
)
from akgentic.core.actor_system_impl import (
    ActorSystem,
    ExecutionContext,
    Statistics,
)
from akgentic.core.agent import Akgent, AkgentDeserializeContext
from akgentic.core.agent_card import AgentCard
from akgentic.core.agent_config import (
    AgentConfig,
    BaseConfig,
)
from akgentic.core.agent_state import AkgentStateObserver, BaseState
from akgentic.core.orchestrator import EventSubscriber, Orchestrator
from akgentic.core.user_proxy import UserProxy

__version__ = "1.0.0-alpha.2"

__all__ = [
    # Version
    "__version__",
    # Agent base class and proxies
    "Akgent",
    "AkgentDeserializeContext",
    # Actor system
    "ActorSystem",
    "ExecutionContext",
    "Statistics",
    # Actor addressing
    "ActorAddress",
    "ActorAddressImpl",
    "ActorAddressProxy",
    "ActorAddressStopped",
    # Agent configuration
    "AgentConfig",
    "BaseConfig",
    # Agent card (profiles)
    "AgentCard",
    # Agent state
    "AkgentStateObserver",
    "BaseState",
    # Orchestrator
    "Orchestrator",
    "EventSubscriber",
    # UserProxy
    "UserProxy",
]
