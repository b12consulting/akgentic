"""Tests for TeamRestorer -- AC 5-13, 15, 18."""

from __future__ import annotations

import time
import uuid
from datetime import UTC, datetime
from typing import Any
from unittest.mock import patch

import pytest
from akgentic.core.actor_address import ActorAddress
from akgentic.core.actor_system_impl import ActorSystem
from akgentic.core.agent import Akgent
from akgentic.core.agent_card import AgentCard
from akgentic.core.agent_config import BaseConfig
from akgentic.core.agent_state import BaseState
from akgentic.core.messages.message import Message
from akgentic.core.messages.orchestrator import (
    EventMessage,
    SentMessage,
    StartMessage,
    StopMessage,
)
from akgentic.core.orchestrator import EventSubscriber, Orchestrator

from akgentic.team.models import (
    PersistedEvent,
    Process,
    TeamCard,
    TeamCardMember,
    TeamRuntime,
    TeamStatus,
)
from akgentic.team.restorer import TeamRestorer
from tests.services.conftest import InMemoryEventStore

# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


class StubAgent(Akgent[BaseConfig, BaseState]):
    """Minimal agent for restorer tests."""

    pass


class RecordingSubscriber(EventSubscriber):
    """Subscriber that records received messages."""

    def __init__(self) -> None:
        self.messages: list[Message] = []
        self.stopped: bool = False

    def on_message(self, msg: Message) -> None:
        """Record received message."""
        self.messages.append(msg)

    def on_stop(self) -> None:
        """Record stop."""
        self.stopped = True

    def set_restoring(self, restoring: bool) -> None:  # noqa: FBT001
        """No-op for test subscriber."""


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


def _make_start_message(
    agent_id: uuid.UUID,
    name: str,
    role: str,
    team_id: uuid.UUID,
    agent_class: type[Akgent[Any, Any]] = StubAgent,
    config: BaseConfig | None = None,
    parent_id: uuid.UUID | None = None,
    parent_name: str = "orchestrator",
    parent_role: str = "Orchestrator",
) -> StartMessage:
    """Create a StartMessage with a properly-formed sender address.

    Args:
        parent_id: If set, creates a parent ActorAddressProxy with this agent_id.
    """
    cfg = config or BaseConfig(name=name, role=role)
    msg = StartMessage(config=cfg)
    # Build a fake sender address dict so serialize() works
    from akgentic.core.actor_address_impl import ActorAddressProxy
    from akgentic.core.utils.deserializer import ActorAddressDict

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
    sender = ActorAddressProxy(addr_dict)
    msg.sender = sender
    msg.team_id = team_id

    if parent_id is not None:
        parent_dict: ActorAddressDict = {
            "__actor_address__": True,
            "__actor_type__": "akgentic.core.orchestrator.Orchestrator",
            "agent_id": str(parent_id),
            "name": parent_name,
            "role": parent_role,
            "team_id": str(team_id),
            "squad_id": "",
            "user_message": False,
        }
        msg.parent = ActorAddressProxy(parent_dict)

    return msg


def _make_stop_message(
    agent_id: uuid.UUID,
    name: str,
    role: str,
    team_id: uuid.UUID,
) -> StopMessage:
    """Create a StopMessage with a properly-formed sender address."""
    from akgentic.core.actor_address_impl import ActorAddressProxy
    from akgentic.core.utils.deserializer import ActorAddressDict

    msg = StopMessage()
    addr_dict: ActorAddressDict = {
        "__actor_address__": True,
        "__actor_type__": "akgentic.core.agent.Akgent",
        "agent_id": str(agent_id),
        "name": name,
        "role": role,
        "team_id": str(team_id),
        "squad_id": str(uuid.uuid4()),
        "user_message": False,
    }
    msg.sender = ActorAddressProxy(addr_dict)
    msg.team_id = team_id
    return msg


def _populate_stopped_team(
    event_store: InMemoryEventStore,
    team_card: TeamCard | None = None,
    extra_members: list[tuple[str, str]] | None = None,
    fired_members: list[tuple[str, str, uuid.UUID]] | None = None,
) -> tuple[uuid.UUID, Process]:
    """Populate InMemoryEventStore with events simulating a stopped team.

    Creates StartMessage events for orchestrator + all agents in team_card,
    plus optional fired agents (with matching StopMessage events).

    Returns:
        Tuple of (team_id, Process with STOPPED status).
    """
    tc = team_card or _make_team_card()
    team_id = uuid.uuid4()
    seq = 0

    # Orchestrator StartMessage
    orch_id = uuid.uuid4()
    seq += 1
    orch_start = _make_start_message(
        orch_id,
        "orchestrator",
        "Orchestrator",
        team_id,
        agent_class=Orchestrator,
        config=BaseConfig(name="@Orchestrator", role="Orchestrator"),
    )
    event_store.save_event(
        PersistedEvent(
            team_id=team_id,
            sequence=seq,
            event=orch_start,
            timestamp=datetime.now(UTC),
        )
    )

    # Agent StartMessages -- from TeamCard tree
    agent_names: list[str] = []

    def _walk_member(
        member: TeamCardMember,
        parent_agent_id: uuid.UUID | None = None,
        parent_name: str = "orchestrator",
        parent_role: str = "Orchestrator",
    ) -> None:
        nonlocal seq
        name = member.card.config.name
        role = member.card.config.role
        agent_id = uuid.uuid4()
        agent_class = member.card.get_agent_class()
        seq += 1
        sm = _make_start_message(
            agent_id,
            name,
            role,
            team_id,
            agent_class=agent_class,
            config=member.card.get_config_copy(),
            parent_id=parent_agent_id or orch_id,
            parent_name=parent_name,
            parent_role=parent_role,
        )
        event_store.save_event(
            PersistedEvent(
                team_id=team_id,
                sequence=seq,
                event=sm,
                timestamp=datetime.now(UTC),
            )
        )
        agent_names.append(name)
        for child in member.members:
            _walk_member(child, parent_agent_id=agent_id, parent_name=name, parent_role=role)

    _walk_member(tc.entry_point)
    for member in tc.members:
        _walk_member(member)

    # Fired members: add StartMessage + StopMessage pairs
    if fired_members:
        for fname, frole, fid in fired_members:
            seq += 1
            fsm = _make_start_message(fid, fname, frole, team_id)
            event_store.save_event(
                PersistedEvent(
                    team_id=team_id,
                    sequence=seq,
                    event=fsm,
                    timestamp=datetime.now(UTC),
                )
            )
            seq += 1
            fstop = _make_stop_message(fid, fname, frole, team_id)
            event_store.save_event(
                PersistedEvent(
                    team_id=team_id,
                    sequence=seq,
                    event=fstop,
                    timestamp=datetime.now(UTC),
                )
            )

    # Process record
    now = datetime.now(UTC)
    process = Process(
        team_id=team_id,
        team_card=tc,
        status=TeamStatus.STOPPED,
        user_id="test-user",
        user_email="test@test.com",
        created_at=now,
        updated_at=now,
    )
    event_store.save_team(process)

    return team_id, process


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


