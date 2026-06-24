from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Protocol, TypeAlias, runtime_checkable

from akgentic.core.actor_address import ActorAddress
from akgentic.core.agent import AkgentType
from akgentic.core.messages import Message
from akgentic.core.utils.serializer import SerializableBaseModel

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


class CommandArg(SerializableBaseModel):
    """A single positional/keyword argument of a discoverable command (ADR-028 §Decision 3).

    Derived from a command callable's signature so consumers (the dispatch parser
    in Story 21.2 and the frontend help renderer) share one typed contract.

    Attributes:
        name: Argument name as it appears in the callable signature.
        type: JSON-schema type name (e.g. ``"string"``, ``"integer"``, ``"boolean"``).
        required: Whether the argument must be supplied (no default).
        description: Optional human-readable description; ``None`` when absent.
    """

    name: str
    type: str
    required: bool
    description: str | None = None


class CommandDescriptor(SerializableBaseModel):
    """A discoverable command exposed by a tool (ADR-028 §Decision 3).

    Describes one canonical command, its provenance, and its ordered argument
    list. The ``args`` order is load-bearing: it drives positional dispatch
    parsing (Story 21.2) and frontend help rendering.

    Attributes:
        name: Canonical command name (e.g. ``"hire_member"``).
        description: Command description, sourced from the callable docstring.
        args: Ordered list of :class:`CommandArg` entries (may be empty).
        tool_card: Provenance of the command (e.g. ``"TeamTool"``).
    """

    name: str
    description: str
    args: list[CommandArg]
    tool_card: str


class CommandsAnnouncedEvent(SerializableBaseModel):
    """Announcement of the command set an agent executes (ADR-028 §Decision 3).

    Emitted so downstream agents/frontends can render the available commands.
    Fully serializable: the ``__model__`` marker (from ``SerializableBaseModel``)
    lets consumers discriminate this inner event during deserialization.

    Attributes:
        agent: Address of the agent that executes these commands (core
            ``ActorAddress``; the tool layer MAY import core, NFR1).
        commands: The :class:`CommandDescriptor` set announced for ``agent``.
    """

    agent: ActorAddress
    commands: list[CommandDescriptor]


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
