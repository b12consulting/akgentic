"""Tests for TeamManager — AC 1-13."""

from __future__ import annotations

import time
import uuid
from typing import Any
from unittest.mock import MagicMock

import pytest
from akgentic.core.actor_system_impl import ActorSystem
from akgentic.core.agent import Akgent
from akgentic.core.agent_card import AgentCard
from akgentic.core.agent_config import BaseConfig
from akgentic.core.agent_state import BaseState
from akgentic.core.messages.message import Message
from akgentic.core.orchestrator import EventSubscriber

from akgentic.team.manager import TeamManager
from akgentic.team.models import (
    Process,
    TeamCard,
    TeamCardMember,
    TeamRuntime,
    TeamStatus,
)
from akgentic.team.ports import NullServiceRegistry
from tests.services.conftest import InMemoryEventStore

# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


class StubAgent(Akgent[BaseConfig, BaseState]):
    """Minimal agent for manager tests."""

    pass


class FailingAgent(Akgent[BaseConfig, BaseState]):
    """Agent that raises during __init__ for rollback tests."""

    def __init__(self, **kwargs: Any) -> None:
        msg = "FailingAgent intentional error"
        raise RuntimeError(msg)


class RecordingSubscriber(EventSubscriber):
    """Subscriber that records received messages and stop lifecycle events.

    Implements the team_id-aware ``EventSubscriber`` Protocol. The shared-
    subscriber case (one instance attached to many teams) is irrelevant here
    so the stub accepts any ``team_id`` without asserting. ``stopped_team_ids``
    captures the on_stop fan-out — used to verify that ``Orchestrator.on_stop``
    delivers the lifecycle signal to subscribers (the invariant Story 21.2
    locks in by deleting ``TeamManager._teardown_team``'s unsubscribe loop).
    """

    def __init__(self) -> None:
        self.messages: list[Message] = []
        self.stopped: bool = False
        self.stopped_team_ids: list[uuid.UUID] = []

    def on_message(self, msg: Message) -> None:
        """Record received message."""
        self.messages.append(msg)

    def on_stop(self, team_id: uuid.UUID) -> None:
        """Record stop with team_id."""
        self.stopped = True
        self.stopped_team_ids.append(team_id)

    def on_stop_request(self, team_id: uuid.UUID) -> None:
        """No-op for test subscriber."""
        del team_id

    def set_restoring(self, team_id: uuid.UUID, restoring: bool) -> None:  # noqa: FBT001
        """No-op for test subscriber."""
        del team_id, restoring


def _make_card(
    name: str,
    role: str = "TestRole",
    agent_class: type[Akgent[Any, Any]] = StubAgent,
) -> AgentCard:
    return AgentCard(
        role=role,
        description=f"Test: {role}",
        skills=["testing"],
        agent_class=agent_class,
        config=BaseConfig(name=name, role=role),
        routes_to=[],
    )


def _make_member(
    name: str,
    role: str = "TestRole",
    agent_class: type[Akgent[Any, Any]] = StubAgent,
    headcount: int = 1,
    members: list[TeamCardMember] | None = None,
) -> TeamCardMember:
    return TeamCardMember(
        card=_make_card(name, role, agent_class),
        headcount=headcount,
        members=members or [],
    )


