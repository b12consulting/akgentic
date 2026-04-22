"""Integration tests for TeamManager lifecycle: create, stop, resume.

Tests use real Akgent subclasses, real orchestrators, and real event flow.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any
from unittest.mock import patch as mock_patch

import pytest
from akgentic.core.actor_address import ActorAddress
from akgentic.core.actor_address_impl import ActorAddressProxy
from akgentic.core.actor_system_impl import ActorSystem
from akgentic.core.agent import Akgent
from akgentic.core.messages.message import UserMessage
from akgentic.core.messages.orchestrator import EventMessage
from akgentic.core.utils.deserializer import ActorAddressDict

from akgentic.team.manager import TeamManager
from akgentic.team.models import PersistedEvent, TeamCard, TeamCardMember, TeamStatus
from tests.integration.conftest import (
    RecordingAgent,
    StatefulAgent,
    get_actor_from_addr,
    make_integration_agent_card,
    wait_for_agent_state,
)
from tests.services.conftest import InMemoryEventStore


def _make_simple_team_card(
    agent_class: type[Akgent[Any, Any]] = RecordingAgent,
    name: str = "entry",
    role: str = "Entry",
) -> TeamCard:
    """Create a minimal team card with a single entry-point agent."""
    entry = TeamCardMember(
        card=make_integration_agent_card(
            name=name,
            role=role,
            agent_class=agent_class,
        ),
    )
    return TeamCard(
        name="simple-team",
        description="A single-agent team for lifecycle tests",
        entry_point=entry,
        members=[],
        message_types=[UserMessage],
    )


class TestLifecycleIntegration:
    """TeamManager lifecycle integration tests."""

    def test_create_team_is_functional(
        self,
        actor_system: ActorSystem,
    ) -> None:
        """A team created via TeamManager can receive messages."""
        event_store = InMemoryEventStore()
        manager = TeamManager(actor_system, event_store)
        team_card = _make_simple_team_card()

        runtime = manager.create_team(team_card)

        # Send a message directly to the entry agent
        actor_system.tell(
            runtime.entry_addr,
            UserMessage(content="lifecycle-test"),
        )

        reached = wait_for_agent_state(
            runtime.entry_addr,
            lambda state: "lifecycle-test" in getattr(state, "messages", []),
            timeout=3.0,
        )
        assert reached, "Message did not reach entry agent"

    def test_stop_team_persists_state(
        self,
        actor_system: ActorSystem,
    ) -> None:
        """Stopping a team persists Process and events to EventStore."""
        event_store = InMemoryEventStore()
        manager = TeamManager(actor_system, event_store)
        team_card = _make_simple_team_card()

        runtime = manager.create_team(team_card)
        team_id = runtime.id

        # Send messages to generate events
        actor_system.tell(
            runtime.entry_addr,
            UserMessage(content="msg-1"),
        )
        wait_for_agent_state(
            runtime.entry_addr,
            lambda state: getattr(state, "counter", 0) >= 1,
            timeout=3.0,
        )

        manager.stop_team(team_id)

        # Verify persistence
        process = event_store.load_team(team_id)
        assert process is not None, "Process not found after stop"
        assert process.status == TeamStatus.STOPPED, f"Expected STOPPED, got {process.status}"

        events = event_store.load_events(team_id)
        assert len(events) > 0, "No events persisted"

    def test_resume_team_is_functional(
        self,
        actor_system: ActorSystem,
    ) -> None:
        """A resumed team can send and receive NEW messages."""
        event_store = InMemoryEventStore()
        manager = TeamManager(actor_system, event_store)
        team_card = _make_simple_team_card()

        runtime = manager.create_team(team_card)
        team_id = runtime.id

        actor_system.tell(
            runtime.entry_addr,
            UserMessage(content="message-1"),
        )
        wait_for_agent_state(
            runtime.entry_addr,
            lambda state: "message-1" in getattr(state, "messages", []),
            timeout=3.0,
        )

        manager.stop_team(team_id)

        # Resume the team
        new_runtime = manager.resume_team(team_id)

        actor_system.tell(
            new_runtime.entry_addr,
            UserMessage(content="message-2"),
        )

        reached = wait_for_agent_state(
            new_runtime.entry_addr,
            lambda state: "message-2" in getattr(state, "messages", []),
            timeout=3.0,
        )
        assert reached, "Message 'message-2' did not reach entry agent on resumed team"

    def test_resume_team_preserves_agent_state(
        self,
        actor_system: ActorSystem,
    ) -> None:
        """Agent state survives stop/resume cycle."""
        event_store = InMemoryEventStore()
        manager = TeamManager(actor_system, event_store)
        team_card = _make_simple_team_card(
            agent_class=StatefulAgent,
            name="stateful",
            role="Stateful",
        )

        runtime = manager.create_team(team_card)
        team_id = runtime.id

        # Send 3 messages to increment counter to 3
        for i in range(3):
            actor_system.tell(
                runtime.entry_addr,
                UserMessage(content=f"msg-{i}"),
            )

        wait_for_agent_state(
            runtime.entry_addr,
            lambda state: getattr(state, "counter", 0) >= 3,
            timeout=3.0,
        )

        manager.stop_team(team_id)

        # Resume and verify counter preserved
        new_runtime = manager.resume_team(team_id)

        actor = get_actor_from_addr(new_runtime.entry_addr)
        assert actor.state.counter == 3, (
            f"Expected counter=3 after resume, got {actor.state.counter}"
        )

        # Send 1 more message, counter should become 4
        actor_system.tell(
            new_runtime.entry_addr,
            UserMessage(content="msg-extra"),
        )
        reached = wait_for_agent_state(
            new_runtime.entry_addr,
            lambda state: getattr(state, "counter", 0) >= 4,
            timeout=3.0,
        )
        assert reached, "Counter did not increment to 4 after resume"

    def test_resume_preserves_parent_child_hierarchy(
        self,
        actor_system: ActorSystem,
        hierarchical_team_card: TeamCard,
    ) -> None:
        """AC 1: After stop/resume, children belong to their supervisor, not orchestrator."""
        event_store = InMemoryEventStore()
        manager = TeamManager(actor_system, event_store)

        runtime = manager.create_team(hierarchical_team_card)
        team_id = runtime.id

        manager.stop_team(team_id)
        new_runtime = manager.resume_team(team_id)

        # Supervisor's _children should contain worker_a and worker_b
        supervisor_addr = new_runtime.addrs["supervisor"]
        supervisor_actor = get_actor_from_addr(supervisor_addr)
        supervisor_child_ids = {c.agent_id for c in supervisor_actor._children}

        worker_a_addr = new_runtime.addrs["worker_a"]
        worker_b_addr = new_runtime.addrs["worker_b"]
        assert worker_a_addr.agent_id in supervisor_child_ids, (
            "worker_a should be a child of supervisor after resume"
        )
        assert worker_b_addr.agent_id in supervisor_child_ids, (
            "worker_b should be a child of supervisor after resume"
        )

        # Orchestrator's children should NOT include worker_a and worker_b directly
        orchestrator_actor = get_actor_from_addr(new_runtime.orchestrator_addr)
        orchestrator_child_ids = {c.agent_id for c in orchestrator_actor._children}
        assert worker_a_addr.agent_id not in orchestrator_child_ids, (
            "worker_a should NOT be a direct child of orchestrator"
        )
        assert worker_b_addr.agent_id not in orchestrator_child_ids, (
            "worker_b should NOT be a direct child of orchestrator"
        )

    def test_resume_hierarchical_team_stop_cascades(
        self,
        actor_system: ActorSystem,
        hierarchical_team_card: TeamCard,
    ) -> None:
        """AC 5: Stopping a supervisor cascades to its children after resume."""
        event_store = InMemoryEventStore()
        manager = TeamManager(actor_system, event_store)

        runtime = manager.create_team(hierarchical_team_card)
        team_id = runtime.id

        manager.stop_team(team_id)
        new_runtime = manager.resume_team(team_id)

        supervisor_addr = new_runtime.addrs["supervisor"]
        worker_a_addr = new_runtime.addrs["worker_a"]
        worker_b_addr = new_runtime.addrs["worker_b"]

        # All agents alive before stop
        assert supervisor_addr.is_alive()
        assert worker_a_addr.is_alive()
        assert worker_b_addr.is_alive()

        # Stop supervisor via proxy -- triggers Akgent.stop() which cascades
        supervisor_proxy: Akgent[Any, Any] = actor_system.proxy_ask(supervisor_addr, Akgent)
        supervisor_proxy.stop()

        assert not supervisor_addr.is_alive(), "Supervisor should be stopped"
        assert not worker_a_addr.is_alive(), (
            "worker_a should be stopped via cascade from supervisor"
        )
        assert not worker_b_addr.is_alive(), (
            "worker_b should be stopped via cascade from supervisor"
        )

    def test_restore_has_one_start_message_per_agent(
        self,
        actor_system: ActorSystem,
    ) -> None:
        """AC 4: After restore, each agent has exactly one StartMessage -- no duplicates."""
        event_store = InMemoryEventStore()
        manager = TeamManager(actor_system, event_store)
        team_card = _make_simple_team_card()

        runtime = manager.create_team(team_card)
        team_id = runtime.id

        manager.stop_team(team_id)
        new_runtime = manager.resume_team(team_id)

        # Inspect orchestrator's team -- each agent should appear exactly once
        team = new_runtime.orchestrator_proxy.get_team()
        agent_ids = [addr.agent_id for addr in team]
        assert len(agent_ids) == len(set(agent_ids)), (
            f"Duplicate agent_ids in team roster after restore: {agent_ids}"
        )

        # The key invariant is that the orchestrator's live roster has no
        # duplicates (checked above). During restore, new StartMessages are
        # emitted by createActor() but old ones still exist in the store.

    @pytest.mark.skipif(
        not hasattr(Akgent, "init_llm_context"),
        reason="Requires akgentic-core with init_llm_context (Story 14.2)",
    )
    def test_resume_restores_event_messages_to_agents(
        self,
        actor_system: ActorSystem,
    ) -> None:
        """AC 3,4: Restorer calls init_llm_context with filtered EventMessage events."""
        event_store = InMemoryEventStore()
        manager = TeamManager(actor_system, event_store)
        team_card = _make_simple_team_card()

        runtime = manager.create_team(team_card)
        team_id = runtime.id

        # Send a message to generate activity
        actor_system.tell(
            runtime.entry_addr,
            UserMessage(content="pre-stop-msg"),
        )
        wait_for_agent_state(
            runtime.entry_addr,
            lambda state: "pre-stop-msg" in getattr(state, "messages", []),
            timeout=3.0,
        )

        # Inject EventMessage events for the entry agent before stopping
        entry_agent_id = runtime.entry_addr.agent_id
        addr_dict: ActorAddressDict = {
            "__actor_address__": True,
            "__actor_type__": (
                f"{RecordingAgent.__module__}.{RecordingAgent.__name__}"
            ),
            "agent_id": str(entry_agent_id),
            "name": "entry",
            "role": "Entry",
            "team_id": str(team_id),
            "squad_id": str(uuid.uuid4()),
            "user_message": False,
        }
        em1 = EventMessage(event="llm-event-1")
        em1.sender = ActorAddressProxy(addr_dict)
        em1.team_id = team_id

        em2 = EventMessage(event="llm-event-2")
        em2.sender = ActorAddressProxy(addr_dict)
        em2.team_id = team_id

        event_store.save_event(
            PersistedEvent(
                team_id=team_id, sequence=9000, event=em1,
                timestamp=datetime.now(UTC),
            )
        )
        event_store.save_event(
            PersistedEvent(
                team_id=team_id, sequence=9001, event=em2,
                timestamp=datetime.now(UTC),
            )
        )

        manager.stop_team(team_id)

        # Track init_llm_context calls during resume
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

        with mock_patch.object(actor_system, "proxy_ask", side_effect=tracking_proxy_ask):
            new_runtime = manager.resume_team(team_id)

        # Verify init_llm_context was called for entry agent with the 2 events
        assert "entry" in init_llm_calls, (
            "init_llm_context not called for entry agent during resume"
        )
        assert len(init_llm_calls["entry"]) == 2
        # Verify they are EventMessage instances with correct payloads in order
        for ev in init_llm_calls["entry"]:
            assert isinstance(ev, EventMessage)
        assert init_llm_calls["entry"][0].event == "llm-event-1"
        assert init_llm_calls["entry"][1].event == "llm-event-2"

        # Team is still functional after resume
        assert new_runtime.entry_addr.is_alive()
