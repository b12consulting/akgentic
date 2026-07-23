"""TeamHandle protocol — tier-agnostic team interaction abstraction."""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from akgentic.core.messages.message import Message
    from akgentic.core.orchestrator import EventSubscriber


@runtime_checkable
class TeamHandle(Protocol):
    """Tier-agnostic handle for interacting with a running team.

    Hides actor-internal details behind clean method calls so that
    ``TeamService`` can send messages, route human input, and manage
    event subscriptions without knowing the underlying tier implementation.

    Implementations: LocalTeamHandle (community), RemoteTeamHandle (department/enterprise).

    Error contract:
        - ``send()`` / ``send_to()`` raise ``ValueError`` if the team is no
          longer running (handle points to a dead team).
        - ``process_human_input()`` raises ``ValueError`` if the team's
          HumanProxy agent cannot be found or the team is not running.
        - ``subscribe()`` / ``unsubscribe()`` are best-effort — if the
          orchestrator has already stopped, they may silently fail.
    """

    @property
    def team_id(self) -> uuid.UUID:
        """The unique identifier of the team this handle points to."""
        ...

    def send(self, content: str | Message) -> None:
        """Send a message to the team's default entry point.

        Args:
            content: The message content — a ``str`` (wrapped in the team's
                default type) or a pre-formed ``Message`` routed untouched.
        """
        ...

    def send_to(self, agent_name: str, content: str | Message) -> None:
        """Send a message to a specific agent within the team.

        Args:
            agent_name: Name of the target agent.
            content: The message content — a ``str`` or a pre-formed ``Message``.
        """
        ...

    def send_from_to(self, sender_name: str, recipient_name: str, content: str | Message) -> None:
        """Send a message from a specific agent to another agent.

        Args:
            sender_name: Name of the agent to send from.
            recipient_name: Name of the agent to send to.
            content: The message content — a ``str`` or a pre-formed ``Message``.
        """
        ...

    def emitMessage(self, message: Message) -> None:  # noqa: N802
        """Publish a pre-formed message to the team's subscribers.

        Reaches the durable event store and the live stream with no agent
        processing and no outbound channel dispatch. Rationale: ADR-22.

        Args:
            message: The pre-formed message to publish.
        """
        ...

    def process_human_input(self, content: str, message: Message) -> None:
        """Route human input to the team's HumanProxy agent.

        Args:
            content: The human-provided content.
            message: The original Message object (already resolved by the caller).
        """
        ...

    def subscribe(self, subscriber: EventSubscriber) -> None:
        """Register an event subscriber with the team's orchestrator.

        Args:
            subscriber: The event subscriber to register.
        """
        ...

    def unsubscribe(self, subscriber: EventSubscriber) -> None:
        """Remove an event subscriber from the team's orchestrator.

        Args:
            subscriber: The event subscriber to remove.
        """
        ...
