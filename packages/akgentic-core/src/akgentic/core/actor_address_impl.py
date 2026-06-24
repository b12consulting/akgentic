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
    """ActorAddress wrapping a live Pykka ActorRef — a resilient snapshot.

    An ``ActorAddressImpl`` is a **point-in-time snapshot** of an actor's
    identity and metadata (ADR-013). Because the Pykka 4.4.2+ ``ActorRef`` holds
    only a *weakref* to the underlying actor (ADR-003), an address can outlive
    its actor: the actor is garbage-collected while the address lives on in
    orchestrator history, queued telemetry, or subscriber snapshots.

    To make that lifecycle safe, **all** metadata (``agent_id``, ``name``,
    ``role``, ``team_id``, ``squad_id``, the actor ``type`` and the
    ``user_message`` flag) is captured into private vars at construction. Every
    accessor, ``serialize()`` and ``__repr__`` read the cache — reading metadata
    or checking liveness NEVER raises on a collected actor. The live
    ``_actor_ref`` is retained for message *delivery* only (``send`` /
    ``proxy``).

    Note:
        Metadata reflects the actor's state at construction time, not a live
        view. If an actor's ``config`` is mutated after an address is built, an
        address constructed earlier keeps the old value. Addresses are
        short-lived (built per-send via ``myAddress``), so this snapshot
        semantics is correct for the dominant use.

    Args:
        actor_ref: The Pykka ActorRef to wrap.

    Example:
        >>> actor_ref = MyAgent.start(config=agent_config)
        >>> address = ActorAddressImpl(actor_ref)
        >>> print(address.name)
        'my-agent'
    """

    def __init__(self, actor_ref: ActorRef[Any]) -> None:
        """Initialize with a Pykka ActorRef, caching all metadata (ADR-013 §1).

        Addresses are only ever built from a live ref (``myAddress``,
        ``createActor``, ``ActorSystem`` lookups), so the underlying actor is
        alive here. Capture everything the address will ever need — identity,
        config metadata, the actor type and the user-message flag — so every
        later read survives the actor's garbage collection.

        The capture is wrapped: an already-dead ref at construction (rare) leaves
        a safe snapshot (``None``/``False``) rather than raising.

        Args:
            actor_ref: The Pykka ActorRef to wrap. Retained for delivery only.
        """
        self._actor_ref = actor_ref
        self._agent_id: uuid.UUID | None = None
        self._name: str | None = None
        self._role: str | None = None
        self._team_id: uuid.UUID | None = None
        self._squad_id: uuid.UUID | None = None
        self._actor_type: type[Any] | None = None
        self._user_message: bool = False
        try:
            actor = actor_ref._actor_weakref()
            if actor is not None:
                self._agent_id = actor.agent_id
                self._name = actor.config.name
                self._role = actor.config.role
                self._team_id = actor.team_id
                self._squad_id = actor.config.squad_id
                self._actor_type = type(actor)
                self._user_message = callable(getattr(actor, "receiveMsg_UserMessage", None))
        except Exception:  # noqa: BLE001
            # Already-dead ref at construction (rare): keep the safe snapshot.
            pass

    @property
    def agent_id(self) -> uuid.UUID:
        """Unique identifier (cached at construction; GC-safe — ADR-013 §2).

        Returns:
            UUID captured from the actor's ``agent_id`` at construction.
        """
        return self._agent_id  # type: ignore[return-value]

    @property
    def name(self) -> str:
        """Agent name (cached at construction; GC-safe — ADR-013 §2).

        Returns:
            Name string captured from ``config.name`` at construction.
        """
        return self._name  # type: ignore[return-value]

    @property
    def role(self) -> str:
        """Agent role (cached at construction; GC-safe — ADR-013 §2).

        Returns:
            Role string captured from ``config.role`` at construction.
        """
        return self._role  # type: ignore[return-value]

    @property
    def team_id(self) -> uuid.UUID:
        """Team identifier (cached at construction; GC-safe — ADR-013 §2).

        Returns:
            UUID captured from the actor's ``team_id`` at construction.
        """
        return self._team_id  # type: ignore[return-value]

    @property
    def squad_id(self) -> uuid.UUID | None:
        """Squad identifier (cached at construction; GC-safe — ADR-013 §2).

        Returns:
            UUID captured from ``config.squad_id`` at construction, or None.
        """
        return self._squad_id

    def send(self, recipient: ActorAddress, message: Any) -> None:
        """Send a message via Pykka proxy.

        Uses the live ``_actor_ref`` — the one operation that genuinely needs
        the live actor (ADR-013 §4). Blocks until the message is delivered.

        Args:
            recipient: The intended recipient of the message.
            message: The message to send.
        """
        self._actor_ref.proxy().send(recipient, message).get()

    def is_alive(self) -> bool:
        """Check if the underlying actor is still running (never raises).

        Liveness is best-effort: a torn-down or collected ref is simply
        "not alive" (ADR-013 §3).

        Returns:
            True if the Pykka actor is alive; False on any exception.
        """
        try:
            return self._actor_ref.is_alive()
        except Exception:  # noqa: BLE001 — a torn-down/collected ref is not alive
            return False

    def handle_user_message(self) -> bool:
        """Check if the agent accepts user messages (cached; GC-safe).

        Returns:
            True if the actor had a ``receiveMsg_UserMessage`` method at
            construction.
        """
        return self._user_message

    def serialize(self) -> ActorAddressDict:
        """Serialize to dictionary for transport (composed from the cache).

        Composes the dict on demand from the cached values — never touches the
        weakref (ADR-013 §2), so a stopped + GC'd address serializes cleanly.
        The ``__actor_type__`` is the actual agent class, not the address wrapper.

        Returns:
            ActorAddressDict with all cached actor metadata.
        """
        actor_type = self._actor_type
        return {
            "__actor_address__": True,
            "__actor_type__": (
                f"{actor_type.__module__}.{actor_type.__name__}" if actor_type else ""
            ),
            "agent_id": str(self._agent_id) if self._agent_id is not None else "",
            "name": self._name or "",
            "role": self._role or "",
            "team_id": str(self._team_id) if self._team_id is not None else "",
            "squad_id": str(self._squad_id) if self._squad_id is not None else "",
            "user_message": self._user_message,
        }

    def __repr__(self) -> str:
        """String representation for debugging (reads the cache)."""
        return f"<ActorAddress {self._role} {self._name} ({self._agent_id})>"

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
