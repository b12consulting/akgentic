"""TeamService — orchestrates catalog resolution and team lifecycle via protocols."""

from __future__ import annotations

import logging
import shutil
import uuid
from pathlib import Path

from akgentic.catalog.models.errors import CatalogValidationError, EntryNotFoundError
from akgentic.core.messages.orchestrator import SentMessage
from akgentic.infra.protocols.event_stream import EventStream
from akgentic.infra.protocols.runtime_cache import RuntimeCache
from akgentic.infra.protocols.team_handle import TeamHandle
from akgentic.infra.server.deps import TierServices
from akgentic.team.models import PersistedEvent, Process, TeamStatus

logger = logging.getLogger(__name__)


def _remove_workspace_dir(workspaces_root: Path, team_id: uuid.UUID) -> None:
    """Best-effort removal of a team's workspace directory.

    Removes ``{workspaces_root}/{team_id}`` recursively. A missing directory
    is a silent no-op (ephemeral teams that never invoked a ``Filesystem``
    write have no directory to clean). Any ``shutil.rmtree`` failure is logged
    at WARNING and suppressed so team deletion still completes in the system
    of record — a later janitor pass can sweep orphans.

    Generalized from akgentic-infra-enterprise's
    ``routes/enterprise_server_teams.py`` per Epic 24 (Tier-Alignment Fixes
    from Department + Enterprise); see ADR-022 §D7 for the original
    best-effort, log-not-raise rationale.
    """
    target = workspaces_root / str(team_id)
    if not target.exists():
        return
    try:
        shutil.rmtree(target)
    except Exception as exc:  # noqa: BLE001 — log-not-raise; cleanup is best-effort
        logger.warning(
            "Workspace cleanup failed — team_id=%s error=%s",
            team_id,
            exc,
        )


class TeamService:
    """Service layer bridging catalog resolution with team lifecycle management.

    Resolves catalog entry IDs to TeamCards, delegates lifecycle operations
    through PlacementStrategy and WorkerHandle protocols, and queries
    EventStore for listing. Delegates runtime interaction through
    RuntimeCache/TeamHandle protocols.
    """

    def __init__(self, services: TierServices, *, workspaces_root: Path) -> None:
        """Construct a TeamService.

        Args:
            services: Pre-wired tier services container.
            workspaces_root: Server-side root directory under which each
                team's workspace lives at ``{workspaces_root}/{team_id}/``.
                Used by ``delete_team`` for best-effort FS cleanup.
        """
        self._services = services
        self._cache: RuntimeCache = services.runtime_cache
        self._workspaces_root = workspaces_root

    def create_team(
        self,
        catalog_namespace: str,
        user_id: str,
        user_email: str = "",
        team_id: uuid.UUID | None = None,
    ) -> Process:
        """Resolve a catalog namespace to a TeamCard and create a running team.

        Loads the team definition via the v2 unified ``Catalog.load_team``
        API and forwards the namespace tag through placement so that the
        persisted ``Process.catalog_namespace`` records the binding.

        Args:
            catalog_namespace: v2 catalog namespace holding exactly one
                ``kind="team"`` entry.
            user_id: Identifier of the user creating the team.
            user_email: Email of the user creating the team.
            team_id: Optional caller-supplied team identifier; the placement
                layer auto-generates a UUID when None.

        Returns:
            The persisted ``Process`` for the newly created team.

        Raises:
            EntryNotFoundError: If ``catalog_namespace`` has no team entry.
                ``Catalog.load_team`` surfaces the condition as
                ``CatalogValidationError``; this layer translates it so
                the existing teams router's ``EntryNotFoundError → 404``
                handler applies unchanged.
        """
        logger.debug("Resolving team for catalog namespace: %s", catalog_namespace)
        try:
            team_card = self._services.catalog.load_team(catalog_namespace)
        except CatalogValidationError as exc:
            # Translate v2's validation error into the existing 404-mapped
            # exception so the teams router's error-handling stays a no-op
            # for this story (Story 18.3 consolidates error handling).
            raise EntryNotFoundError(catalog_namespace) from exc
        handle = self._services.placement.create_team(
            team_card,
            user_id,
            user_email=user_email,
            team_id=team_id,
            catalog_namespace=catalog_namespace,
        )
        self._cache.store(handle.team_id, handle)
        # Consistency invariant: create_team() writes to event store, so
        # get_team() must find it immediately. If this fires, there is a bug
        # in placement or event store — not a transient race condition.
        process = self._services.worker_handle.get_team(handle.team_id)
        if process is None:  # pragma: no cover
            msg = f"Team {handle.team_id} was created but not found in event store"
            raise RuntimeError(msg)
        logger.info(
            "Team created: team_id=%s, catalog_namespace=%s",
            process.team_id,
            catalog_namespace,
        )
        return process

    def list_teams(self, user_id: str) -> list[Process]:
        """List all teams for a given user.

        Pushes the ``user_id`` filter into the EventStore rather than loading
        every team into Python and filtering here. Per-request cost scales with
        the requesting user's team count, not with total teams across all
        users. See team-package ADR-16 / Epic 19 for the Protocol change.
        """
        return self._services.event_store.list_teams(user_id=user_id)

    def get_team(self, team_id: uuid.UUID) -> Process | None:
        """Get a single team by ID."""
        return self._services.worker_handle.get_team(team_id)

    def delete_team(self, team_id: uuid.UUID) -> None:
        """Stop (if running) and delete a team.

        After the team is removed from the system of record, the team's
        workspace directory (``{workspaces_root}/{team_id}/``) is removed on a
        best-effort basis — a missing directory or an ``rmtree`` failure does
        not prevent deletion from completing.

        Raises:
            ValueError: If team not found or already deleted. Raised before
                any filesystem work, so a missing team never triggers FS
                cleanup.
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
        # FS cleanup runs LAST — after the worker-side delete — so a worker
        # delete failure does not leave behind a removed workspace dir.
        _remove_workspace_dir(self._workspaces_root, team_id)
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
        # _find_message resolves by inner id and returns only SentMessage, so
        # event.message is the inner Message to route (ADR-027 §Decision 1).
        event = self._find_message(team_id, message_id)
        handle.process_human_input(content, event.message)
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

    def _find_message(self, team_id: uuid.UUID, message_id: str) -> SentMessage:
        """Find a SentMessage by its inner ``message.id`` in persisted events.

        Mirrors the worker route's ``_find_message``: resolution is by the
        **inner** ``SentMessage.message.id`` — the id every distributed tier
        puts on the wire — not the outer envelope ``SentMessage.id``
        (ADR-027 §Decision 1).

        Raises:
            ValueError: If no matching SentMessage is found. The ``not found``
                substring is load-bearing for the 404 mapping.
        """
        events = self._services.event_store.load_events(team_id)
        for ev in events:
            if isinstance(ev.event, SentMessage) and str(ev.event.message.id) == message_id:
                return ev.event
        msg = f"Message {message_id} not found"
        raise ValueError(msg)
