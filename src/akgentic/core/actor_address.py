"""ActorAddress abstraction for agent references and communication.

Provides an abstract interface for agent addressing that decouples agent identity
from the underlying actor implementation (Pykka). Supports serialization for
Phase 3 remote communication and rich metadata access.

Phase 3 Extensibility:
    This module defines the ActorAddress ABC that can be extended for remote
    communication. Future implementations may include:
    - ActorAddressRemote: HTTP-based remote actor communication
    - ActorAddressRedis: Redis pub/sub based communication

Example:
    >>> # Using ActorAddress in an Agent (future Agent class)
    >>> class Agent(pykka.ThreadingActor):
    ...     @property
    ...     def myAddress(self) -> ActorAddress:
    ...         return ActorAddressImpl(self.actor_ref)
"""

from __future__ import annotations

import uuid
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from akgentic.core.utils.deserializer import ActorAddressDict


class ActorAddress(ABC):
    """Abstract base class for actor address/reference.

    Provides a unified interface for agent identification and communication
    that is independent of the underlying actor system (Pykka). All agent
    references should use this abstraction rather than raw ActorRef.

    The address provides rich metadata (name, role, team_id, squad_id) without
    exposing internal implementation details. Implementations handle different
    scenarios: live local actors, deserialized proxy addresses, and stopped actors.

    Attributes:
        agent_id: Unique identifier for the agent.
        name: Agent name from configuration.
        role: Agent role from configuration.
        team_id: Team identifier from configuration.
        squad_id: Squad identifier from configuration.
    """

    @property
    @abstractmethod
    def agent_id(self) -> uuid.UUID:
        """Unique identifier for this agent.

        Returns:
            UUID uniquely identifying this agent instance.
        """
        ...

    @property
    @abstractmethod
    def name(self) -> str:
        """Agent name from configuration.

        Returns:
            Human-readable name for this agent.
        """
        ...

    @property
    @abstractmethod
    def role(self) -> str:
        """Agent role from configuration.

        Returns:
            Role string describing agent's function.
        """
        ...

    @property
    @abstractmethod
    def team_id(self) -> uuid.UUID:
        """Team identifier from configuration.

        Returns:
            UUID of the team this agent belongs to.
        """
        ...

    @property
    @abstractmethod
    def squad_id(self) -> uuid.UUID | None:
        """Squad identifier from configuration.

        Returns:
            UUID of the squad this agent belongs to, or None if not set.
        """
        ...

    @abstractmethod
    def send(self, recipient: ActorAddress, message: Any) -> None:
        """Send a message from this agent to the recipient.

        Args:
            recipient: The target actor to receive the message, or None if unspecified.
            message: The message to send (typically a Message subclass).

        Raises:
            RuntimeError: If this address cannot send messages (e.g., proxy address).
        """
        ...

    @abstractmethod
    def is_alive(self) -> bool:
        """Check if this actor is still running.

        Returns:
            True if the actor is alive and can receive messages, False otherwise.
        """
        ...

    @abstractmethod
    def handle_user_message(self) -> bool:
        """Check if this agent accepts user messages.

        Returns:
            True if the agent can process UserMessage instances.
        """
        ...

    @abstractmethod
    def serialize(self) -> ActorAddressDict:
        """Serialize this address to a dictionary for transport.

        Produces an ActorAddressDict suitable for JSON serialization
        and Phase 3 remote communication.

        Returns:
            Dictionary representation of this address.
        """
        ...

    @abstractmethod
    def __repr__(self) -> str:
        """String representation for debugging.

        Returns:
            String showing class name, agent name, and ID.
        """
        ...