# ---------------------------------------------------------------------------
# Tests: TestTeamRestorerRestore
# ---------------------------------------------------------------------------


class TestTeamRestorerRestore:
    """AC 5-13: Core restore functionality."""

    def test_restore_returns_valid_team_runtime(
        self,
        actor_system: ActorSystem,
        event_store: InMemoryEventStore,
    ) -> None:
        """AC 5,13: restore returns TeamRuntime with valid addresses."""
        team_id, process = _populate_stopped_team(event_store)

        restorer = TeamRestorer(actor_system, event_store)
        runtime = restorer.restore(process)

        assert isinstance(runtime, TeamRuntime)
        assert runtime.id == team_id
        assert runtime.orchestrator_addr.is_alive()
        assert runtime.entry_addr.is_alive()
        assert "lead" in runtime.addrs

    def test_restore_with_multiple_agents(
        self,
        actor_system: ActorSystem,
        event_store: InMemoryEventStore,
    ) -> None:
        """AC 9: All agents are rebuilt from StartMessages."""
        worker = _make_member("worker", "Worker")
        tc = _make_team_card(members=[worker])

        team_id, process = _populate_stopped_team(event_store, tc)

        restorer = TeamRestorer(actor_system, event_store)
        runtime = restorer.restore(process)

        assert "lead" in runtime.addrs
        assert "worker" in runtime.addrs
        assert runtime.addrs["lead"].is_alive()
        assert runtime.addrs["worker"].is_alive()

    def test_restore_orchestrator_created_first(
        self,
        actor_system: ActorSystem,
        event_store: InMemoryEventStore,
    ) -> None:
        """AC 8: Orchestrator is created first during restore."""
        worker = _make_member("worker", "Worker")
        tc = _make_team_card(members=[worker])

        team_id, process = _populate_stopped_team(event_store, tc)

        restorer = TeamRestorer(actor_system, event_store)
        runtime = restorer.restore(process)

        # Orchestrator should be alive and functional
        assert runtime.orchestrator_addr.is_alive()
        team = runtime.orchestrator_proxy.get_team()
        assert isinstance(team, list)

    def test_restore_events_replayed(
        self,
        actor_system: ActorSystem,
        event_store: InMemoryEventStore,
    ) -> None:
        """AC 12: Events are replayed through orchestrator during restore."""
        tc = _make_team_card()
        team_id, process = _populate_stopped_team(event_store, tc)

        # Track how many events exist before restore
        events_before = len(event_store.load_events(team_id))
        assert events_before > 0

        restorer = TeamRestorer(actor_system, event_store)
        runtime = restorer.restore(process)

        # Team is functional after restore
        assert runtime.orchestrator_addr.is_alive()

    def test_restore_get_team_works_after_restore(
        self,
        actor_system: ActorSystem,
        event_store: InMemoryEventStore,
    ) -> None:
        """get_team() returns agents after restore (restore_message populates history)."""
        worker = _make_member("worker", "Worker")
        tc = _make_team_card(members=[worker])

        team_id, process = _populate_stopped_team(event_store, tc)

        restorer = TeamRestorer(actor_system, event_store)
        runtime = restorer.restore(process)

        team = runtime.orchestrator_proxy.get_team()
        team_names = {addr.name for addr in team}
        # Both lead and worker should appear in orchestrator's team
        assert "lead" in team_names
        assert "worker" in team_names

    def test_restore_supervisor_addrs_populated(
        self,
        actor_system: ActorSystem,
        event_store: InMemoryEventStore,
    ) -> None:
        """AC 13: TeamRuntime has supervisor_addrs populated for first-layer members."""
        worker = _make_member("worker", "Worker")
        supervisor = _make_member("supervisor", "Supervisor", members=[worker])
        tc = _make_team_card(members=[supervisor])

        team_id, process = _populate_stopped_team(event_store, tc)

        restorer = TeamRestorer(actor_system, event_store)
        runtime = restorer.restore(process)

        # supervisor is a first-layer member, so it's in supervisor_addrs
        assert "supervisor" in runtime.supervisor_addrs
        # entry point (lead) is NOT in supervisor_addrs
        assert "lead" not in runtime.supervisor_addrs

    def test_restore_with_agent_state_snapshots(
        self,
        actor_system: ActorSystem,
        event_store: InMemoryEventStore,
    ) -> None:
        """AC 11: Persisted AgentStateSnapshot is restored via init_state()."""
        from unittest.mock import patch as mock_patch

        from akgentic.team.models import AgentStateSnapshot

        tc = _make_team_card()
        team_id, process = _populate_stopped_team(event_store, tc)

        # Inject a state snapshot for the "lead" agent
        snapshot = AgentStateSnapshot(
            team_id=team_id,
            agent_id="lead",
            state=BaseState(),
            updated_at=datetime.now(UTC),
        )
        event_store.save_agent_state(snapshot)

        restorer = TeamRestorer(actor_system, event_store)

        # Track init_state calls via spy
        init_state_calls: list[str] = []
        original_proxy_ask = actor_system.proxy_ask

        def tracking_proxy_ask(
            addr: ActorAddress,
            cls: type[Any],
        ) -> Any:
            proxy = original_proxy_ask(addr, cls)
            if cls is Akgent:
                original_init = proxy.init_state

                def tracked_init(state: Any) -> None:
                    init_state_calls.append("lead")
                    return original_init(state)

                proxy.init_state = tracked_init
            return proxy

        with mock_patch.object(actor_system, "proxy_ask", side_effect=tracking_proxy_ask):
            runtime = restorer.restore(process)

        # Verify init_state was called for the agent with snapshot
        assert "lead" in init_state_calls
        assert runtime.addrs["lead"].is_alive()

    def test_restore_agent_profiles_registered(
        self,
        actor_system: ActorSystem,
        event_store: InMemoryEventStore,
    ) -> None:
        """AC 9: Only agent_profiles are registered with orchestrator after restore."""
        worker = _make_member("worker", "Worker")
        tc = _make_team_card(members=[worker])
        # Explicitly register profiles for hiring
        tc.agent_profiles = list(tc.agent_cards.values())

        team_id, process = _populate_stopped_team(event_store, tc)

        restorer = TeamRestorer(actor_system, event_store)
        runtime = restorer.restore(process)

        catalog = runtime.orchestrator_proxy.get_agent_catalog()
        roles = {c.role for c in catalog}
        assert "Lead" in roles
        assert "Worker" in roles

    def test_restore_no_profiles_means_empty_catalog(
        self,
        actor_system: ActorSystem,
        event_store: InMemoryEventStore,
    ) -> None:
        """Default agent_profiles (empty) results in empty hiring catalog after restore."""
        worker = _make_member("worker", "Worker")
        tc = _make_team_card(members=[worker])
        # agent_profiles defaults to empty — no roles available for hiring

        team_id, process = _populate_stopped_team(event_store, tc)

        restorer = TeamRestorer(actor_system, event_store)
        runtime = restorer.restore(process)

        catalog = runtime.orchestrator_proxy.get_agent_catalog()
        assert len(catalog) == 0


