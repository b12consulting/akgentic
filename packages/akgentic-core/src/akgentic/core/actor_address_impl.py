"""Concrete ActorAddress implementations.

Provides three ActorAddress implementations for different use cases:
- ActorAddressImpl: Wraps live Pykka ActorRef for local actors
- ActorAddressProxy: Represents deserialized/mock addresses
- ActorAddressStopped: Represents actors that have been stopped

Phase 3 Extensibility:
    Additional implementations can be added for remote communication:
    - ActorAddressRemote: HTTP-based remote actor addressing
    - ActorAddressRedis: Redis pub/sub based addressing
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any

from akgentic.core.actor_address import ActorAddress
from akgentic.core.utils.deserializer import ActorAddressDict

if TYPE_CHECKING:
    from pykka import ActorRef


class ActorAddressImpl(ActorAddress):
    """ActorAddress wrapping a live Pykka ActorRef.

    Provides access to agent metadata by reading from the underlying actor
    instance. Used for local in-memory actor communication.

    Note:
        This implementation accesses the Pykka 4.4.2+ weakref API
        (_actor_ref._actor_weakref) to dereference the underlying actor.
        Direct property access on a GC'd actor raises RuntimeError.

    Args:
        actor_ref: The Pykka ActorRef to wrap.

    Example:
        >>> actor_ref = MyAgent.start(config=agent_config)
        >>> address = ActorAddressImpl(actor_ref)
        >>> print(address.name)
        'my-agent'
    """

    def __init__(self, actor_ref: ActorRef[Any]) -> None:
        """Initialize with a Pykka ActorRef.

        Args:
            actor_ref: The Pykka ActorRef to wrap.
        """
        self._actor_ref = actor_ref

    def _resolve_actor(self) -> Any:
        """Dereference the weak reference to the underlying actor.

        Returns:
            The live actor instance.

        Raises:
            RuntimeError: If the actor has been garbage collected.
        """
        actor = self._actor_ref._actor_weakref()
        if actor is None:
            raise RuntimeError(f"Actor {self._actor_ref.actor_urn} has been garbage collected")
        return actor

    @property
    def agent_id(self) -> uuid.UUID:
        """Unique identifier from the underlying actor.

        Returns:
            UUID from the actor's agent_id attribute.
        """
        return self._resolve_actor().agent_id  # type: ignore[no-any-return]

    @property
    def name(self) -> str:
        """Agent name from the underlying actor's config.

        Returns:
            Name string from config, or string representation of actor_ref as fallback.
        """
        actor = self._resolve_actor()
        return actor.config.name  # type: ignore[no-any-return]

    @property
    def role(self) -> str:
        """Agent role from the underlying actor's config.

        Returns:
            Role string from config, or class name as fallback.
        """
        actor = self._resolve_actor()
        return actor.config.role  # type: ignore[no-any-return]

    @property
    def team_id(self) -> uuid.UUID:
        """Team identifier from the underlying actor.

        Returns:
            UUID from team_id.
        """
        actor = self._resolve_actor()
        return actor.team_id  # type: ignore[no-any-return]

    @property
    def squad_id(self) -> uuid.UUID | None:
        """Squad identifier from the underlying actor's config.

        Returns:
            UUID from config.squad_id, or None if not available.
        """
        actor = self._resolve_actor()
        return actor.config.squad_id  # type: ignore[no-any-return]

    def send(self, recipient: ActorAddress, message: Any) -> None:
        """Send a message via Pykka proxy.

        Uses the actor's send method via proxy to deliver the message.
        Blocks until the message is delivered.

        Args:
            recipient: The intended recipient of the message.
            message: The message to send.
        """
        self._actor_ref.proxy().send(recipient, message).get()

    def is_alive(self) -> bool:
        """Check if the underlying actor is still running.

        Returns:
            True if the Pykka actor is alive.
        """
        return self._actor_ref.is_alive()

    def handle_user_message(self) -> bool:
        """Check if the agent accepts user messages.

        Checks for the existence of a receiveMsg_UserMessage method on the actor.

        Returns:
            True if the actor has a receiveMsg_UserMessage method.
        """
        actor = self._resolve_actor()
        accept_method = getattr(actor, "receiveMsg_UserMessage", None)
        return callable(accept_method)

    def serialize(self) -> ActorAddressDict:
        """Serialize to dictionary for transport.

        Captures all metadata from the live actor for reconstruction.
        The __actor_type__ is set to the actual agent class, not the address wrapper.

        Returns:
            ActorAddressDict with all actor metadata.
        """
        agent_type = self._resolve_actor().__class__
        return {
            "__actor_address__": True,
            "__actor_type__": f"{agent_type.__module__}.{agent_type.__name__}",
            "agent_id": str(self.agent_id),
            "name": self.name,
            "role": self.role,
            "team_id": str(self.team_id),
            "squad_id": str(self.squad_id) if self.squad_id is not None else "",
            "user_message": self.handle_user_message(),
        }

    def __repr__(self) -> str:
        """String representation for debugging."""
        return f"<ActorAddress {self.role} {self.name} ({self.agent_id})>"

    def __eq__(self, other: object) -> bool:
        """Compare addresses by agent_id (same class only)."""
        if not isinstance(other, ActorAddressImpl):
            return False
        return self.agent_id == other.agent_id

    def __hash__(self) -> int:
        """Hash based on agent_id."""
        return hash(self.agent_id)


class ActorAddressProxy(ActorAddress):
    """ActorAddress representing a deserialized or mock address.

    Constructed from an ActorAddressDict, this class provides read-only
    access to address metadata without an underlying live actor. Used for:
    - Deserialized addresses from network messages
    - Mock addresses in tests
    - Phase 3 remote agent references

    Note:
        Cannot send messages - will raise RuntimeError if attempted.
        Assumes actor is alive (is_alive returns True).

    Args:
        address_dict: The ActorAddressDict containing address metadata.

    Example:
        >>> data = {"__actor_address__": True, "agent_id": "...", ...}
        >>> proxy = ActorAddressProxy(data)
        >>> print(proxy.name)
        'remote-agent'
    """

    def __init__(self, address_dict: ActorAddressDict) -> None:
        """Initialize from ActorAddressDict.

        Args:
            address_dict: Dictionary containing all address metadata.
        """
        self.actor_address_dict = address_dict

    @property
    def agent_id(self) -> uuid.UUID:
        """Unique identifier from the stored dictionary.

        Returns:
            UUID parsed from the agent_id string.
        """
        return uuid.UUID(self.actor_address_dict["agent_id"])

    @property
    def name(self) -> str:
        """Agent name from the stored dictionary.

        Returns:
            Name string from the dictionary.
        """
        return self.actor_address_dict["name"]

    @property
    def role(self) -> str:
        """Agent role from the stored dictionary.

        Returns:
            Role string from the dictionary.
        """
        return self.actor_address_dict["role"]

    @property
    def team_id(self) -> uuid.UUID:
        """Team identifier from the stored dictionary.

        Returns:
            UUID parsed from the team_id string.
        """
        return uuid.UUID(self.actor_address_dict["team_id"])

    @property
    def squad_id(self) -> uuid.UUID | None:
        """Squad identifier from the stored dictionary.

        Returns:
            UUID parsed from the squad_id string, or None if not present.
        """
        squad_id_str = self.actor_address_dict.get("squad_id")
        return uuid.UUID(squad_id_str) if squad_id_str else None

    def send(self, recipient: ActorAddress, message: Any) -> None:
        """Send a message via Pykka proxy.

        Args:
            recipient: Not used.
            message: Not used.

        Raises:
            RuntimeError: Always, as proxy addresses cannot send.
        """
        raise RuntimeError(
            f"Cannot send message from mock actor address {self.name} - actor not alive"
        )

    def is_alive(self) -> bool:
        """Assume the remote/proxied actor is alive.

        Returns:
            True (assumed alive for proxy addresses).
        """
        return True

    def handle_user_message(self) -> bool:
        """Check if this agent accepts user messages.

        Returns:
            Boolean from the stored dictionary, defaults to False if not present.
        """
        return self.actor_address_dict.get("user_message", False)

    def serialize(self) -> ActorAddressDict:
        """Return the stored dictionary.

        Returns:
            The original ActorAddressDict.
        """
        return self.actor_address_dict

    def __repr__(self) -> str:
        """String representation for debugging."""
        return f"<ActorAddressProxy {self.role} {self.name} ({self.agent_id})>"

    def __eq__(self, other: object) -> bool:
        """Compare addresses by agent_id (same class only)."""
        if not isinstance(other, ActorAddressProxy):
            return False
        return self.agent_id == other.agent_id

    def __hash__(self) -> int:
        """Hash based on agent_id."""
        return hash(self.agent_id)


class ActorAddressStopped(ActorAddressProxy):
    """ActorAddress representing a stopped actor.

    Inherits from ActorAddressProxy but overrides is_alive to return False.
    Used for tracking stopped actors in orchestrator history.

    Args:
        address_dict: The ActorAddressDict containing address metadata.

    Example:
        >>> stopped = ActorAddressStopped(original_address.serialize())
        >>> stopped.is_alive()
        False
    """

    def is_alive(self) -> bool:
        """Stopped actors are not alive.

        Returns:
            False (actor has been stopped).
        """
        return False

    def __repr__(self) -> str:
        """String representation for debugging."""
        return f"<ActorAddressStopped {self.role} {self.name} ({self.agent_id})>"

    def __eq__(self, other: object) -> bool:
        """Compare addresses by agent_id (same class only)."""
        if not isinstance(other, ActorAddressStopped):
            return False
        return self.agent_id == other.agent_id
