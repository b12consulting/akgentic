"""Domain models for team lifecycle management.

TeamCard, TeamCardMember, TeamRuntime, TeamStatus, Process, PersistedEvent,
AgentStateSnapshot.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any, cast

from pydantic import Field, PrivateAttr

from akgentic.core.actor_address import ActorAddress
from akgentic.core.actor_address_impl import ActorAddressProxy
from akgentic.core.actor_system_impl import ActorSystem
from akgentic.core.agent import Akgent
from akgentic.core.agent_card import AgentCard
from akgentic.core.agent_state import BaseState
from akgentic.core.messages.message import Message
from akgentic.core.orchestrator import Orchestrator
from akgentic.core.user_proxy import UserProxy
from akgentic.core.utils import hydrate_addresses
from akgentic.core.utils.serializer import SerializableBaseModel


class TeamCardMember(SerializableBaseModel):
    """A member slot in a team card, wrapping an AgentCard with multiplicity.

    TeamCardMember is self-referential: each member can contain subordinate
    members, forming a tree that describes hierarchical team structures.

    Attributes:
        card: The agent card describing this member's role and capabilities.
        headcount: Number of agent instances to create for this slot.
        members: Subordinate members managed by this member.
    """

    card: AgentCard = Field(description="Agent card describing this member's role and capabilities")
    headcount: int = Field(default=1, description="Number of agent instances for this slot")
    members: list[TeamCardMember] = Field(
        default_factory=list,
        description="Subordinate members managed by this member",
    )


class TeamCard(SerializableBaseModel):
    """Declarative definition of a team's structure, entry point, and routing.

    TeamCard describes the full hierarchy of agents in a team. The entry_point
    is the agent that receives external messages. Members form a tree that can
    be walked to discover all agent cards and supervisory relationships.

    Attributes:
        name: Unique name identifying this team definition.
        description: Human-readable summary of what the team does.
        entry_point: The member that serves as the team's external interface.
        members: Top-level members of the team (excluding the entry point).
        message_types: Message classes the team handles; first is the default.
    """

    name: str = Field(description="Unique name identifying this team definition")
    description: str = Field(description="Human-readable summary of what the team does")
    entry_point: TeamCardMember = Field(
        description="The member that serves as the team's external interface",
    )
    members: list[TeamCardMember] = Field(
        default_factory=list,
        description="Top-level members of the team excluding the entry point",
    )
    message_types: list[type] = Field(
        default_factory=list,
        description="Message classes the team handles; first is the default",
    )
    agent_profiles: list[AgentCard] = Field(
        default_factory=list,
        description="AgentCards available for runtime hiring, not instantiated at startup",
    )

    @property
    def agent_cards(self) -> dict[str, AgentCard]:
        """Return a flat index of all AgentCards in the member tree.

        Walks the entry_point and all members recursively, collecting every
        AgentCard keyed by its ``config.name``.

        Returns:
            Dictionary mapping config name to AgentCard for every member.
        """
        result: dict[str, AgentCard] = {}
        self._collect_cards(self.entry_point, result)
        for member in self.members:
            self._collect_cards(member, result)
        return result

    @property
    def supervisors(self) -> list[AgentCard]:
        """Return AgentCards for the first layer of ``members`` only.

        Supervisors are the agents that receive external messages routed
        through the entry point.  The entry point itself is excluded --
        it is the *sender*, not a recipient.  Deeper members (children
        of first-layer members) are also excluded -- they are internal
        to their supervisor's subtree.

        Returns:
            List of AgentCards for each top-level member.
        """
        return [m.card for m in self.members]

    @staticmethod
    def _collect_cards(
        member: TeamCardMember,
        result: dict[str, AgentCard],
    ) -> None:
        """Recursively collect AgentCards from a member subtree.

        Args:
            member: The member node to start from.
            result: Accumulator dict to populate with discovered cards.

        Raises:
            ValueError: If a duplicate config name is detected in the tree.
        """
        name = member.card.config.name
        if name in result:
            msg = (
                f"Duplicate config name '{name}' in team member tree. "
                f"Each AgentCard must have a unique config.name."
            )
            raise ValueError(msg)
        result[name] = member.card
        for child in member.members:
            TeamCard._collect_cards(child, result)


# --- TeamRuntime ---


class TeamRuntime(SerializableBaseModel):
    """Live handle to a running team that survives serialization for persistence.

    Stores persistent actor addresses and rebuilds ephemeral proxies on
    construction via ``model_post_init``. Persistent fields survive
    ``model_dump()`` / ``model_validate()`` round-trips while ephemeral
    proxies are excluded from serialization.

    Attributes:
        id: Externally assigned unique identifier for this runtime instance.
        team: The declarative team definition this runtime is based on.
        actor_system: The actor system hosting this team's actors.
        orchestrator_addr: Persistent address of the orchestrator actor.
        entry_addr: Persistent address of the team's entry-point actor.
        supervisor_addrs: Persistent addresses of supervisor actors keyed by name.
        addrs: Persistent addresses of all actors keyed by name.
    """

    id: uuid.UUID = Field(description="Externally assigned unique identifier for this runtime")
    team: TeamCard = Field(description="Declarative team definition this runtime is based on")
    actor_system: ActorSystem = Field(
        exclude=True,
        description="Actor system hosting this team's actors",
    )
    orchestrator_addr: ActorAddress = Field(
        description="Persistent address of the orchestrator actor",
    )
    entry_addr: ActorAddress = Field(
        description="Persistent address of the entry-point actor",
    )
    supervisor_addrs: dict[str, ActorAddress] = Field(
        default_factory=dict,
        description="Persistent addresses of supervisor actors keyed by name",
    )
    addrs: dict[str, ActorAddress] = Field(
        default_factory=dict,
        description="Persistent addresses of all actors keyed by name",
    )

    _orchestrator_proxy: Orchestrator = PrivateAttr()
    _entry_proxy: Akgent[Any, Any] = PrivateAttr()
    _supervisor_proxies: dict[str, Akgent[Any, Any]] = PrivateAttr(default_factory=dict)
    _message_cls: type[Message] | None = PrivateAttr(default=None)
    _addr_map: dict[uuid.UUID, ActorAddress] = PrivateAttr(default_factory=dict)

    def model_post_init(self, __context: Any) -> None:
        """Rebuild all ephemeral proxies from persistent addresses.

        Always overwrites all ephemeral fields completely to ensure
        idempotency — safe to call multiple times.

        Args:
            __context: Pydantic validation context (unused).
        """
        self._orchestrator_proxy = self.actor_system.proxy_ask(self.orchestrator_addr, Orchestrator)
        entry_agent_class = self.team.entry_point.card.get_agent_class()
        self._entry_proxy = self.actor_system.proxy_tell(self.entry_addr, entry_agent_class)

        self._supervisor_proxies = {}
        for card in self.team.supervisors:
            addr = self.supervisor_addrs.get(card.config.name)
            if addr is not None:
                self._supervisor_proxies[card.config.name] = self.actor_system.proxy_ask(
                    addr, card.get_agent_class()
                )

        self._message_cls = self.team.message_types[0] if self.team.message_types else None

    def _make_message(self, content: str) -> Message:
        """Create a message from the team's declared message type.

        Args:
            content: The message content.

        Returns:
            A Message instance of the team's declared type.

        Raises:
            RuntimeError: If no message type is declared for this team.
        """
        if self._message_cls is None:
            msg = "No message type declared for this team"
            raise RuntimeError(msg)
        return self._message_cls(content=content)  # type: ignore[call-arg]

    def send(self, content: str) -> None:
        """Send a message into the team through the entry-point agent.

        Routes through the entry proxy so that ``sender`` is set to the
        entry agent.  Each supervisor receives its own message instance
        to avoid shared mutable state between actors.  The entry point
        itself never receives a copy -- it is the sender, not a recipient.

        Args:
            content: The message content to send.
        """
        for addr in self.supervisor_addrs.values():
            self._entry_proxy.send(addr, self._make_message(content))

    def _resolve_addr(self, agent_name: str, addr: ActorAddress) -> ActorAddress:
        """Resolve a potentially stale proxy address to a live address.

        If the address is an ``ActorAddressProxy`` (from deserialized data),
        looks up the live address in ``_addr_map``.  Returns the address
        unchanged if it is already live.

        Args:
            agent_name: Name of the agent (used in error messages).
            addr: The address to resolve.

        Returns:
            A live ``ActorAddress``.

        Raises:
            ValueError: If the address is a stale proxy with no live mapping.
        """
        if isinstance(addr, ActorAddressProxy):
            live = self._addr_map.get(addr.agent_id)
            if live is None:
                msg = (
                    f"Agent '{agent_name}' has stale proxy address"
                    " — no live mapping available"
                )
                raise ValueError(msg)
            return live
        return addr

    def _lookup_member(self, agent_name: str) -> ActorAddress:
        """Look up a team member by name and resolve stale proxies.

        Args:
            agent_name: Name of the agent to look up.

        Returns:
            A live ``ActorAddress`` for the agent.

        Raises:
            ValueError: If the agent is not found or has a stale proxy.
        """
        addr = self._orchestrator_proxy.get_team_member(agent_name)
        if addr is None:
            msg = f"Agent '{agent_name}' not found in team '{self.team.name}'"
            raise ValueError(msg)
        return self._resolve_addr(agent_name, addr)

    def send_to(self, agent_name: str, content: str) -> None:
        """Send a directed message to a specific agent by name.

        Looks up the agent via the orchestrator proxy and sends a message
        via the entry proxy. Includes a safety net to resolve stale
        ``ActorAddressProxy`` refs that may leak through after restore.

        Args:
            agent_name: Name of the target agent.
            content: The message content.

        Raises:
            ValueError: If the agent is not found or has a stale proxy address.
        """
        actor_addr = self._lookup_member(agent_name)
        message = self._make_message(content)
        self._entry_proxy.send(actor_addr, message)

    def send_from_to(
        self,
        sender_name: str,
        recipient_name: str,
        content: str,
    ) -> None:
        """Send a message from a specified agent to another agent.

        Unlike ``send_to()`` which always sends via the entry proxy (so
        the sender is the entry agent), this method obtains a proxy for
        the *sender* agent and calls ``send()`` on it, so the message's
        ``sender`` field is set to the specified sender's address.

        Args:
            sender_name: Name of the agent to send from.
            recipient_name: Name of the agent to send to.
            content: The message content.

        Raises:
            ValueError: If sender or recipient is not found or has a stale proxy.
        """
        sender_addr = self._lookup_member(sender_name)
        recipient_addr = self._lookup_member(recipient_name)
        sender_proxy = self.actor_system.proxy_tell(sender_addr, Akgent)
        sender_proxy.send(recipient_addr, self._make_message(content))

    def process_human_input(self, content: str, message: Message) -> None:
        """Route human input to the correct UserProxy by recipient name.

        Validates that the message has a recipient, rehydrates any
        ``ActorAddressProxy`` references via the orchestrator, then
        routes by ``message.recipient.name`` to the correct ``UserProxy``.

        Args:
            content: The human's text response.
            message: The original message from the requesting agent.

        Raises:
            ValueError: If the message has no recipient, the recipient
                cannot be rehydrated, or the target is not a UserProxy.
        """
        if message.recipient is None:
            msg = "Cannot route human input: message has no recipient"
            raise ValueError(msg)

        def _resolve(proxy: ActorAddressProxy) -> ActorAddress:
            live = self._orchestrator_proxy.get_team_member(proxy.name)
            if live is None:
                err = (
                    f"Cannot rehydrate address for agent '{proxy.name}': "
                    "not found in team"
                )
                raise ValueError(err)
            return live

        live_message = cast(Message, hydrate_addresses(message, _resolve))

        target_name = live_message.recipient.name  # type: ignore[union-attr]
        target_addr = self._lookup_member(target_name)

        card = self.team.agent_cards.get(target_name)
        if card is None or not issubclass(card.get_agent_class(), UserProxy):
            msg = f"Agent '{target_name}' is not a UserProxy"
            raise ValueError(msg)

        proxy = self.actor_system.proxy_ask(target_addr, UserProxy)
        proxy.process_human_input(content, live_message)

    @property
    def orchestrator_proxy(self) -> Orchestrator:
        """Read-only access to the orchestrator proxy."""
        return self._orchestrator_proxy

    @property
    def entry_proxy(self) -> Akgent[Any, Any]:
        """Read-only access to the entry-point proxy."""
        return self._entry_proxy

    @property
    def supervisor_proxies(self) -> dict[str, Akgent[Any, Any]]:
        """Read-only access to the supervisor proxies."""
        return self._supervisor_proxies


# --- Persistence Models ---


class TeamStatus(StrEnum):
    """Lifecycle states for a team instance."""

    RUNNING = "running"
    STOPPED = "stopped"
    DELETED = "deleted"


class Process(SerializableBaseModel):
    """Persisted team metadata for crash recovery.

    Stores the TeamCard blueprint so the team can be rebuilt on resume,
    along with lifecycle status and audit fields. This is NOT the
    TeamRuntime -- addresses are stale after stop/crash.
    """

    team_id: uuid.UUID = Field(description="Unique identifier for this team instance")
    team_card: TeamCard = Field(description="Declarative team definition for rebuilding on resume")
    status: TeamStatus = Field(description="Current lifecycle state of the team")
    user_id: str = Field(default="cli", description="Identifier of the user who owns this team")
    user_email: str = Field(default="", description="Email of the user who owns this team")
    created_at: datetime = Field(description="Timestamp when the team was created")
    updated_at: datetime = Field(description="Timestamp of the last status change")


class PersistedEvent(SerializableBaseModel):
    """Append-only event log entry for event-sourced persistence.

    Each entry captures a single event (Message subclass) with its
    sequence number for ordered replay during team restoration.
    """

    team_id: uuid.UUID = Field(description="Team instance this event belongs to")
    sequence: int = Field(description="Monotonically increasing event sequence number")
    event: Message = Field(description="Polymorphic event payload preserving concrete Message type")
    timestamp: datetime = Field(description="Timestamp when the event was persisted")


class AgentStateSnapshot(SerializableBaseModel):
    """Overwrite-strategy snapshot of an agent's state.

    Captures the latest state of a single agent for fast recovery
    without full event replay. Each snapshot overwrites the previous
    one for the same (team_id, agent_id) pair.
    """

    team_id: uuid.UUID = Field(description="Team instance this snapshot belongs to")
    agent_id: str = Field(description="Identifier of the agent whose state is captured")
    state: BaseState = Field(
        description="Polymorphic agent state preserving concrete BaseState type"
    )
    updated_at: datetime = Field(description="Timestamp when the snapshot was taken")