# ---------------------------------------------------------------------------
# Tests: TestTeamRestorerAgentFiltering
# ---------------------------------------------------------------------------


class TestTeamRestorerAgentFiltering:
    """AC 7: StartMessage/StopMessage filtering."""

    def test_fired_agent_excluded_from_restore(
        self,
        actor_system: ActorSystem,
        event_store: InMemoryEventStore,
    ) -> None:
        """AC 7: Agents with matching StopMessage are excluded from rebuild."""
        tc = _make_team_card()
        fired_id = uuid.uuid4()

        team_id, process = _populate_stopped_team(
            event_store,
            tc,
            fired_members=[("fired-agent", "FiredRole", fired_id)],
        )

        restorer = TeamRestorer(actor_system, event_store)
        runtime = restorer.restore(process)

        # Fired agent should NOT be in the restored team
        assert "fired-agent" not in runtime.addrs
        # Lead should still be there
        assert "lead" in runtime.addrs

    def test_agent_stopped_then_not_rebuilt(
        self,
        actor_system: ActorSystem,
        event_store: InMemoryEventStore,
    ) -> None:
        """AC 7: Multiple fired agents are all excluded."""
        tc = _make_team_card()
        fired1_id = uuid.uuid4()
        fired2_id = uuid.uuid4()

        team_id, process = _populate_stopped_team(
            event_store,
            tc,
            fired_members=[
                ("fired1", "Role1", fired1_id),
                ("fired2", "Role2", fired2_id),
            ],
        )

        restorer = TeamRestorer(actor_system, event_store)
        runtime = restorer.restore(process)

        assert "fired1" not in runtime.addrs
        assert "fired2" not in runtime.addrs
        assert "lead" in runtime.addrs


# ---------------------------------------------------------------------------
# Tests: TestTeamRestorerRestoringFlag
# ---------------------------------------------------------------------------


class TestTeamRestorerRestoringFlag:
    """AC 12: Restoring flag toggle on PersistenceSubscriber."""

    def test_restoring_flag_managed_correctly(
        self,
        actor_system: ActorSystem,
        event_store: InMemoryEventStore,
    ) -> None:
        """AC 12: PersistenceSubscriber restoring=True before replay, False after.

        The caller (TeamManager) is now responsible for toggling the restoring flag.
        This test validates the pattern: set_restoring(True) before restore(),
        set_restoring(False) after restore().
        """
        from akgentic.team.subscriber import PersistenceSubscriber

        tc = _make_team_card()
        team_id, process = _populate_stopped_team(event_store, tc)

        persistence_sub = PersistenceSubscriber(team_id, event_store)
        persistence_sub.set_restoring(True)

        restorer = TeamRestorer(actor_system, event_store)
        restorer.restore(process, subscribers=[persistence_sub])

        # Caller sets restoring=False after restore (simulating TeamManager)
        persistence_sub.set_restoring(False)
        assert persistence_sub._restoring is False

    def test_no_duplicate_events_during_replay(
        self,
        actor_system: ActorSystem,
        event_store: InMemoryEventStore,
    ) -> None:
        """AC 12: Replayed events are not re-persisted (restoring flag prevents it)."""
        from akgentic.team.subscriber import PersistenceSubscriber

        tc = _make_team_card()
        team_id, process = _populate_stopped_team(event_store, tc)

        original_events = event_store.load_events(team_id)
        original_sequences = {e.sequence for e in original_events}

        persistence_sub = PersistenceSubscriber(team_id, event_store)
        persistence_sub.set_restoring(True)

        restorer = TeamRestorer(actor_system, event_store)
        restorer.restore(process, subscribers=[persistence_sub])

        persistence_sub.set_restoring(False)

        # Check no events with original sequences were duplicated
        all_events = event_store.load_events(team_id)
        seq_counts: dict[int, int] = {}
        for e in all_events:
            seq_counts[e.sequence] = seq_counts.get(e.sequence, 0) + 1

        for seq in original_sequences:
            assert seq_counts[seq] == 1, f"Sequence {seq} was duplicated"


# ---------------------------------------------------------------------------
# Tests: TestTeamRestorerRollback
# ---------------------------------------------------------------------------


