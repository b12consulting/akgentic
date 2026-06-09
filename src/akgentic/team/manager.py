"""TeamManager: lifecycle facade for create, resume, stop, delete operations."""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime

from akgentic.core.actor_system_impl import ActorSystem
from akgentic.core.orchestrator import STOP_TIMEOUT, EventSubscriber, Orchestrator
from akgentic.team.factory import TeamFactory
from akgentic.team.models import Process, TeamCard, TeamRuntime, TeamStatus
from akgentic.team.ports import EventStore, NullServiceRegistry, ServiceRegistry
from akgentic.team.restorer import TeamRestorer
from akgentic.team.subscriber import PersistenceSubscriber, TimerStopSubscriber

logger = logging.getLogger(__name__)

# Grace period passed into the non-blocking ``Orchestrator.stop(grace_timeout)``
# at every orchestrator-stop site. It is the single stop timeout (ADR-19 §4):
# core's backstop guarantees the returned event is set within ~this many seconds,
# so callers ``.wait()`` with NO separate caller-side timeout. Defaulted to core's
# ``STOP_TIMEOUT`` so the team grace period tracks the framework default.
GRACE_TIMEOUT_SECONDS = STOP_TIMEOUT


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
                ``PersistenceSubscriber`` and ``TimerStopSubscriber`` are
                per-team and constructed internally by ``TeamManager`` in
                ``create_team`` / ``resume_team``.
            instance_id: Worker instance identifier. Auto-generated if None.
        """
        self._actor_system = actor_system
        self._event_store = event_store
        self._service_registry = service_registry or NullServiceRegistry()
        self._shared_subscribers = subscribers or []
        self._instance_id = instance_id or uuid.uuid4()
        self._runtimes: dict[uuid.UUID, TeamRuntime] = {}

    def create_team(
        self,
        team_card: TeamCard,
        user_id: str = "cli",
        user_email: str = "",
        team_id: uuid.UUID | None = None,
        catalog_namespace: str | None = None,
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
            team_id: Optional team identifier. Auto-generated if None.
            catalog_namespace: Optional opaque tag identifying the catalog
                namespace this team was instantiated from. Stored verbatim on
                the persisted ``Process``; ``akgentic-team`` does not interpret
                it. Consumers read it back via ``get_team(team_id)``.

        Returns:
            A TeamRuntime handle to the running team.

        Raises:
            ValueError: If the TeamCard is invalid (e.g. entry_point headcount != 1).
            Exception: Any exception from TeamFactory.build propagates unchanged.
        """
        if team_id is None:
            team_id = uuid.uuid4()
        logger.info("Creating team '%s' with id %s", team_card.name, team_id)

        # Build subscriber list: per-team subscribers first, shared subscribers behind
        persistence_sub = PersistenceSubscriber(team_id, self._event_store)
        timer_stop_sub = TimerStopSubscriber(self, team_id)
        subscribers: list[EventSubscriber] = [
            persistence_sub,
            timer_stop_sub,
            *self._shared_subscribers,
        ]

        # Build the team — if this raises, no Process is persisted
        runtime = TeamFactory.build(team_card, self._actor_system, subscribers, team_id=team_id)

        # Track runtime for stop_team
        self._runtimes[team_id] = runtime

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
            catalog_namespace=catalog_namespace,
        )
        self._event_store.save_team(process)

        # Register with service discovery
        self._service_registry.register_team(self._instance_id, team_id)

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

        # Create per-team subscribers once — passed to restorer and tracked for stop
        persistence_sub = PersistenceSubscriber(
            team_id, self._event_store, initial_sequence=max_seq
        )
        timer_stop_sub = TimerStopSubscriber(self, team_id)
        all_subs: list[EventSubscriber] = [
            persistence_sub,
            timer_stop_sub,
            *self._shared_subscribers,
        ]

        # Toggle restoring guard on all subscribers.
        # Each subscriber decides independently whether to skip during restore.
        for sub in all_subs:
            sub.set_restoring(team_id, True)

        try:
            runtime = restorer.restore(process, subscribers=all_subs)
        finally:
            for sub in all_subs:
                sub.set_restoring(team_id, False)

        # Track runtime for stop_team
        self._runtimes[team_id] = runtime

        now = datetime.now(UTC)
        updated_process = Process(
            team_id=process.team_id,
            team_card=process.team_card,
            status=TeamStatus.RUNNING,
            user_id=process.user_id,
            user_email=process.user_email,
            created_at=process.created_at,
            updated_at=now,
            catalog_namespace=process.catalog_namespace,
        )
        self._event_store.save_team(updated_process)

        self._service_registry.register_team(self._instance_id, team_id)

        logger.info("Team '%s' (%s) resumed successfully", process.team_card.name, team_id)
        return runtime

    def _teardown_team(self, team_id: uuid.UUID, runtime: TeamRuntime) -> None:
        """Trigger graceful orchestrator stop via proxy and wait for completion.

        ``Orchestrator.stop(grace_timeout)`` is non-blocking (akgentic-core
        ADR-012): the ``proxy_ask`` resolves immediately and returns a
        ``threading.Event`` that core sets once teardown is complete (mailbox
        drained, subscribers' ``on_stop`` fired, actor deregistered). We
        ``.wait()`` on that event — on this caller/daemon thread, NOT the
        orchestrator's actor thread — so ``stop_team`` only persists ``STOPPED``
        and deregisters AFTER the actors are actually down.

        The wait takes no timeout: core's backstop guarantees the event is set
        within ~``GRACE_TIMEOUT_SECONDS``, so it cannot hang and always returns
        ``True``. There is therefore no ``if not stopped:`` branch — the
        "had to force it" WARNING lives in core's ``_force_stop``.

        The orchestrator's own ``on_stop`` fans out ``on_stop(team_id)`` to
        every attached subscriber *before* tearing actors down and clearing
        the subscriber list — see ``akgentic-core``
        ``Orchestrator.on_stop``. ``TeamManager`` therefore no longer
        unsubscribes subscribers itself; doing so would strip the list
        before the orchestrator could deliver the lifecycle signal.

        Best-effort: any failure is logged at WARNING but does not raise.

        Args:
            team_id: The team identifier being stopped.
            runtime: The active TeamRuntime containing actor addresses.
        """
        try:
            orchestrator_proxy: Orchestrator = self._actor_system.proxy_ask(
                runtime.orchestrator_addr, Orchestrator
            )
            orchestrator_proxy.stop(GRACE_TIMEOUT_SECONDS).wait()
        except Exception:
            logger.warning(
                "Failed to teardown orchestrator for team %s",
                team_id,
                exc_info=True,
            )

    def stop_team(self, team_id: uuid.UUID) -> None:
        """Gracefully stop a running team.

        Stops the orchestrator via proxy (which fans out ``on_stop`` to
        subscribers and recursively tears down all child actors), persists
        Process with STOPPED status, and deregisters from ServiceRegistry.

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
            catalog_namespace=process.catalog_namespace,
        )
        self._event_store.save_team(updated_process)

        # Deregister from service discovery
        self._service_registry.deregister_team(self._instance_id, team_id)

        # Cleanup runtime tracking
        self._runtimes.pop(team_id, None)

        logger.info("Team %s stopped successfully", team_id)
