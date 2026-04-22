from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Protocol, TypeAlias, runtime_checkable

from akgentic.core.actor_address import ActorAddress
from akgentic.core.agent import AkgentType
from akgentic.core.messages import Message

if TYPE_CHECKING:
    from akgentic.tool.knowledge_graph.models import KnowledgeGraphStateEvent

# Union of tool-specific delta payloads carried by ``ToolStateEvent`` (ADR-024).
# Defined as a ``TypeAlias`` so future stateful tools (e.g. ``VectorStoreStateEvent``)
# can extend the union without touching ``ToolStateEvent``. Uses a string forward
# reference to avoid the ``event.py → knowledge_graph.models`` import cycle.
ToolStatePayload: TypeAlias = "KnowledgeGraphStateEvent"


class ToolStateEvent(Message):
    """Generic tool-state event envelope (ADR-024, Story 17.1).

    Wraps a tool-specific delta payload so any stateful tool actor can broadcast
    typed state changes on the existing orchestrator event stream. Inherits
    ``team_id``, ``timestamp``, ``id``, ``sender``, and ``display_type`` from
    :class:`akgentic.core.messages.Message` without override.

    Attributes:
        tool_id: Tool-actor name emitting the event (e.g. ``"#KnowledgeGraphTool"``).
        seq: Per-tool monotonic sequence number (starts at 1, enforced in Story 17.2).
        payload: Tool-specific delta payload (see :data:`ToolStatePayload`).
    """

    tool_id: str
    seq: int
    payload: ToolStatePayload


@runtime_checkable
class ToolObserver(Protocol):
    """Basic observer protocol for tool interactions.

    This protocol defines the minimal interface required for tools that only
    need to emit events. Tools requiring actor-aware features should use
    ActorToolObserver instead.
    """

    def notify_event(self, event: object) -> None:
        """Called when a tool domain event is emitted.

        Args:
            event: Domain event object
        """
        ...


@runtime_checkable
class ActorToolObserver(ToolObserver, Protocol):
    """Actor-aware observer protocol for tool interactions.

    Extends ToolObserver with actor-specific capabilities needed by tools
    that interact with the actor system (e.g., PlanningTool).
    """

    @property
    def myAddress(self) -> ActorAddress:  # noqa: N802
        """Get the current actor's address."""
        ...

    @property
    def orchestrator(self) -> ActorAddress | None:
        """Get the orchestrator address."""
        ...

    @property
    def team_id(self) -> uuid.UUID:
        """Get the team id."""
        ...

    def proxy_ask(
        self,
        actor: ActorAddress,
        actor_type: type[AkgentType] | None = None,
        timeout: int | None = None,
    ) -> AkgentType:
        """Get a proxy to another actor.

        Args:
            actor: Address of the target actor
            actor_type: Optional expected type of the target actor for better type checking
            timeout: Optional timeout for the proxy ask

        Returns:
            Proxy object to interact with the target actor
        """
        ...


@runtime_checkable
class TeamManagementToolObserver(ActorToolObserver, Protocol):
    """Observer protocol for team management tools.

    Extends ActorToolObserver with team-specific capabilities needed by
    TeamTool for hiring, firing, and managing team members within the
    actor system.
    """

    def createActor(  # noqa: N802
        self,
        actor_class: type[AkgentType],
        *,
        config: object,
    ) -> ActorAddress:
        """Create a child actor with the given config.

        Args:
            actor_class: The actor class to instantiate
            config: Configuration object for the actor

        Returns:
            Address of the newly created actor
        """
        ...

    def on_hire(self, address: ActorAddress) -> None:
        """Hook called after hiring a team member.

        Handles agent-specific concerns such as:
        - Tracking child in agent's children list
        - Updating local caches
        - Any agent-specific bookkeeping

        Args:
            address: ActorAddress of hired agent
        """
        ...

    def on_fire(self, address: ActorAddress) -> None:
        """Hook called after firing a team member.

        Handles agent-specific concerns such as:
        - Removing from children tracking
        - Clearing from local caches
        - Any agent-specific cleanup

        Args:
            address: ActorAddress of fired agent
        """
        ...