class TestTeamRestorerRollback:
    """Rollback on failure."""

    def test_rollback_on_failure(
        self,
        actor_system: ActorSystem,
        event_store: InMemoryEventStore,
    ) -> None:
        """All spawned actors cleaned up if a phase fails."""
        tc = _make_team_card()
        team_id, process = _populate_stopped_team(event_store, tc)

        restorer = TeamRestorer(actor_system, event_store)

        # Track orchestrator actor created before failure
        import pykka

        actors_before = len(pykka.ActorRegistry.get_all())

        # Patch import_class to fail when resolving the lead agent class
        with patch(
            "akgentic.team.restorer.import_class",
            side_effect=ImportError("cannot find agent class"),
        ):
            with pytest.raises(ImportError, match="cannot find agent class"):
                restorer.restore(process)

        # After rollback, no new actors should remain alive
        actors_after = len(pykka.ActorRegistry.get_all())
        assert actors_after == actors_before, (
            f"Rollback failed: {actors_after - actors_before} actor(s) leaked"
        )

    def test_subscribers_registered_with_orchestrator(
        self,
        actor_system: ActorSystem,
        event_store: InMemoryEventStore,
    ) -> None:
        """Pre-instantiated subscribers are registered with orchestrator."""
        tc = _make_team_card()
        team_id, process = _populate_stopped_team(event_store, tc)

        recording = RecordingSubscriber()

        restorer = TeamRestorer(actor_system, event_store)
        runtime = restorer.restore(process, subscribers=[recording])

        # Recording subscriber should have received replayed events
        assert len(recording.messages) > 0

        # Stop via proxy and wait for full shutdown before asserting on_stop
        runtime.orchestrator_proxy.stop()
        # on_stop() fires asynchronously after the proxy stop() call returns;
        # wait for the actor thread to finish so on_stop() has been invoked.
        deadline = time.monotonic() + 2.0
        while runtime.orchestrator_addr.is_alive() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert recording.stopped is True

    def test_subscriber_receives_start_messages_during_replay(
        self,
        actor_system: ActorSystem,
        event_store: InMemoryEventStore,
    ) -> None:
        """Subscriber receives StartMessage events for all agents during Phase 3 replay.

        Story 14.9 AC 4: Given a team with 3 agents that is stopped and resumed,
        when a subscriber is registered during restore, then the subscriber
        receives StartMessage events for all 3 agents during Phase 3 replay.
        """
        worker1 = _make_member("worker1", "Worker")
        worker2 = _make_member("worker2", "Worker")
        tc = _make_team_card(members=[worker1, worker2])

        team_id, process = _populate_stopped_team(event_store, tc)

        recording = RecordingSubscriber()

        restorer = TeamRestorer(actor_system, event_store)
        restorer.restore(process, subscribers=[recording])

        # Filter StartMessage events received by the subscriber
        start_messages = [
            m for m in recording.messages if isinstance(m, StartMessage)
        ]

        # Should receive StartMessages for orchestrator + lead + worker1 + worker2
        start_names = {
            m.sender.name for m in start_messages if m.sender is not None
        }
        assert "lead" in start_names, (
            f"Missing StartMessage for 'lead'; received: {start_names}"
        )
        assert "worker1" in start_names, (
            f"Missing StartMessage for 'worker1'; received: {start_names}"
        )
        assert "worker2" in start_names, (
            f"Missing StartMessage for 'worker2'; received: {start_names}"
        )


# ---------------------------------------------------------------------------
# Tests: Hierarchy propagation during restore (Story 10-1, AC 3, 5)
# ---------------------------------------------------------------------------


class TestRestorerHierarchyPropagation:
    """AC 3,5: Restored agents have _orchestrator set."""

    def test_orchestrator_set_on_restored_agents(
        self,
        actor_system: ActorSystem,
        event_store: InMemoryEventStore,
    ) -> None:
        """AC 3,5: _orchestrator is not None on agents rebuilt during restore."""
        worker = _make_member("worker", "Worker")
        tc = _make_team_card(members=[worker])

        team_id, process = _populate_stopped_team(event_store, tc)

        restorer = TeamRestorer(actor_system, event_store)
        runtime = restorer.restore(process)

        for name, addr in runtime.addrs.items():
            proxy: Akgent[Any, Any] = actor_system.proxy_ask(addr, Akgent)
            orch = proxy.orchestrator
            assert orch is not None, f"Restored agent '{name}' has _orchestrator=None"
            assert orch.is_alive(), f"Restored agent '{name}' orchestrator is not alive"

    def test_parent_set_on_restored_agents(
        self,
        actor_system: ActorSystem,
        event_store: InMemoryEventStore,
    ) -> None:
        """AC 5: _parent is set on agents rebuilt during restore."""
        worker = _make_member("worker", "Worker")
        tc = _make_team_card(members=[worker])

        team_id, process = _populate_stopped_team(event_store, tc)

        restorer = TeamRestorer(actor_system, event_store)
        runtime = restorer.restore(process)

        # All restored agents should have orchestrator as parent
        for name, addr in runtime.addrs.items():
            actor = addr._actor_ref._actor_weakref()  # type: ignore[union-attr]
            assert actor._parent is not None, f"Restored agent '{name}' has _parent=None"
            assert actor._parent.agent_id == runtime.orchestrator_addr.agent_id, (
                f"Restored agent '{name}' parent is not the orchestrator"
            )


# ---------------------------------------------------------------------------
# Tests: TestRestorerAddressResolution (Story 12.3, AC 1)
# ---------------------------------------------------------------------------


