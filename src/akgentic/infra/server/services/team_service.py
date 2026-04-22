"""TeamService — orchestrates catalog resolution and team lifecycle via protocols."""

from __future__ import annotations

import logging
import uuid

from akgentic.catalog.models.errors import EntryNotFoundError
from akgentic.core.messages.message import Message
from akgentic.core.messages.orchestrator import SentMessage
from akgentic.infra.protocols.event_stream import EventStream
from akgentic.infra.protocols.runtime_cache import RuntimeCache
from akgentic.infra.protocols.team_handle import TeamHandle
from akgentic.infra.server.deps import TierServices
from akgentic.team.models import PersistedEvent, Process, TeamStatus

logger = logging.getLogger(__name__)


class TeamService:
    """Service layer bridging catalog resolution with team lifecycle management.

    Resolves catalog entry IDs to TeamCards, delegates lifecycle operations
    through PlacementStrategy and WorkerHandle protocols, and queries
    EventStore for listing. Delegates runtime interaction through
    RuntimeCache/TeamHandle protocols.
    """

    def __init__(self, services: TierServices) -> None:
        self._services = services
        self._cache: RuntimeCache = services.runtime_cache

    def create_team(self, catalog_entry_id: str, user_id: str) -> Process:
        """Resolve catalog entry and create a running team.

        Raises:
            EntryNotFoundError: If catalog_entry_id is not found.
        """
        logger.debug("Resolving catalog entry: %s", catalog_entry_id)
        entry = self._services.team_catalog.get(catalog_entry_id)
        if entry is None:
            raise EntryNotFoundError(catalog_entry_id)
        team_card = entry.to_team_card(
            self._services.agent_catalog,
            self._services.tool_catalog,
            self._services.template_catalog,
        )
        handle = self._services.placement.create_team(team_card, user_id)
        self._cache.store(handle.team_id, handle)
        # Consistency invariant: create_team() writes to event store, so
        # get_team() must find it immediately. If this fires, there is a bug
        # in placement or event store — not a transient race condition.
        process = self._services.worker_handle.get_team(handle.team_id)
        if process is None:  # pragma: no cover
            msg = f"Team {handle.team_id} was created but not found in event store"
            raise RuntimeError(msg)
        logger.info("Team created: team_id=%s, catalog_entry=%s", process.team_id, catalog_entry_id)
        return process

    def list_teams(self, user_id: str) -> list[Process]:
        """List all teams for a given user."""
        all_teams = self._services.event_store.list_teams()
        return [t for t in all_teams if t.user_id == user_id]

    def get_team(self, team_id: uuid.UUID) -> Process | None:
        """Get a single team by ID."""
        return self._services.worker_handle.get_team(team_id)

    def delete_team(self, team_id: uuid.UUID) -> None:
        """Stop (if running) and delete a team.

        Raises:
            ValueError: If team not found or already deleted.
        """
        process = self._services.worker_handle.get_team(team_id)
        if process is None:
            msg = f"Team {team_id} not found"
            raise ValueError(msg)
        if process.status == TeamStatus.RUNNING:
            self._services.worker_handle.stop_team(team_id)
        self._cache.remove(team_id)
        # Safety net: remove ephemeral stream if not already removed on stop
        try:
            self._services.event_stream.remove(team_id)
        except Exception:
            logger.debug("event_stream.remove() on delete — stream may already be removed")
        self._services.worker_handle.delete_team(team_id)
        logger.info("Team deleted: team_id=%s", team_id)

    def send_message(self, team_id: uuid.UUID, content: str) -> None:
        """Send a message to a running team.

        Raises:
            ValueError: If team not found or not running.
        """
        handle = self._get_running_handle(team_id)
        handle.send(content)
        logger.debug("Message sent to team %s", team_id)

    def send_message_to(self, team_id: uuid.UUID, agent_name: str, content: str) -> None:
        """Send a message to a specific agent in a running team.

        Raises:
            ValueError: If team not found, not running, or agent not found.
        """
        handle = self._get_running_handle(team_id)
        handle.send_to(agent_name, content)
        logger.debug("Message sent to agent '%s' in team %s", agent_name, team_id)

    def send_message_from_to(
        self, team_id: uuid.UUID, sender_name: str, recipient_name: str, content: str
    ) -> None:
        """Send a message from a specific agent to another agent in a running team.

        Raises:
            ValueError: If team not found, not running, sender not found, or recipient not found.
        """
        handle = self._get_running_handle(team_id)
        handle.send_from_to(sender_name, recipient_name, content)
        logger.debug(
            "Message sent from '%s' to '%s' in team %s", sender_name, recipient_name, team_id
        )

    def process_human_input(
        self,
        team_id: uuid.UUID,
        content: str,
        message_id: str,
    ) -> None:
        """Route human input to HumanProxy for a specific message.

        Raises:
            ValueError: If team not found, not running, or message not found.
        """
        handle = self._get_running_handle(team_id)
        event = self._find_message(team_id, message_id)
        if not isinstance(event, SentMessage):
            msg = f"Message {message_id} is a {type(event).__name__}, expected SentMessage"
            raise ValueError(msg)
        inner = event.message
        handle.process_human_input(content, inner)
        logger.debug("Human input routed to team %s, message_id=%s", team_id, message_id)

    def stop_team(self, team_id: uuid.UUID) -> None:
        """Stop a running team without deleting persisted data.

        Raises:
            ValueError: If team not found or not in a stoppable state.
        """
        process = self._services.worker_handle.get_team(team_id)
        if process is None:
            msg = f"Team {team_id} not found"
            raise ValueError(msg)
        if process.status == TeamStatus.STOPPED:
            msg = f"Team {team_id} is already stopped"
            raise ValueError(msg)
        if process.status == TeamStatus.DELETED:
            msg = f"Team {team_id} has been deleted"
            raise ValueError(msg)
        self._services.worker_handle.stop_team(team_id)
        self._cache.remove(team_id)
        try:
            self._services.event_stream.remove(team_id)
        except Exception:
            logger.debug("event_stream.remove() on stop — stream may already be removed")
        logger.info("Team stopped: team_id=%s", team_id)

    def restore_team(self, team_id: uuid.UUID) -> Process:
        """Restore a stopped team.

        Raises:
            ValueError: If team not found or not in a restorable state.
        """
        process = self._services.worker_handle.get_team(team_id)
        if process is None:
            msg = f"Team {team_id} not found"
            raise ValueError(msg)
        if process.status == TeamStatus.RUNNING:
            msg = f"Team {team_id} is already running"
            raise ValueError(msg)
        if process.status == TeamStatus.DELETED:
            msg = f"Team {team_id} has been deleted"
            raise ValueError(msg)
        handle = self._services.worker_handle.resume_team(team_id)
        self._cache.store(handle.team_id, handle)
        updated = self._services.worker_handle.get_team(team_id)
        if updated is None:  # pragma: no cover
            msg = f"Team {team_id} was restored but not found in event store"
            raise RuntimeError(msg)
        logger.info("Team restored: team_id=%s", team_id)
        return updated

    def get_events(self, team_id: uuid.UUID) -> list[PersistedEvent]:
        """Get all persisted events for a team.

        Raises:
            ValueError: If team not found.
        """
        process = self._services.worker_handle.get_team(team_id)
        if process is None:
            msg = f"Team {team_id} not found"
            raise ValueError(msg)
        logger.debug("Loading events for team %s", team_id)
        return self._services.event_store.load_events(team_id)

    def get_event_stream(self) -> EventStream:
        """Return the tier's EventStream for cursor-based replay and fan-out."""
        return self._services.event_stream

    def get_handle(self, team_id: uuid.UUID) -> TeamHandle | None:
        """Return the cached TeamHandle for a team, or None if not cached.

        Args:
            team_id: Team UUID.

        Returns:
            TeamHandle if cached, else None.
        """
        return self._cache.get(team_id)

    def _get_running_handle(self, team_id: uuid.UUID) -> TeamHandle:
        """Look up a cached handle, verifying the team is running.

        Raises:
            ValueError: If team not found, not running, or handle not cached.
        """
        process = self._services.worker_handle.get_team(team_id)
        if process is None:
            msg = f"Team {team_id} not found"
            raise ValueError(msg)
        if process.status != TeamStatus.RUNNING:
            msg = f"Team {team_id} is not running"
            raise ValueError(msg)
        logger.debug("Resolving running handle for team %s", team_id)
        handle = self._cache.get(team_id)
        if handle is None:
            msg = f"Team {team_id} handle not cached"
            raise ValueError(msg)
        return handle

    def _find_message(self, team_id: uuid.UUID, message_id: str) -> Message:
        """Find a message by ID in persisted events.

        Raises:
            ValueError: If message not found.
        """
        events = self._services.event_store.load_events(team_id)
        for ev in events:
            if str(ev.event.id) == message_id:
                return ev.event
        msg = f"Message {message_id} not found"
        raise ValueError(msg)