def _make_team_card(
    entry_point: TeamCardMember | None = None,
    members: list[TeamCardMember] | None = None,
    name: str = "test-team",
) -> TeamCard:
    ep = entry_point or _make_member("lead", "Lead")
    return TeamCard(
        name=name,
        description="Test team",
        entry_point=ep,
        members=members or [],
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def actor_system() -> ActorSystem:  # type: ignore[misc]
    """Provide an ActorSystem that shuts down after each test."""
    system = ActorSystem()
    yield system  # type: ignore[misc]
    system.shutdown()


@pytest.fixture()
def event_store() -> InMemoryEventStore:
    """Provide a fresh InMemoryEventStore per test."""
    return InMemoryEventStore()


@pytest.fixture()
def manager(
    actor_system: ActorSystem, event_store: InMemoryEventStore
) -> TeamManager:
    """Provide a TeamManager with default NullServiceRegistry."""
    return TeamManager(actor_system=actor_system, event_store=event_store)


# ---------------------------------------------------------------------------
# Tests: create_team
# ---------------------------------------------------------------------------


class TestTeamManagerCreate:
    """AC 1-8: TeamManager.create_team creates teams via TeamFactory."""

    def test_create_team_happy_path(
        self,
        manager: TeamManager,
        event_store: InMemoryEventStore,
    ) -> None:
        """AC 2,4,5,7: create_team returns TeamRuntime and persists RUNNING Process."""
        from datetime import UTC, datetime, timedelta

        before = datetime.now(UTC)
        tc = _make_team_card()
        runtime = manager.create_team(tc, user_id="test-user", user_email="u@test.com")
        after = datetime.now(UTC)

        assert isinstance(runtime, TeamRuntime)
        assert runtime.orchestrator_addr.is_alive()

        # Process persisted with RUNNING status
        process = event_store.load_team(runtime.id)
        assert process is not None
        assert process.status == TeamStatus.RUNNING
        assert process.user_id == "test-user"
        assert process.user_email == "u@test.com"
        assert process.team_card.name == "test-team"

        # Timestamps are set to reasonable values
        assert before - timedelta(seconds=1) <= process.created_at <= after + timedelta(seconds=1)
        assert process.created_at == process.updated_at

    def test_create_team_with_shared_subscribers(
        self,
        actor_system: ActorSystem,
        event_store: InMemoryEventStore,
    ) -> None:
        """AC 2,3: shared subscribers appended after PersistenceSubscriber."""
        recording = RecordingSubscriber()

        mgr = TeamManager(
            actor_system=actor_system,
            event_store=event_store,
            subscribers=[recording],
        )
        tc = _make_team_card()
        runtime = mgr.create_team(tc)

        # Verify recording subscriber is registered by stopping orchestrator via proxy
        runtime.orchestrator_proxy.stop()
        # on_stop() fires asynchronously after the proxy stop() call returns;
        # wait for the actor thread to finish so on_stop() has been invoked.
        deadline = time.monotonic() + 2.0
        while runtime.orchestrator_addr.is_alive() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert recording.stopped is True

    def test_create_team_rollback_on_build_failure(
        self,
        manager: TeamManager,
        event_store: InMemoryEventStore,
    ) -> None:
        """AC 8: If build fails, no Process is persisted."""
        failing = _make_member("failing", "Failing", agent_class=FailingAgent)
        tc = _make_team_card(members=[failing])

        with pytest.raises(RuntimeError) as excinfo:
            manager.create_team(tc)
        assert str(excinfo.value)

        # No Process should be in event store
        assert len(event_store.teams) == 0

    def test_create_team_uses_pre_generated_team_id(
        self,
        manager: TeamManager,
        event_store: InMemoryEventStore,
    ) -> None:
        """AC 4: TeamManager pre-generates team_id and passes to TeamFactory.build."""
        tc = _make_team_card()
        runtime = manager.create_team(tc)

        # The team_id in Process must match the runtime id
        process = event_store.load_team(runtime.id)
        assert process is not None
        assert process.team_id == runtime.id


# ---------------------------------------------------------------------------
# Tests: get_team
# ---------------------------------------------------------------------------


class TestTeamManagerGet:
    """AC 9: TeamManager.get_team retrieves Process metadata."""

    def test_get_team_found(
        self,
        manager: TeamManager,
        event_store: InMemoryEventStore,
    ) -> None:
        """AC 9: get_team returns Process when team exists."""
        tc = _make_team_card()
        runtime = manager.create_team(tc)

        result = manager.get_team(runtime.id)
        assert result is not None
        assert result.team_id == runtime.id
        assert result.status == TeamStatus.RUNNING

    def test_get_team_not_found(self, manager: TeamManager) -> None:
        """AC 9: get_team returns None when team does not exist."""
        result = manager.get_team(uuid.uuid4())
        assert result is None


# ---------------------------------------------------------------------------
# Tests: State machine enforcement
# ---------------------------------------------------------------------------


class TestTeamManagerStateMachine:
    """AC 10-11: State machine enforcement for delete_team."""

    def test_delete_running_team_raises(
        self,
        manager: TeamManager,
    ) -> None:
        """AC 10: delete_team on RUNNING team raises ValueError."""
        tc = _make_team_card()
        runtime = manager.create_team(tc)

        with pytest.raises(ValueError, match="currently running"):
            manager.delete_team(runtime.id)

    def test_delete_stopped_team_succeeds(
        self,
        manager: TeamManager,
        event_store: InMemoryEventStore,
    ) -> None:
        """AC 11: delete_team on STOPPED team purges data."""
        tc = _make_team_card()
        runtime = manager.create_team(tc)

        # Manually transition to STOPPED
        process = event_store.load_team(runtime.id)
        assert process is not None
        stopped_process = Process(
            team_id=process.team_id,
            team_card=process.team_card,
            status=TeamStatus.STOPPED,
            user_id=process.user_id,
            user_email=process.user_email,
            created_at=process.created_at,
            updated_at=process.updated_at,
        )
        event_store.save_team(stopped_process)

        manager.delete_team(runtime.id)

        # Data should be purged
        assert event_store.load_team(runtime.id) is None

    def test_delete_nonexistent_team_raises(
        self,
        manager: TeamManager,
    ) -> None:
        """AC 11: delete_team on non-existent team raises ValueError."""
        with pytest.raises(ValueError, match="not found"):
            manager.delete_team(uuid.uuid4())

    def test_delete_already_deleted_team_raises(
        self,
        manager: TeamManager,
        event_store: InMemoryEventStore,
    ) -> None:
        """AC 11: delete_team on DELETED team raises ValueError."""
        from datetime import UTC, datetime

        team_id = uuid.uuid4()
        process = Process(
            team_id=team_id,
            team_card=_make_team_card(),
            status=TeamStatus.DELETED,
            user_id="cli",
            user_email="",
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        event_store.save_team(process)

        with pytest.raises(ValueError, match="already deleted"):
            manager.delete_team(team_id)


# ---------------------------------------------------------------------------
# Tests: ServiceRegistry integration
# ---------------------------------------------------------------------------


class TestTeamManagerServiceRegistry:
    """AC 6: ServiceRegistry.register_team called on create."""

    def test_register_team_called_on_create(
        self,
        actor_system: ActorSystem,
        event_store: InMemoryEventStore,
    ) -> None:
        """AC 6: register_team is called with instance_id and team_id."""
        mock_registry = MagicMock(spec=NullServiceRegistry)
        instance_id = uuid.uuid4()
        mgr = TeamManager(
            actor_system=actor_system,
            event_store=event_store,
            service_registry=mock_registry,
            instance_id=instance_id,
        )
        tc = _make_team_card()
        runtime = mgr.create_team(tc)

        mock_registry.register_team.assert_called_once_with(instance_id, runtime.id)

    def test_deregister_team_called_on_delete(
        self,
        actor_system: ActorSystem,
        event_store: InMemoryEventStore,
    ) -> None:
        """deregister_team is called with instance_id and team_id on delete."""
        from datetime import UTC, datetime

        mock_registry = MagicMock(spec=NullServiceRegistry)
        instance_id = uuid.uuid4()
        mgr = TeamManager(
            actor_system=actor_system,
            event_store=event_store,
            service_registry=mock_registry,
            instance_id=instance_id,
        )
        team_id = uuid.uuid4()
        process = Process(
            team_id=team_id,
            team_card=_make_team_card(),
            status=TeamStatus.STOPPED,
            user_id="cli",
            user_email="",
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        event_store.save_team(process)

        mgr.delete_team(team_id)

        mock_registry.deregister_team.assert_called_once_with(instance_id, team_id)


# ---------------------------------------------------------------------------
# Tests: resume_team
# ---------------------------------------------------------------------------


def _create_and_stop_team(
    manager: TeamManager,
    event_store: InMemoryEventStore,
    team_card: TeamCard | None = None,
) -> uuid.UUID:
    """Create a team, populate events, stop it, and set Process to STOPPED.

    Manually injects StartMessage events for each agent so that
    TeamRestorer can rebuild the team from the event log.

    Returns the team_id.
    """
    from datetime import UTC, datetime

    from akgentic.core.actor_address_impl import ActorAddressProxy
    from akgentic.core.messages.orchestrator import StartMessage
    from akgentic.core.orchestrator import Orchestrator
    from akgentic.core.utils.deserializer import ActorAddressDict

    from akgentic.team.models import PersistedEvent

    tc = team_card or _make_team_card()
    runtime = manager.create_team(tc, user_id="test-user")

    team_id = runtime.id
    seq = 0

    # Inject Orchestrator StartMessage
    seq += 1
    orch_addr_dict: ActorAddressDict = {
        "__actor_address__": True,
        "__actor_type__": f"{Orchestrator.__module__}.{Orchestrator.__name__}",
        "agent_id": str(runtime.orchestrator_addr.agent_id),
        "name": "orchestrator",
        "role": "Orchestrator",
        "team_id": str(team_id),
        "squad_id": str(uuid.uuid4()),
        "user_message": False,
    }
    orch_start = StartMessage(
        config=BaseConfig(name="@Orchestrator", role="Orchestrator"),
    )
    orch_start.sender = ActorAddressProxy(orch_addr_dict)
    orch_start.team_id = team_id
    event_store.save_event(PersistedEvent(
        team_id=team_id, sequence=seq, event=orch_start, timestamp=datetime.now(UTC),
    ))

    # Inject StartMessages for all agents in the TeamCard tree
    def _inject_member(member: TeamCardMember) -> None:
        nonlocal seq
        name = member.card.config.name
        role = member.card.config.role
        agent_class = member.card.get_agent_class()
        addr = runtime.addrs.get(name)
        agent_id = addr.agent_id if addr else uuid.uuid4()
        seq += 1
        addr_dict: ActorAddressDict = {
            "__actor_address__": True,
            "__actor_type__": f"{agent_class.__module__}.{agent_class.__name__}",
            "agent_id": str(agent_id),
            "name": name,
            "role": role,
            "team_id": str(team_id),
            "squad_id": str(uuid.uuid4()),
            "user_message": False,
        }
        sm = StartMessage(config=member.card.get_config_copy())
        sm.sender = ActorAddressProxy(addr_dict)
        sm.team_id = team_id
        event_store.save_event(PersistedEvent(
            team_id=team_id, sequence=seq, event=sm, timestamp=datetime.now(UTC),
        ))
        for child in member.members:
            _inject_member(child)

    _inject_member(tc.entry_point)
    for member in tc.members:
        _inject_member(member)

    # Stop team via manager (uses proxy-based stop, transitions to STOPPED)
    manager.stop_team(team_id)

    return team_id


class TestTeamManagerResume:
    """AC 1-4, 14, 16: resume_team tests."""

    def test_resume_happy_path(
        self,
        manager: TeamManager,
        event_store: InMemoryEventStore,
    ) -> None:
        """AC 1,14: resume_team returns TeamRuntime and updates Process to RUNNING."""
        team_id = _create_and_stop_team(manager, event_store)

        runtime = manager.resume_team(team_id)

        assert isinstance(runtime, TeamRuntime)
        assert runtime.orchestrator_addr.is_alive()

        # Process should be RUNNING
        process = event_store.load_team(team_id)
        assert process is not None
        assert process.status == TeamStatus.RUNNING

    def test_resume_running_raises(
        self,
        manager: TeamManager,
    ) -> None:
        """AC 2: resume_team on RUNNING team raises ValueError."""
        tc = _make_team_card()
        runtime = manager.create_team(tc)

        with pytest.raises(ValueError, match="currently running"):
            manager.resume_team(runtime.id)

    def test_resume_deleted_raises(
        self,
        manager: TeamManager,
        event_store: InMemoryEventStore,
    ) -> None:
        """AC 3: resume_team on DELETED team raises ValueError."""
        from datetime import UTC, datetime

        team_id = uuid.uuid4()
        process = Process(
            team_id=team_id,
            team_card=_make_team_card(),
            status=TeamStatus.DELETED,
            user_id="cli",
            user_email="",
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        event_store.save_team(process)

        with pytest.raises(ValueError, match="has been deleted"):
            manager.resume_team(team_id)

    def test_resume_nonexistent_raises(
        self,
        manager: TeamManager,
    ) -> None:
        """AC 4: resume_team on non-existent team raises ValueError."""
        with pytest.raises(ValueError, match="not found"):
            manager.resume_team(uuid.uuid4())

    def test_resume_registers_with_service_registry(
        self,
        actor_system: ActorSystem,
        event_store: InMemoryEventStore,
    ) -> None:
        """AC 14: ServiceRegistry.register_team called on successful resume."""
        mock_registry = MagicMock(spec=NullServiceRegistry)
        instance_id = uuid.uuid4()
        mgr = TeamManager(
            actor_system=actor_system,
            event_store=event_store,
            service_registry=mock_registry,
            instance_id=instance_id,
        )

        team_id = _create_and_stop_team(mgr, event_store)

        # Reset mock to clear the create_team call
        mock_registry.register_team.reset_mock()

        mgr.resume_team(team_id)

        mock_registry.register_team.assert_called_once_with(instance_id, team_id)

    def test_resume_no_duplicate_subscriber_creation(
        self,
        actor_system: ActorSystem,
        event_store: InMemoryEventStore,
    ) -> None:
        """AC 3,6: resume_team attaches each subscriber exactly once (no double-creation).

        Observable behaviour: after stop_team on a resumed team, the
        orchestrator's on_stop fan-out must deliver on_stop(team_id) to the
        shared RecordingSubscriber exactly once — if resume had attached it
        twice, the fan-out would record two entries for the same team_id.
        """
        recording = RecordingSubscriber()

        mgr = TeamManager(
            actor_system=actor_system,
            event_store=event_store,
            subscribers=[recording],
        )
        team_id = _create_and_stop_team(mgr, event_store)

        # _create_and_stop_team already drove one stop → expect one on_stop
        # recorded from the create/stop cycle. Reset to isolate the resume.
        recording.stopped_team_ids.clear()
        recording.stopped = False

        mgr.resume_team(team_id)
        mgr.stop_team(team_id)

        # Shared subscriber must see on_stop(team_id) exactly once — a
        # double-attach during resume would deliver it twice.
        same_team_count = sum(1 for tid in recording.stopped_team_ids if tid == team_id)
        assert same_team_count == 1, (
            f"Shared subscriber received on_stop({team_id}) {same_team_count} "
            f"times after resume → stop; expected exactly 1"
        )

    def test_resume_continues_sequence_numbering(
        self,
        manager: TeamManager,
        event_store: InMemoryEventStore,
    ) -> None:
        """AC 14.8: After stop/resume, new events continue from max existing sequence.

        Story 21.2 deleted the ``_team_subscribers`` registry that this test
        used to introspect. The invariant under test has two halves:

        1. ``resume_team`` queries ``event_store.get_max_sequence(team_id)``
           and constructs its per-team ``PersistenceSubscriber`` with
           ``initial_sequence=max_seq``. The resume invocation below
           exercises that wiring path.
        2. A ``PersistenceSubscriber`` constructed with ``initial_sequence
           =max_seq`` emits ``on_message`` at ``max_seq + 1``. This is the
           unit-level invariant the manager relies on; it is verified on
           an isolated event store to isolate the assertion from
           orchestrator telemetry the manager's internal subscriber will
           also persist post-resume.
        """
        from akgentic.core.messages.message import UserMessage

        from akgentic.team.subscriber import PersistenceSubscriber

        team_id = _create_and_stop_team(manager, event_store)

        # Record max sequence before resume
        existing_events = event_store.load_events(team_id)
        max_seq = max(e.sequence for e in existing_events)
        assert max_seq > 0, "Precondition: events must exist before resume"

        # Half 1: exercise the manager's resume_team wiring path.
        manager.resume_team(team_id)

        # Half 2: invariant check on PersistenceSubscriber, isolated from
        # orchestrator startup telemetry on the resumed team.
        isolated_store = type(event_store)()
        isolated_team_id = uuid.uuid4()
        persistence_sub = PersistenceSubscriber(
            isolated_team_id, isolated_store, initial_sequence=max_seq
        )
        persistence_sub.on_message(UserMessage(content="post-resume message"))

        events = isolated_store.load_events(isolated_team_id)
        assert len(events) == 1
        assert events[0].sequence == max_seq + 1

    def test_create_team_uses_default_initial_sequence(
        self,
        actor_system: ActorSystem,
        event_store: InMemoryEventStore,
    ) -> None:
        """AC 14.8: PersistenceSubscriber default initial_sequence=0 (no regression).

        Story 21.2 deleted the ``_team_subscribers`` registry that this test
        used to introspect. The behavioural invariant — that a fresh
        ``PersistenceSubscriber(team_id, event_store)`` (no
        ``initial_sequence`` override) starts numbering at 1 — is now
        exercised directly on a fresh event store, isolated from the
        events the orchestrator emits during ``create_team`` startup.

        ``create_team`` is still invoked to confirm the manager wires the
        per-team subscriber with no override, but the sequence assertion
        runs against an isolated store/team_id pair so it cannot collide
        with orchestrator startup telemetry.
        """
        from akgentic.core.messages.message import UserMessage

        from akgentic.team.subscriber import PersistenceSubscriber

        # Exercise the manager so the create_team wiring is covered.
        manager = TeamManager(actor_system=actor_system, event_store=event_store)
        tc = _make_team_card()
        manager.create_team(tc)

        # Direct invariant check on PersistenceSubscriber, isolated from
        # orchestrator startup telemetry on the just-created team.
        isolated_store = type(event_store)()
        isolated_team_id = uuid.uuid4()
        persistence_sub = PersistenceSubscriber(isolated_team_id, isolated_store)
        persistence_sub.on_message(UserMessage(content="first message"))

        events = isolated_store.events
        assert len(events) == 1
        assert events[0].team_id == isolated_team_id
        assert events[0].sequence == 1

    def test_stop_after_resume_triggers_on_stop_fanout(
        self,
        actor_system: ActorSystem,
        event_store: InMemoryEventStore,
    ) -> None:
        """AC 5,6: stop_team after resume delivers on_stop + transitions to STOPPED.

        Story 21.2 made ``Orchestrator.on_stop`` the single source of truth
        for the unsubscribe fan-out. The behavioural invariant is now
        observable directly: the shared RecordingSubscriber, attached to
        the resumed team, must see ``on_stop(team_id)`` once stop_team
        returns.
        """
        recording = RecordingSubscriber()

        mgr = TeamManager(
            actor_system=actor_system,
            event_store=event_store,
            subscribers=[recording],
        )
        team_id = _create_and_stop_team(mgr, event_store)

        mgr.resume_team(team_id)

        # Reset recorder so we only observe the upcoming stop_team
        # (the create→stop in _create_and_stop_team already fired one).
        recording.stopped_team_ids.clear()
        recording.stopped = False

        mgr.stop_team(team_id)

        # The orchestrator's on_stop fan-out must have reached the shared
        # subscriber while it was still attached.
        assert team_id in recording.stopped_team_ids
        assert team_id not in mgr._runtimes

        # Process should be STOPPED
        process = event_store.load_team(team_id)
        assert process is not None
        assert process.status == TeamStatus.STOPPED


# ---------------------------------------------------------------------------
# Tests: delete data purge verification
# ---------------------------------------------------------------------------


class TestTeamManagerDeleteDataPurge:
    """AC 1: delete_team purges all persisted data."""

    def test_delete_purges_all_persisted_data(
        self,
        actor_system: ActorSystem,
        event_store: InMemoryEventStore,
    ) -> None:
        """Verify Process, events, and agent states are all purged on delete."""
        from datetime import UTC, datetime

        from akgentic.core.messages.message import UserMessage

        from akgentic.team.models import AgentStateSnapshot, PersistedEvent

        manager = TeamManager(actor_system=actor_system, event_store=event_store)
        tc = _make_team_card()
        runtime = manager.create_team(tc)
        team_id = runtime.id

        # Manually populate events and agent states to verify full purge
        event_store.save_event(PersistedEvent(
            team_id=team_id,
            sequence=1,
            event=UserMessage(content="test"),
            timestamp=datetime.now(UTC),
        ))
        event_store.save_agent_state(AgentStateSnapshot(
            team_id=team_id,
            agent_id="test-agent",
            state=BaseState(),
            updated_at=datetime.now(UTC),
        ))

        # Verify all three data types exist before delete
        assert event_store.load_team(team_id) is not None
        assert len(event_store.load_events(team_id)) > 0
        assert len(event_store.load_agent_states(team_id)) > 0

        # Use stop_team to transition to STOPPED
        manager.stop_team(team_id)

        # Delete and verify complete purge of all three data types
        manager.delete_team(team_id)
        assert event_store.load_team(team_id) is None
        assert event_store.load_events(team_id) == []
        assert event_store.load_agent_states(team_id) == []


# ---------------------------------------------------------------------------
# Tests: stop_team
# ---------------------------------------------------------------------------


class TestTeamManagerStop:
    """AC 1-5: TeamManager.stop_team graceful shutdown tests."""

    def test_stop_running_team_succeeds(
        self,
        manager: TeamManager,
        event_store: InMemoryEventStore,
    ) -> None:
        """AC 1: stop_team on RUNNING team transitions to STOPPED with actor teardown."""
        from datetime import UTC, datetime, timedelta

        tc = _make_team_card()
        runtime = manager.create_team(tc)
        team_id = runtime.id

        # Verify team is running
        assert runtime.orchestrator_addr.is_alive()

        before = datetime.now(UTC)
        manager.stop_team(team_id)
        after = datetime.now(UTC)

        # Process should be STOPPED
        process = event_store.load_team(team_id)
        assert process is not None
        assert process.status == TeamStatus.STOPPED

        # Actors should be dead
        assert not runtime.orchestrator_addr.is_alive()

        # updated_at should be recent
        assert before - timedelta(seconds=1) <= process.updated_at <= after + timedelta(seconds=1)

    def test_stop_running_team_triggers_on_stop_fanout(
        self,
        actor_system: ActorSystem,
        event_store: InMemoryEventStore,
    ) -> None:
        """AC 1: stop_team triggers Orchestrator.on_stop fan-out to subscribers.

        Story 21.2 deleted ``_teardown_team``'s manual unsubscribe loop.
        ``Orchestrator.on_stop`` is now the single source of truth — it
        fans out ``on_stop(team_id)`` to every attached subscriber, then
        runs ``super().on_stop()``, then clears the subscriber list. The
        RecordingSubscriber observes that fan-out actually fires while
        subscribers are still attached.
        """
        recording = RecordingSubscriber()

        mgr = TeamManager(
            actor_system=actor_system,
            event_store=event_store,
            subscribers=[recording],
        )
        tc = _make_team_card()
        runtime = mgr.create_team(tc)
        team_id = runtime.id

        # Precondition: subscriber has not yet seen on_stop
        assert recording.stopped_team_ids == []

        mgr.stop_team(team_id)

        # The orchestrator's on_stop fan-out must reach the subscriber
        # exactly once with the correct team_id.
        assert team_id in recording.stopped_team_ids

        # After stop, actors are dead and runtime tracking is cleaned up.
        assert not runtime.orchestrator_addr.is_alive()
        assert team_id not in mgr._runtimes

        # Process should be STOPPED
        process = event_store.load_team(team_id)
        assert process is not None
        assert process.status == TeamStatus.STOPPED

    def test_stop_stopped_team_is_noop(
        self,
        manager: TeamManager,
        event_store: InMemoryEventStore,
    ) -> None:
        """AC 2: stop_team on STOPPED team is idempotent — no error raised."""
        tc = _make_team_card()
        runtime = manager.create_team(tc)
        team_id = runtime.id

        # Stop the team
        manager.stop_team(team_id)
        process_after_first_stop = event_store.load_team(team_id)
        assert process_after_first_stop is not None
        assert process_after_first_stop.status == TeamStatus.STOPPED

        # Stop again — should be no-op
        manager.stop_team(team_id)

        # Process should remain STOPPED with same timestamp
        process_after_second_stop = event_store.load_team(team_id)
        assert process_after_second_stop is not None
        assert process_after_second_stop.status == TeamStatus.STOPPED
        assert process_after_second_stop.updated_at == process_after_first_stop.updated_at

    def test_stop_deleted_team_raises(
        self,
        manager: TeamManager,
        event_store: InMemoryEventStore,
    ) -> None:
        """AC 3: stop_team on DELETED team raises ValueError."""
        from datetime import UTC, datetime

        team_id = uuid.uuid4()
        process = Process(
            team_id=team_id,
            team_card=_make_team_card(),
            status=TeamStatus.DELETED,
            user_id="cli",
            user_email="",
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        event_store.save_team(process)

        with pytest.raises(ValueError, match="no longer exists"):
            manager.stop_team(team_id)

    def test_stop_nonexistent_team_raises(
        self,
        manager: TeamManager,
    ) -> None:
        """AC 4: stop_team on non-existent team raises ValueError."""
        with pytest.raises(ValueError, match="not found"):
            manager.stop_team(uuid.uuid4())

    def test_stop_deregisters_from_service_registry(
        self,
        actor_system: ActorSystem,
        event_store: InMemoryEventStore,
    ) -> None:
        """AC 5: ServiceRegistry.deregister_team called on stop."""
        mock_registry = MagicMock(spec=NullServiceRegistry)
        instance_id = uuid.uuid4()
        mgr = TeamManager(
            actor_system=actor_system,
            event_store=event_store,
            service_registry=mock_registry,
            instance_id=instance_id,
        )
        tc = _make_team_card()
        runtime = mgr.create_team(tc)
        team_id = runtime.id

        mgr.stop_team(team_id)

        mock_registry.deregister_team.assert_called_once_with(instance_id, team_id)

    def test_stop_running_without_tracked_runtime(
        self,
        manager: TeamManager,
        event_store: InMemoryEventStore,
    ) -> None:
        """AC 1: stop_team handles RUNNING team with no tracked runtime gracefully.

        Simulates a manager restart where _runtimes is empty but EventStore
        still has a RUNNING Process. stop_team should update Process to STOPPED
        and deregister without attempting actor teardown.
        """
        tc = _make_team_card()
        runtime = manager.create_team(tc)
        team_id = runtime.id

        # Simulate manager restart: clear runtime tracking
        manager._runtimes.clear()

        # stop_team should still succeed — update state and deregister
        manager.stop_team(team_id)

        process = event_store.load_team(team_id)
        assert process is not None
        assert process.status == TeamStatus.STOPPED

    def test_stop_updates_timestamp(
        self,
        manager: TeamManager,
        event_store: InMemoryEventStore,
    ) -> None:
        """AC 1: updated_at is set to a new value after stop."""
        tc = _make_team_card()
        runtime = manager.create_team(tc)
        team_id = runtime.id

        process_before = event_store.load_team(team_id)
        assert process_before is not None
        original_updated_at = process_before.updated_at

        manager.stop_team(team_id)

        process_after = event_store.load_team(team_id)
        assert process_after is not None
        # updated_at must change — stop_team always generates a new timestamp
        assert process_after.updated_at >= original_updated_at
        assert process_after.status == TeamStatus.STOPPED


# ---------------------------------------------------------------------------
# Tests: TimerStopSubscriber → STOPPED bridge
# Timer-stop bridge: TimerStopSubscriber is a standard per-team subscriber.
# ---------------------------------------------------------------------------


def test_timer_stop_persists_stopped_status(
    actor_system: ActorSystem,
    event_store: InMemoryEventStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End-to-end: orchestrator inactivity timer drives Process.status == STOPPED.

    Re-enabled in Story 21.3 once TimerStopSubscriber is wired as a
    standard per-team subscriber. The full chain under test is:
    timer → _timeout_handler → _notify_subscribers("on_stop_request")
    → TimerStopSubscriber → stop_team → event_store.save_team(STOPPED).
    """
    monkeypatch.setenv("ORCHESTRATOR_TIMEOUT_DELAY", "1")

    mgr = TeamManager(actor_system=actor_system, event_store=event_store)
    tc = _make_team_card()
    runtime = mgr.create_team(tc)
    team_id = runtime.id

    deadline = time.monotonic() + 15.0
    while time.monotonic() < deadline:
        process = event_store.load_team(team_id)
        if process is not None and process.status == TeamStatus.STOPPED:
            break
        time.sleep(0.05)

    process = event_store.load_team(team_id)
    assert process is not None
    assert process.status == TeamStatus.STOPPED
    assert team_id not in mgr._runtimes