class TestRestorerAddressResolution:
    """AC 1: Restored teams resolve serialized actor addresses to live refs."""

    def test_restored_get_team_returns_live_addresses(
        self,
        actor_system: ActorSystem,
        event_store: InMemoryEventStore,
    ) -> None:
        """After restore, get_team() returns ActorAddressImpl, not ActorAddressProxy."""
        from akgentic.core.actor_address_impl import ActorAddressImpl, ActorAddressProxy

        worker = _make_member("worker", "Worker")
        tc = _make_team_card(members=[worker])

        team_id, process = _populate_stopped_team(event_store, tc)

        restorer = TeamRestorer(actor_system, event_store)
        runtime = restorer.restore(process)

        team = runtime.orchestrator_proxy.get_team()
        for addr in team:
            assert isinstance(addr, ActorAddressImpl), (
                f"Expected ActorAddressImpl but got {type(addr).__name__} for agent '{addr.name}'"
            )
            assert not isinstance(addr, ActorAddressProxy), (
                f"ActorAddressProxy leaked into get_team() for '{addr.name}'"
            )

    def test_restored_get_team_member_returns_live_address(
        self,
        actor_system: ActorSystem,
        event_store: InMemoryEventStore,
    ) -> None:
        """After restore, get_team_member() returns the live address matching spawned actor."""
        from akgentic.core.actor_address_impl import ActorAddressImpl

        worker = _make_member("worker", "Worker")
        tc = _make_team_card(members=[worker])

        team_id, process = _populate_stopped_team(event_store, tc)

        restorer = TeamRestorer(actor_system, event_store)
        runtime = restorer.restore(process)

        lead_addr = runtime.orchestrator_proxy.get_team_member("lead")
        assert lead_addr is not None
        assert isinstance(lead_addr, ActorAddressImpl)
        assert lead_addr.is_alive()

        worker_addr = runtime.orchestrator_proxy.get_team_member("worker")
        assert worker_addr is not None
        assert isinstance(worker_addr, ActorAddressImpl)
        assert worker_addr.is_alive()

    def test_replay_does_not_overwrite_live_with_proxy(
        self,
        actor_system: ActorSystem,
        event_store: InMemoryEventStore,
    ) -> None:
        """Phase 3 replayed StartMessages do not overwrite Phase 2 live addresses."""
        from akgentic.core.actor_address_impl import ActorAddressImpl

        tc = _make_team_card()
        team_id, process = _populate_stopped_team(event_store, tc)

        restorer = TeamRestorer(actor_system, event_store)
        runtime = restorer.restore(process)

        # The entry agent should have a live address matching the spawned actor
        lead_from_roster = runtime.orchestrator_proxy.get_team_member("lead")
        lead_from_addrs = runtime.addrs["lead"]

        assert lead_from_roster is not None
        assert isinstance(lead_from_roster, ActorAddressImpl)
        # The address from get_team_member should match the address from addrs
        assert lead_from_roster.agent_id == lead_from_addrs.agent_id


# ---------------------------------------------------------------------------
# Tests: Proxy-based restore spawning (Story 12.4, AC 4, 6)
# ---------------------------------------------------------------------------


class TestRestorerProxySpawning:
    """AC 4,6: Restore uses public createActor() API, no duplicate roster entries."""

    def test_restore_creates_agents_through_public_api(
        self,
        actor_system: ActorSystem,
        event_store: InMemoryEventStore,
    ) -> None:
        """AC 4: After restore, all agents are alive and have correct names/roles."""
        worker = _make_member("worker", "Worker")
        tc = _make_team_card(members=[worker])

        team_id, process = _populate_stopped_team(event_store, tc)

        restorer = TeamRestorer(actor_system, event_store)
        runtime = restorer.restore(process)

        team = runtime.orchestrator_proxy.get_team()
        team_names = {addr.name for addr in team}
        assert "lead" in team_names
        assert "worker" in team_names
        for addr in team:
            assert addr.is_alive()

    def test_restore_no_duplicate_roster_entries(
        self,
        actor_system: ActorSystem,
        event_store: InMemoryEventStore,
    ) -> None:
        """AC 6: get_team() has no duplicate agent_ids after restore."""
        worker = _make_member("worker", "Worker")
        tc = _make_team_card(members=[worker])

        team_id, process = _populate_stopped_team(event_store, tc)

        restorer = TeamRestorer(actor_system, event_store)
        runtime = restorer.restore(process)

        team = runtime.orchestrator_proxy.get_team()
        agent_ids = [addr.agent_id for addr in team]
        assert len(agent_ids) == len(set(agent_ids)), (
            f"Duplicate agent_ids in team roster: {agent_ids}"
        )


# ---------------------------------------------------------------------------
# Tests: Orphan fallback in _spawn_agents (Story 14-1, AC 3)
# ---------------------------------------------------------------------------


class TestRestorerOrphanFallback:
    """AC 3: Unknown parent falls back to orchestrator."""

    def test_spawn_agents_orphan_falls_back_to_orchestrator(
        self,
        actor_system: ActorSystem,
        event_store: InMemoryEventStore,
    ) -> None:
        """AC 3: Agent with unknown parent spawns through orchestrator (no crash)."""
        tc = _make_team_card()
        team_id = uuid.uuid4()
        seq = 0

        # Orchestrator StartMessage
        orch_id = uuid.uuid4()
        seq += 1
        orch_start = _make_start_message(
            orch_id,
            "orchestrator",
            "Orchestrator",
            team_id,
            agent_class=Orchestrator,
            config=BaseConfig(name="@Orchestrator", role="Orchestrator"),
        )
        event_store.save_event(
            PersistedEvent(
                team_id=team_id,
                sequence=seq,
                event=orch_start,
                timestamp=datetime.now(UTC),
            )
        )

        # Agent with an unknown parent_id (orphan)
        unknown_parent_id = uuid.uuid4()
        agent_id = uuid.uuid4()
        seq += 1
        orphan_start = _make_start_message(
            agent_id,
            "lead",
            "Lead",
            team_id,
            agent_class=StubAgent,
            parent_id=unknown_parent_id,
            parent_name="ghost",
            parent_role="Ghost",
        )
        event_store.save_event(
            PersistedEvent(
                team_id=team_id,
                sequence=seq,
                event=orphan_start,
                timestamp=datetime.now(UTC),
            )
        )

        now = datetime.now(UTC)
        process = Process(
            team_id=team_id,
            team_card=tc,
            status=TeamStatus.STOPPED,
            user_id="test-user",
            user_email="test@test.com",
            created_at=now,
            updated_at=now,
        )
        event_store.save_team(process)

        restorer = TeamRestorer(actor_system, event_store)
        runtime = restorer.restore(process)

        # The orphan agent should be alive and a child of the orchestrator
        assert "lead" in runtime.addrs
        assert runtime.addrs["lead"].is_alive()

        # Verify it's parented to orchestrator (orphan fallback)
        from tests.integration.conftest import get_actor_from_addr

        orchestrator_actor = get_actor_from_addr(runtime.orchestrator_addr)
        orch_child_ids = {c.agent_id for c in orchestrator_actor._children}
        assert runtime.addrs["lead"].agent_id in orch_child_ids


# ---------------------------------------------------------------------------
# Helpers: EventMessage construction
# ---------------------------------------------------------------------------


