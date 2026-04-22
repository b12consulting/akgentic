"""LocalTeamHandle — community-tier TeamHandle implementation wrapping TeamRuntime."""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from akgentic.core.orchestrator import EventSubscriber

if TYPE_CHECKING:
    from akgentic.core.messages.message import Message
    from akgentic.team.models import TeamRuntime


class LocalTeamHandle:
    """Community-tier adapter that delegates TeamHandle methods to a TeamRuntime.

    Wraps an in-process ``TeamRuntime`` and exposes the tier-agnostic
    ``TeamHandle`` protocol so that ``TeamService`` can interact with
    teams without touching actor internals directly.
    """

    def __init__(self, runtime: TeamRuntime) -> None:
        self._runtime = runtime

    @property
    def team_id(self) -> uuid.UUID:
        """The unique identifier of the team this handle points to."""
        return self._runtime.id

    def send(self, content: str) -> None:
        """Send a message to the team's default entry point."""
        self._runtime.send(content)

    def send_to(self, agent_name: str, content: str) -> None:
        """Send a message to a specific agent within the team."""
        self._runtime.send_to(agent_name, content)

    def send_from_to(self, sender_name: str, recipient_name: str, content: str) -> None:
        """Send a message from a specific agent to another agent."""
        self._runtime.send_from_to(sender_name, recipient_name, content)

    def process_human_input(self, content: str, message: Message) -> None:
        """Route human input to the team's HumanProxy agent."""
        self._runtime.process_human_input(content, message)

    def subscribe(self, subscriber: EventSubscriber) -> None:
        """Register an event subscriber with the team's orchestrator."""
        self._runtime.orchestrator_proxy.subscribe(subscriber)

    def unsubscribe(self, subscriber: EventSubscriber) -> None:
        """Remove an event subscriber from the team's orchestrator."""
        self._runtime.orchestrator_proxy.unsubscribe(subscriber)
