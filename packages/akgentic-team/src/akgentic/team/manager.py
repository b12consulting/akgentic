"""TeamManager: lifecycle facade for create, resume, stop, delete operations."""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime

from akgentic.core.actor_system_impl import ActorSystem
from akgentic.core.orchestrator import EventSubscriber, Orchestrator
from akgentic.team.factory import TeamFactory
from akgentic.team.models import Process, TeamCard, TeamRuntime, TeamStatus
from akgentic.team.ports import EventStore, NullServiceRegistry, ServiceRegistry
from akgentic.team.restorer import TeamRestorer
from akgentic.team.subscriber import PersistenceSubscriber

logger = logging.getLogger(__name__)


class TeamManager:
    """Single facade for team lifecycle: create, get, resume, stop, delete.

    Coordinates TeamFactory, PersistenceSubscriber, EventStore, and
    ServiceRegistry to provide a unified API for managing team instances
    with consistent state machine enforcement.

    The state machine enforces: RUNNING -> STOPPED -> DELETED.
    Operations that violate this flow raise ValueError.
    """

    def __init__(
        self,
        actor_system: ActorSystem,
        event_store: EventStore,
        service_registry: ServiceRegistry | None = None,
        subscribers: list[EventSubscriber] | None = None,
        instance_id: uuid.UUID | None = None,
    ) -> None:
        """Initialize TeamManager with injected dependencies.

        Args:
            actor_system: The actor system to host team actors.
            event_store: Persistence backend for team state and events.
            service_registry: Service discovery registry. Defaults to
                NullServiceRegistry for single-process mode.
            subscribers: Pre-instantiated list of long-lived EventSubscribers
                shared across all teams. These must be thread-safe since
                different teams' orchestrators may call on_message()
                concurrently from different actor threads.
                PersistenceSubscriber is per-team and created internally
                by TeamManager.
            instance_id: Worker instance identifier. Auto-generated if None.
        """
        self._actor_system = actor_system
        self._event_store = event_store
        self._service_registry = service_registry or NullServiceRegistry()
        self._shared_subscribers = subscribers or []
        self._instance_id = instance_id or uuid.uuid4()
        self._runtimes: dict[uuid.UUID, TeamRuntime] = {}
        self._team_subscribers: dict[uuid.UUID, list[EventSubscriber]] = {}
        # Tracks teams with an attached TimerStopSubscriber so
        # re-entry (e.g. resume → stop → resume) does not stack multiple
        # bridges that would each spawn a daemon thread on timer-stop.
        self._stop_subscriber_attached: set[uuid.UUID] = set()

    def create_team(
        self,
        team_card: TeamCard,
        user_id: str = "cli",
        user_email: str = "",
    ) -> TeamRuntime:
        """Create and start a new team from a TeamCard.

        Pre-generates a team_id, creates a PersistenceSubscriber (always first),
        appends shared subscribers, then delegates to TeamFactory.build.
        On successful build, persists a Process with RUNNING status and registers
        the team with the ServiceRegistry.

        If build fails, the exception propagates without persisting any Process.

        Args:
            team_card: Declarative team definition.
            user_id: Identifier of the user creating the team.
            user_email: Email of the user creating the team.

        Returns:
            A TeamRuntime handle to the running team.

        Raises:
            ValueError: If the TeamCard is invalid (e.g. entry_point headcount != 1).
            Exception: Any exception from TeamFactory.build propagates unchanged.
        """
        team_id = uuid.uuid4()
        logger.info("Creating team '%s' with id %s", team_card.name, team_id)

        # Build subscriber list: PersistenceSubscriber always first
        persistence_sub = PersistenceSubscriber(team_id, self._event_store)
        subscribers: list[EventSubscriber] = [persistence_sub] + list(self._shared_subscribers)

        # Build the team — if this raises, no Process is persisted
        runtime = TeamFactory.build(team_card, self._actor_system, subscribers, team_id=team_id)

        # Track runtime and subscribers for stop_team
        self._runtimes[team_id] = runtime
        self._team_subscribers[team_id] = subscribers

        # Persist Process metadata
        now = datetime.now(UTC)
        process = Process(
            team_id=team_id,
            team_card=team_card,
            status=TeamStatus.RUNNING,
            user_id=user_id,
            user_email=user_email,
            created_at=now,
            updated_at=now,
        )
        self._event_store.save_team(process)

        # Register with service discovery
        self._service_registry.register_team(self._instance_id, team_id)

        # Bridge orchestrator inactivity-timer stop → stop_team so the
        # event store transitions to STOPPED when the timer fires.
        self._attach_stop_subscriber(team_id, runtime)

        logger.info("Team '%s' (%s) created successfully", team_card.name, team_id)
        return runtime

    def get_team(self, team_id: uuid.UUID) -> Process | None:
        """Retrieve Process metadata for a team.

        Args:
            team_id: The team identifier to look up.

        Returns:
            The Process if found, None otherwise.
        """
        return self._event_store.load_team(team_id)

    def delete_team(self, team_id: uuid.UUID) -> None:
        """Delete a stopped team, purging all persisted data.

        Enforces the state machine: only STOPPED teams can be deleted.

        Args:
            team_id: The team identifier to delete.

        Raises:
            ValueError: If the team is not found, is currently RUNNING,
                or is already DELETED.
        """
        process = self._event_store.load_team(team_id)
        if process is None:
            logger.warning("Delete rejected: team %s not found", team_id)
            msg = f"Team {team_id} not found"
            raise ValueError(msg)
        if process.status == TeamStatus.RUNNING:
            logger.warning("Delete rejected: team %s is currently running", team_id)
            msg = f"Cannot delete team {team_id}: team is currently running. Stop it first."
            raise ValueError(msg)
        if process.status == TeamStatus.DELETED:
            logger.warning("Delete rejected: team %s is already deleted", team_id)
            msg = f"Team {team_id} is already deleted"
            raise ValueError(msg)

        # STOPPED — purge all data and deregister from service discovery
        logger.info("Deleting team %s", team_id)
        self._event_store.delete_team(team_id)
        self._service_registry.deregister_team(self._instance_id, team_id)

        # Cleanup runtime tracking
        self._runtimes.pop(team_id, None)
        self._team_subscribers.pop(team_id, None)
        self._stop_subscriber_attached.discard(team_id)

    def resume_team(self, team_id: uuid.UUID) -> TeamRuntime:
        """Resume a stopped team by restoring from persisted EventStore data.

        Loads the Process, validates state machine (only STOPPED teams may
        resume), delegates to TeamRestorer, then updates Process status to
        RUNNING and registers with ServiceRegistry.

        Args:
            team_id: The team identifier to resume.

        Returns:
            A TeamRuntime handle to the resumed team.

        Raises:
            ValueError: If the team is not found, is currently RUNNING,
                or is already DELETED.
        """
        process = self._event_store.load_team(team_id)
        if process is None:
            logger.warning("Resume rejected: team %s not found", team_id)
            msg = f"Team {team_id} not found"
            raise ValueError(msg)
        if process.status == TeamStatus.RUNNING:
            logger.warning("Resume rejected: team %s is currently running", team_id)
            msg = f"Cannot resume team {team_id}: team is currently running"
            raise ValueError(msg)
        if process.status == TeamStatus.DELETED:
            logger.warning("Resume rejected: team %s has been deleted", team_id)
            msg = f"Cannot resume team {team_id}: team has been deleted"
            raise ValueError(msg)

        restorer = TeamRestorer(self._actor_system, self._event_store)

        # Compute max existing sequence so new events continue monotonically
        max_seq = self._event_store.get_max_sequence(team_id)

        # Create PersistenceSubscriber once — passed to restorer and tracked for stop
        persistence_sub = PersistenceSubscriber(
            team_id, self._event_store, initial_sequence=max_seq
        )
        all_subs: list[EventSubscriber] = [persistence_sub] + list(self._shared_subscribers)

        # Toggle restoring guard on all subscribers.
        # Each subscriber decides independently whether to skip during restore.
        for sub in all_subs:
            sub.set_restoring(True)

        try:
            runtime = restorer.restore(process, subscribers=all_subs)
        finally:
            for sub in all_subs:
                sub.set_restoring(False)

        # Track runtime and subscribers for stop_team
        self._runtimes[team_id] = runtime
        self._team_subscribers[team_id] = all_subs

        now = datetime.now(UTC)
        updated_process = Process(
            team_id=process.team_id,
            team_card=process.team_card,
            status=TeamStatus.RUNNING,
            user_id=process.user_id,
            user_email=process.user_email,
            created_at=process.created_at,
            updated_at=now,
        )
        self._event_store.save_team(updated_process)

        self._service_registry.register_team(self._instance_id, team_id)

        # Bridge orchestrator inactivity-timer stop → stop_team so the
        # event store transitions to STOPPED when the timer fires.
        self._attach_stop_subscriber(team_id, runtime)

        logger.info("Team '%s' (%s) resumed successfully", process.team_card.name, team_id)
        return runtime

    def _attach_stop_subscriber(
        self, team_id: uuid.UUID, runtime: TeamRuntime
    ) -> None:
        """Auto-attach a :class:`TimerStopSubscriber` to a team's orchestrator.

        The subscriber bridges the core orchestrator's inactivity-timer
        ``on_stop_request`` fan-out back into :meth:`stop_team`, so teams
        that stop via the timer transition their persisted ``Process.status``
        to ``STOPPED`` instead of leaving a ghost ``RUNNING`` entry.

        Intentionally NOT tracked in ``_team_subscribers``: the subscriber
        triggers ``stop_team``, which itself unsubscribes tracked
        subscribers; tracking the bridge would self-unsubscribe during
        its own firing.

        Idempotent: if this team already has a bridge attached (e.g. a
        prior ``create_team`` / ``resume_team`` succeeded), the method
        is a no-op. The attached flag is cleared in ``stop_team`` once
        the orchestrator is torn down.

        Attachment failure is logged and swallowed — a failed bridge
        must not prevent team creation/resume from succeeding.
        """
        # Lazy-import keeps the team↔subscriber circular dependency explicit.
        from akgentic.team.subscriber import TimerStopSubscriber

        assert runtime.id == team_id, (
            f"runtime.id ({runtime.id}) must match team_id ({team_id})"
        )

        if team_id in self._stop_subscriber_attached:
            logger.debug(
                "TimerStopSubscriber already attached for team %s — skipping",
                team_id,
            )
            return

        try:
            orchestrator_proxy: Orchestrator = self._actor_system.proxy_ask(
                runtime.orchestrator_addr, Orchestrator
            )
            subscriber = TimerStopSubscriber(self, team_id)
            orchestrator_proxy.subscribe(subscriber)
            self._stop_subscriber_attached.add(team_id)
            logger.debug("attached TimerStopSubscriber to team_id=%s", team_id)
        except Exception:
            logger.warning(
                "Failed to attach TimerStopSubscriber to team %s",
                team_id,
                exc_info=True,
            )

    def _teardown_team(self, team_id: uuid.UUID, runtime: TeamRuntime) -> None:
        """Unsubscribe all subscribers and tear down actors for a running team.

        Best-effort teardown: individual failures are logged but do not prevent
        the remaining teardown steps from executing.

        Args:
            team_id: The team identifier being stopped.
            runtime: The active TeamRuntime containing actor addresses.
        """
        # Unsubscribe all tracked subscribers and stop orchestrator via proxy.
        # Using proxy_ask ensures Orchestrator.stop() is invoked (cancels timer,
        # recursive child teardown) instead of bypassing via raw Pykka stop.
        try:
            orchestrator_proxy: Orchestrator = self._actor_system.proxy_ask(
                runtime.orchestrator_addr, Orchestrator
            )
            for sub in self._team_subscribers.get(team_id, []):
                try:
                    orchestrator_proxy.unsubscribe(sub)
                except Exception:
                    logger.warning(
                        "Failed to unsubscribe %s from team %s",
                        sub,
                        team_id,
                        exc_info=True,
                    )
            orchestrator_proxy.stop()
        except Exception:
            logger.warning(
                "Failed to teardown orchestrator for team %s",
                team_id,
                exc_info=True,
            )

    def stop_team(self, team_id: uuid.UUID) -> None:
        """Gracefully stop a running team.

        Unsubscribes all subscribers from the Orchestrator, stops the
        orchestrator via proxy (which recursively tears down all child actors),
        persists Process with STOPPED status, and deregisters from ServiceRegistry.

        Idempotent: calling stop on an already-STOPPED team is a no-op.

        Args:
            team_id: The team identifier to stop.

        Raises:
            ValueError: If the team is not found or is already DELETED.
        """
        process = self._event_store.load_team(team_id)
        if process is None:
            logger.warning("Stop rejected: team %s not found", team_id)
            msg = f"Team {team_id} not found"
            raise ValueError(msg)

        if process.status == TeamStatus.STOPPED:
            logger.info("Team %s is already stopped — no-op", team_id)
            return

        if process.status == TeamStatus.DELETED:
            logger.warning("Stop rejected: team %s no longer exists", team_id)
            msg = f"Team {team_id} no longer exists"
            raise ValueError(msg)

        # RUNNING — perform graceful shutdown
        runtime = self._runtimes.get(team_id)

        if runtime is not None:
            self._teardown_team(team_id, runtime)
        else:
            logger.warning(
                "Team %s is RUNNING but no runtime tracked — "
                "actors may already be dead. Updating state only.",
                team_id,
            )

        # Persist STOPPED status
        now = datetime.now(UTC)
        updated_process = Process(
            team_id=process.team_id,
            team_card=process.team_card,
            status=TeamStatus.STOPPED,
            user_id=process.user_id,
            user_email=process.user_email,
            created_at=process.created_at,
            updated_at=now,
        )
        self._event_store.save_team(updated_process)

        # Deregister from service discovery
        self._service_registry.deregister_team(self._instance_id, team_id)

        # Cleanup runtime tracking
        self._runtimes.pop(team_id, None)
        self._team_subscribers.pop(team_id, None)
        self._stop_subscriber_attached.discard(team_id)

        logger.info("Team %s stopped successfully", team_id)