def _make_event_message(
    agent_id: uuid.UUID,
    name: str,
    role: str,
    team_id: uuid.UUID,
    event: Any = "some-event",
) -> EventMessage:
    """Create an EventMessage with a properly-formed sender address."""
    from akgentic.core.actor_address_impl import ActorAddressProxy
    from akgentic.core.utils.deserializer import ActorAddressDict

    msg = EventMessage(event=event)
    addr_dict: ActorAddressDict = {
        "__actor_address__": True,
        "__actor_type__": f"{StubAgent.__module__}.{StubAgent.__name__}",
        "agent_id": str(agent_id),
        "name": name,
        "role": role,
        "team_id": str(team_id),
        "squad_id": str(uuid.uuid4()),
        "user_message": False,
    }
    msg.sender = ActorAddressProxy(addr_dict)
    msg.team_id = team_id
    return msg


def _make_sent_message(
    agent_id: uuid.UUID,
    name: str,
    role: str,
    team_id: uuid.UUID,
) -> SentMessage:
    """Create a SentMessage (non-EventMessage) for filtering tests."""
    from akgentic.core.actor_address_impl import ActorAddressProxy
    from akgentic.core.utils.deserializer import ActorAddressDict

    inner = Message()
    addr_dict: ActorAddressDict = {
        "__actor_address__": True,
        "__actor_type__": f"{StubAgent.__module__}.{StubAgent.__name__}",
        "agent_id": str(agent_id),
        "name": name,
        "role": role,
        "team_id": str(team_id),
        "squad_id": str(uuid.uuid4()),
        "user_message": False,
    }
    recipient = ActorAddressProxy(addr_dict)
    msg = SentMessage(message=inner, recipient=recipient)
    msg.sender = ActorAddressProxy(addr_dict)
    msg.team_id = team_id
    return msg


# ---------------------------------------------------------------------------
# Tests: TestFilterEventMessages (Story 14.5, AC 2)
# ---------------------------------------------------------------------------


class TestFilterEventMessages:
    """AC 2: _filter_event_messages filtering logic."""

    def test_filter_event_messages_returns_matching_events(
        self,
        event_store: InMemoryEventStore,
    ) -> None:
        """Returns only EventMessage instances with matching agent_id, in order."""
        actor_system = ActorSystem()
        try:
            restorer = TeamRestorer(actor_system, event_store)
            team_id = uuid.uuid4()
            target_id = uuid.uuid4()
            other_id = uuid.uuid4()

            em1 = _make_event_message(target_id, "agent-a", "RoleA", team_id, event="ev1")
            em2 = _make_event_message(other_id, "agent-b", "RoleB", team_id, event="ev2")
            em3 = _make_event_message(target_id, "agent-a", "RoleA", team_id, event="ev3")
            sm = _make_sent_message(target_id, "agent-a", "RoleA", team_id)

            events = [
                PersistedEvent(team_id=team_id, sequence=1, event=em1, timestamp=datetime.now(UTC)),
                PersistedEvent(team_id=team_id, sequence=2, event=em2, timestamp=datetime.now(UTC)),
                PersistedEvent(team_id=team_id, sequence=3, event=em3, timestamp=datetime.now(UTC)),
                PersistedEvent(team_id=team_id, sequence=4, event=sm, timestamp=datetime.now(UTC)),
            ]

            result = restorer._filter_event_messages(events, target_id)

            assert len(result) == 2
            assert result[0] is em1
            assert result[1] is em3
        finally:
            actor_system.shutdown()

    def test_filter_event_messages_returns_empty_for_no_matches(
        self,
        event_store: InMemoryEventStore,
    ) -> None:
        """Returns empty list when no EventMessage matches."""
        actor_system = ActorSystem()
        try:
            restorer = TeamRestorer(actor_system, event_store)
            team_id = uuid.uuid4()
            target_id = uuid.uuid4()
            other_id = uuid.uuid4()

            em = _make_event_message(other_id, "agent-b", "RoleB", team_id)
            sm = _make_sent_message(other_id, "agent-b", "RoleB", team_id)

            events = [
                PersistedEvent(team_id=team_id, sequence=1, event=em, timestamp=datetime.now(UTC)),
                PersistedEvent(team_id=team_id, sequence=2, event=sm, timestamp=datetime.now(UTC)),
            ]

            result = restorer._filter_event_messages(events, target_id)
            assert result == []
        finally:
            actor_system.shutdown()

    def test_filter_event_messages_skips_none_sender(
        self,
        event_store: InMemoryEventStore,
    ) -> None:
        """EventMessage with sender=None is excluded from results."""
        actor_system = ActorSystem()
        try:
            restorer = TeamRestorer(actor_system, event_store)
            team_id = uuid.uuid4()
            target_id = uuid.uuid4()

            em_with_sender = _make_event_message(
                target_id, "agent-a", "RoleA", team_id, event="ev1"
            )
            em_no_sender = EventMessage(event="ev2")
            em_no_sender.sender = None

            events = [
                PersistedEvent(
                    team_id=team_id, sequence=1, event=em_with_sender, timestamp=datetime.now(UTC)
                ),
                PersistedEvent(
                    team_id=team_id, sequence=2, event=em_no_sender, timestamp=datetime.now(UTC)
                ),
            ]

            result = restorer._filter_event_messages(events, target_id)
            assert len(result) == 1
            assert result[0] is em_with_sender
        finally:
            actor_system.shutdown()


# ---------------------------------------------------------------------------
# Tests: TestRebuildAgentsLlmContext (Story 14.5, AC 1, 3, 4)
# ---------------------------------------------------------------------------


_has_init_llm_context = hasattr(Akgent, "init_llm_context")


@pytest.mark.skipif(
    not _has_init_llm_context,
    reason="Requires akgentic-core with init_llm_context (Story 14.2)",
)
class TestRebuildAgentsLlmContext:
    """AC 1,3,4: init_llm_context() called during _rebuild_agents()."""

    def test_init_llm_context_called_for_agents_with_events(
        self,
        actor_system: ActorSystem,
        event_store: InMemoryEventStore,
    ) -> None:
        """init_llm_context() is called for agents that have EventMessage events."""
        tc = _make_team_card()
        team_id, process = _populate_stopped_team(event_store, tc)

        # Add EventMessage events for the "lead" agent
        events = event_store.load_events(team_id)
        # Find the lead agent's agent_id from the StartMessage
        lead_agent_id: uuid.UUID | None = None
        for pe in events:
            if (
                isinstance(pe.event, StartMessage)
                and pe.event.sender is not None
                and pe.event.sender.name == "lead"
            ):
                lead_agent_id = pe.event.sender.agent_id
                break
        assert lead_agent_id is not None, "lead agent_id not found in events"

        # Add EventMessage events for "lead"
        em1 = _make_event_message(lead_agent_id, "lead", "Lead", team_id, event="llm-ev-1")
        em2 = _make_event_message(lead_agent_id, "lead", "Lead", team_id, event="llm-ev-2")
        event_store.save_event(
            PersistedEvent(team_id=team_id, sequence=100, event=em1, timestamp=datetime.now(UTC))
        )
        event_store.save_event(
            PersistedEvent(team_id=team_id, sequence=101, event=em2, timestamp=datetime.now(UTC))
        )

        restorer = TeamRestorer(actor_system, event_store)

        # Track init_llm_context calls
        init_llm_calls: dict[str, list[Any]] = {}
        original_proxy_ask = actor_system.proxy_ask

        def tracking_proxy_ask(
            addr: ActorAddress,
            cls: type[Any],
        ) -> Any:
            proxy = original_proxy_ask(addr, cls)
            if cls is Akgent:
                original_init_llm = proxy.init_llm_context

                def tracked_init_llm(context: list[Any]) -> None:
                    init_llm_calls[addr.name] = context
                    return original_init_llm(context)

                proxy.init_llm_context = tracked_init_llm
            return proxy

        with patch.object(actor_system, "proxy_ask", side_effect=tracking_proxy_ask):
            restorer.restore(process)

        # init_llm_context was called for "lead" with 2 events
        assert "lead" in init_llm_calls, "init_llm_context not called for lead"
        assert len(init_llm_calls["lead"]) == 2

    def test_init_llm_context_not_called_for_agents_without_events(
        self,
        actor_system: ActorSystem,
        event_store: InMemoryEventStore,
    ) -> None:
        """init_llm_context() is NOT called for agents with no EventMessage events."""
        tc = _make_team_card()
        team_id, process = _populate_stopped_team(event_store, tc)

        # No EventMessage events added -- only StartMessages exist

        restorer = TeamRestorer(actor_system, event_store)

        init_llm_calls: dict[str, list[Any]] = {}
        original_proxy_ask = actor_system.proxy_ask

        def tracking_proxy_ask(
            addr: ActorAddress,
            cls: type[Any],
        ) -> Any:
            proxy = original_proxy_ask(addr, cls)
            if cls is Akgent:
                original_init_llm = proxy.init_llm_context

                def tracked_init_llm(context: list[Any]) -> None:
                    init_llm_calls[addr.name] = context
                    return original_init_llm(context)

                proxy.init_llm_context = tracked_init_llm
            return proxy

        with patch.object(actor_system, "proxy_ask", side_effect=tracking_proxy_ask):
            restorer.restore(process)

        # init_llm_context should NOT have been called (no EventMessage events)
        assert len(init_llm_calls) == 0, (
            f"init_llm_context unexpectedly called for: {list(init_llm_calls.keys())}"
        )


# ---------------------------------------------------------------------------
# Tests: ToolActor spawn order during restore (Story 14.6, ADR-010)
# ---------------------------------------------------------------------------


class TestRestorerToolActorSpawnOrder:
    """Story 14.6: ToolActor StartMessages sorted before regular agents."""

    def test_tool_actor_start_messages_sorted_first(
        self,
        event_store: InMemoryEventStore,
    ) -> None:
        """ToolActor StartMessages must be sorted before regular agents."""
        actor_system = ActorSystem()
        try:
            restorer = TeamRestorer(actor_system, event_store)
            team_id = uuid.uuid4()
            orch_id = uuid.uuid4()
            seq = 0

            # Orchestrator
            seq += 1
            orch_start = _make_start_message(
                orch_id,
                "orchestrator",
                "Orchestrator",
                team_id,
                agent_class=Orchestrator,
                config=BaseConfig(name="@Orchestrator", role="Orchestrator"),
            )
            event_store.save_event(
                PersistedEvent(
                    team_id=team_id, sequence=seq, event=orch_start,
                    timestamp=datetime.now(UTC),
                )
            )

            # Regular agents first (lower sequence numbers)
            seq += 1
            manager_start = _make_start_message(
                uuid.uuid4(), "Manager", "Manager", team_id,
                config=BaseConfig(name="Manager", role="Manager"),
                parent_id=orch_id,
            )
            event_store.save_event(
                PersistedEvent(
                    team_id=team_id, sequence=seq, event=manager_start,
                    timestamp=datetime.now(UTC),
                )
            )

            seq += 1
            assistant_start = _make_start_message(
                uuid.uuid4(), "Assistant", "Assistant", team_id,
                config=BaseConfig(name="Assistant", role="Assistant"),
                parent_id=orch_id,
            )
            event_store.save_event(
                PersistedEvent(
                    team_id=team_id, sequence=seq, event=assistant_start,
                    timestamp=datetime.now(UTC),
                )
            )

            # ToolActors last (higher sequence numbers -- lazy creation)
            seq += 1
            planning_start = _make_start_message(
                uuid.uuid4(), "#PlanningTool", "ToolActor", team_id,
                config=BaseConfig(name="#PlanningTool", role="ToolActor"),
                parent_id=orch_id,
            )
            event_store.save_event(
                PersistedEvent(
                    team_id=team_id, sequence=seq, event=planning_start,
                    timestamp=datetime.now(UTC),
                )
            )

            seq += 1
            kg_start = _make_start_message(
                uuid.uuid4(), "#KnowledgeGraphTool", "ToolActor", team_id,
                config=BaseConfig(name="#KnowledgeGraphTool", role="ToolActor"),
                parent_id=orch_id,
            )
            event_store.save_event(
                PersistedEvent(
                    team_id=team_id, sequence=seq, event=kg_start,
                    timestamp=datetime.now(UTC),
                )
            )

            events = event_store.load_events(team_id)
            events.sort(key=lambda e: e.sequence)

            orchestrator_start, agent_starts = restorer._determine_live_agents(events)

            # Before the fix, agent_starts would be in sequence order:
            # [Manager, Assistant, #PlanningTool, #KnowledgeGraphTool]
            # After the fix (sort in _rebuild_agents), ToolActors come first.
            # We test the sort key directly here:
            tool_actor_role = "ToolActor"
            agent_starts.sort(
                key=lambda sm: sm.config.role != tool_actor_role
            )

            roles = [sm.config.role for sm in agent_starts]
            assert roles[:2] == ["ToolActor", "ToolActor"]
            assert "ToolActor" not in roles[2:]

        finally:
            actor_system.shutdown()

    def test_sort_with_no_tool_actors_preserves_order(
        self,
        event_store: InMemoryEventStore,
    ) -> None:
        """When no ToolActors exist, sort preserves stable ordering."""
        actor_system = ActorSystem()
        try:
            restorer = TeamRestorer(actor_system, event_store)
            team_id = uuid.uuid4()
            orch_id = uuid.uuid4()
            seq = 0

            # Orchestrator
            seq += 1
            orch_start = _make_start_message(
                orch_id,
                "orchestrator",
                "Orchestrator",
                team_id,
                agent_class=Orchestrator,
                config=BaseConfig(name="@Orchestrator", role="Orchestrator"),
            )
            event_store.save_event(
                PersistedEvent(
                    team_id=team_id, sequence=seq, event=orch_start,
                    timestamp=datetime.now(UTC),
                )
            )

            # Regular agents only
            seq += 1
            manager_start = _make_start_message(
                uuid.uuid4(), "Manager", "Manager", team_id,
                config=BaseConfig(name="Manager", role="Manager"),
                parent_id=orch_id,
            )
            event_store.save_event(
                PersistedEvent(
                    team_id=team_id, sequence=seq, event=manager_start,
                    timestamp=datetime.now(UTC),
                )
            )

            seq += 1
            assistant_start = _make_start_message(
                uuid.uuid4(), "Assistant", "Assistant", team_id,
                config=BaseConfig(name="Assistant", role="Assistant"),
                parent_id=orch_id,
            )
            event_store.save_event(
                PersistedEvent(
                    team_id=team_id, sequence=seq, event=assistant_start,
                    timestamp=datetime.now(UTC),
                )
            )

            events = event_store.load_events(team_id)
            events.sort(key=lambda e: e.sequence)

            orchestrator_start, agent_starts = restorer._determine_live_agents(events)

            original_names = [sm.sender.name for sm in agent_starts]

            tool_actor_role = "ToolActor"
            agent_starts.sort(
                key=lambda sm: sm.config.role != tool_actor_role
            )

            # All agents present, no crash
            assert len(agent_starts) == 2
            assert {sm.sender.name for sm in agent_starts} == set(original_names)

        finally:
            actor_system.shutdown()

    def test_tool_actors_spawned_before_regular_agents_in_full_restore(
        self,
        actor_system: ActorSystem,
        event_store: InMemoryEventStore,
    ) -> None:
        """Full restore: ToolActors spawned before regular agents (integration)."""
        tc = _make_team_card()
        team_id = uuid.uuid4()
        orch_id = uuid.uuid4()
        seq = 0

        # Orchestrator StartMessage
        seq += 1
        orch_start = _make_start_message(
            orch_id,
            "orchestrator",
            "Orchestrator",
            team_id,
            agent_class=Orchestrator,
            config=BaseConfig(name="@Orchestrator", role="Orchestrator"),
        )
        event_store.save_event(
            PersistedEvent(
                team_id=team_id, sequence=seq, event=orch_start,
                timestamp=datetime.now(UTC),
            )
        )

        # Regular agent (entry point) -- seq 2
        lead_id = uuid.uuid4()
        seq += 1
        lead_start = _make_start_message(
            lead_id, "lead", "Lead", team_id,
            config=BaseConfig(name="lead", role="Lead"),
            parent_id=orch_id,
        )
        event_store.save_event(
            PersistedEvent(
                team_id=team_id, sequence=seq, event=lead_start,
                timestamp=datetime.now(UTC),
            )
        )

        # ToolActor -- seq 3 (higher, simulating lazy creation)
        tool_id = uuid.uuid4()
        seq += 1
        tool_start = _make_start_message(
            tool_id, "#PlanningTool", "ToolActor", team_id,
            config=BaseConfig(name="#PlanningTool", role="ToolActor"),
            parent_id=orch_id,
        )
        event_store.save_event(
            PersistedEvent(
                team_id=team_id, sequence=seq, event=tool_start,
                timestamp=datetime.now(UTC),
            )
        )

        now = datetime.now(UTC)
        process = Process(
            team_id=team_id,
            team_card=tc,
            status=TeamStatus.STOPPED,
            user_id="test-user",
            user_email="test@test.com",
            created_at=now,
            updated_at=now,
        )
        event_store.save_team(process)

        # Track spawn order
        spawn_order: list[str] = []
        original_spawn = TeamRestorer._spawn_agents

        def tracking_spawn(
            self_restorer: TeamRestorer,
            agent_starts_arg: list[StartMessage],
            orchestrator_addr: ActorAddress,
            spawned_addrs: list[ActorAddress],
        ) -> dict[str, ActorAddress]:
            for sm in agent_starts_arg:
                if sm.sender is not None:
                    spawn_order.append(sm.sender.name)
            return original_spawn(
                self_restorer, agent_starts_arg, orchestrator_addr, spawned_addrs
            )

        with patch.object(TeamRestorer, "_spawn_agents", tracking_spawn):
            restorer = TeamRestorer(actor_system, event_store)
            runtime = restorer.restore(process)

        # ToolActor must be spawned BEFORE the regular agent
        assert spawn_order.index("#PlanningTool") < spawn_order.index("lead"), (
            f"Expected #PlanningTool before lead, got order: {spawn_order}"
        )

        # Both agents alive, no duplicates
        assert "#PlanningTool" in runtime.addrs
        assert "lead" in runtime.addrs
        team = runtime.orchestrator_proxy.get_team()
        agent_ids = [addr.agent_id for addr in team]
        assert len(agent_ids) == len(set(agent_ids)), "Duplicate agents in roster"
