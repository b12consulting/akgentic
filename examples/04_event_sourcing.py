"""Example 04: Event Sourcing -- Live Persistence & Inspection.

Demonstrates PersistenceSubscriber + YamlEventStore for persisting team events
and agent state snapshots. Shows the difference between append-only events and
overwrite-on-change state snapshots. Inspects the YAML file layout on disk.

Key concepts:
  - PersistenceSubscriber bridges EventSubscriber (core) with EventStore (team)
  - Events are append-only; agent state snapshots overwrite on each change
  - YAML file layout: {team_uuid}/team.yaml, events.yaml, states/{agent}.yaml

Run:
    uv run python packages/akgentic-team/examples/04_event_sourcing.py
"""

import logging
import tempfile
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

from akgentic.core.actor_address import ActorAddress
from akgentic.core.actor_address_impl import ActorAddressProxy  # seed_start_events workaround
from akgentic.core.actor_system_impl import ActorSystem
from akgentic.core.agent import Akgent
from akgentic.core.agent_card import AgentCard
from akgentic.core.agent_config import BaseConfig
from akgentic.core.agent_state import BaseState
from akgentic.core.messages.message import UserMessage
from akgentic.core.messages.orchestrator import StartMessage, StateChangedMessage
from akgentic.core.orchestrator import Orchestrator  # seed_start_events workaround
from akgentic.core.utils.deserializer import ActorAddressDict  # seed_start_events workaround

from akgentic.team.manager import TeamManager
from akgentic.team.models import (
    PersistedEvent,
    TeamCard,
    TeamCardMember,
    TeamRuntime,
)
from akgentic.team.ports import EventStore
from akgentic.team.repositories.yaml import YamlEventStore
from akgentic.team.subscriber import PersistenceSubscriber

# --- Inline CounterAgent ---


class CounterState(BaseState):
    """Agent state that tracks message count."""

    count: int = 0


class CounterAgent(Akgent[BaseConfig, CounterState]):
    """Agent that counts received messages in its state."""

    def on_start(self) -> None:
        """Initialize CounterState so the agent tracks a count field."""
        self.init_state(CounterState())

    def receiveMsg_UserMessage(  # noqa: N802
        self, message: UserMessage, sender: ActorAddress
    ) -> None:
        """Increment counter and persist state change."""
        self.state.count += 1
        print(f"  [{self.config.name}] count={self.state.count} (received: {message.content!r})")
        self.state.notify_state_change()


# --- seed_start_events helper (self-contained, no cross-file dependency) ---


def seed_start_events(
    runtime: TeamRuntime,
    team_card: TeamCard,
    event_store: EventStore,
) -> None:
    """Persist StartMessage events so TeamRestorer can rebuild the team.

    In the current framework, agents are spawned without an orchestrator
    reference, so their StartMessages are not automatically routed through
    the PersistenceSubscriber. This helper seeds the event store with the
    same StartMessage events that TeamRestorer expects during restore.
    """
    team_id = runtime.id
    seq = 0
    now = datetime.now(UTC)

    # 1. Orchestrator StartMessage
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
        config=BaseConfig(name="orchestrator", role="Orchestrator"),
    )
    orch_start.sender = ActorAddressProxy(orch_addr_dict)
    orch_start.team_id = team_id
    event_store.save_event(
        PersistedEvent(team_id=team_id, sequence=seq, event=orch_start, timestamp=now)
    )

    # 2. Agent StartMessages (walk the member tree)
    def _seed_member(member: TeamCardMember) -> None:
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
        event_store.save_event(
            PersistedEvent(team_id=team_id, sequence=seq, event=sm, timestamp=now)
        )
        for child in member.members:
            _seed_member(child)

    _seed_member(team_card.entry_point)
    for member in team_card.members:
        _seed_member(member)


def main() -> None:
    """Run the event sourcing demonstration."""
    # Suppress noisy loggers
    logging.getLogger("akgentic.core.actor_system_impl").setLevel(logging.ERROR)
    logging.getLogger("akgentic.team.manager").setLevel(logging.ERROR)

    # ================================================================
    # CREATE
    # ================================================================
    print("=== CREATE: Build a team with CounterAgent members ===")

    with tempfile.TemporaryDirectory() as tmp_dir:
        actor_system = ActorSystem()
        try:
            event_store = YamlEventStore(Path(tmp_dir))
            team_manager = TeamManager(actor_system=actor_system, event_store=event_store)

            # Build TeamCard with CounterAgent entry_point and worker
            leader_card = AgentCard(
                role="Leader",
                description="Entry-point counter agent",
                skills=["counting"],
                agent_class=CounterAgent,
                config=BaseConfig(name="leader", role="Leader"),
            )
            worker_card = AgentCard(
                role="Worker",
                description="Worker counter agent",
                skills=["counting"],
                agent_class=CounterAgent,
                config=BaseConfig(name="worker", role="Worker"),
            )

            worker_member = TeamCardMember(card=worker_card)
            leader_member = TeamCardMember(card=leader_card, members=[worker_member])

            team_card = TeamCard(
                name="counter-team",
                description="A team for demonstrating event sourcing",
                entry_point=leader_member,
                members=[],
                message_types=[UserMessage],
            )

            runtime = team_manager.create_team(team_card, user_id="demo")
            team_id = runtime.id
            print(f"  Team created with id: {team_id}")

            # Seed StartMessage events for persistence (required for resume)
            seed_start_events(runtime, team_card, event_store)

            # ================================================================
            # SEND MESSAGES
            # ================================================================
            print("\n=== SEND MESSAGES: Send 3 messages to increment counters ===")

            for i in range(1, 4):
                runtime.send(f"Message {i}")
                time.sleep(0.5)

            # ================================================================
            # DEMONSTRATE PersistenceSubscriber
            # ================================================================
            print("\n=== PERSISTENCE SUBSCRIBER: Show how events flow to storage ===")
            print("  PersistenceSubscriber is an EventSubscriber registered with the")
            print("  orchestrator. It intercepts every message and persists it as a")
            print("  PersistedEvent. StateChangedMessage triggers agent state snapshots.")
            print()

            # Demonstrate PersistenceSubscriber directly. NOTE: TeamManager already
            # registered one during create_team(), so events from runtime.send()
            # are also persisted by that subscriber. This second subscriber is for
            # explicit demonstration only -- its events will have separate sequence
            # numbers that overlap with the TeamManager's subscriber.
            persistence_sub = PersistenceSubscriber(team_id, event_store)

            # Simulate a UserMessage event flowing through the subscriber
            user_msg = UserMessage(content="demo-event")
            user_msg.sender = ActorAddressProxy(
                {
                    "__actor_address__": True,
                    "__actor_type__": f"{CounterAgent.__module__}.{CounterAgent.__name__}",
                    "agent_id": str(uuid.uuid4()),
                    "name": "leader",
                    "role": "Leader",
                    "team_id": str(team_id),
                    "squad_id": str(uuid.uuid4()),
                    "user_message": True,
                }
            )
            user_msg.team_id = team_id
            persistence_sub.on_message(user_msg)
            print("  -> PersistenceSubscriber.on_message(UserMessage) -> saved as PersistedEvent")

            # Simulate a StateChangedMessage flowing through the subscriber
            counter_state = CounterState(count=3)
            state_msg = StateChangedMessage(state=counter_state)
            state_msg.sender = ActorAddressProxy(
                {
                    "__actor_address__": True,
                    "__actor_type__": f"{CounterAgent.__module__}.{CounterAgent.__name__}",
                    "agent_id": str(uuid.uuid4()),
                    "name": "leader",
                    "role": "Leader",
                    "team_id": str(team_id),
                    "squad_id": str(uuid.uuid4()),
                    "user_message": False,
                }
            )
            state_msg.team_id = team_id
            persistence_sub.on_message(state_msg)
            print(
                "  -> PersistenceSubscriber.on_message(StateChangedMessage) "
                "-> saved event + AgentStateSnapshot"
            )

            # ================================================================
            # INSPECT EVENTS
            # ================================================================
            print("\n=== INSPECT EVENTS: View persisted event log ===")

            events = event_store.load_events(team_id)
            print(f"  Total persisted events: {len(events)}")
            assert len(events) > 0, "Expected at least one persisted event"

            for ev in events:
                print(
                    f"  seq={ev.sequence:3d}  "
                    f"timestamp={ev.timestamp.isoformat()[:19]}  "
                    f"type={type(ev.event).__name__}"
                )

            # ================================================================
            # INSPECT STATES
            # ================================================================
            print("\n=== INSPECT STATES: View agent state snapshots ===")

            snapshots = event_store.load_agent_states(team_id)
            print(f"  Total agent state snapshots: {len(snapshots)}")
            assert len(snapshots) > 0, "Expected at least one agent state snapshot"

            for snap in snapshots:
                print(
                    f"  agent={snap.agent_id:10s}  "
                    f"state={snap.state.model_dump()}  "
                    f"updated_at={snap.updated_at.isoformat()[:19]}"
                )

            snapshot_count_before = len(snapshots)

            # ================================================================
            # INSPECT FILE LAYOUT
            # ================================================================
            print("\n=== INSPECT FILE LAYOUT: YAML storage on disk ===")

            team_dir = Path(tmp_dir) / str(team_id)
            print(f"  Team directory: {team_dir}")

            team_yaml = team_dir / "team.yaml"
            events_yaml = team_dir / "events.yaml"
            states_dir = team_dir / "states"

            print(f"  team.yaml exists: {team_yaml.exists()}")
            print(f"  events.yaml exists: {events_yaml.exists()}")
            print(f"  states/ directory exists: {states_dir.exists()}")

            if states_dir.exists():
                state_files = sorted(states_dir.iterdir())
                for sf in state_files:
                    print(f"    states/{sf.name}")

            # ================================================================
            # APPEND-ONLY VS OVERWRITE
            # ================================================================
            print("\n=== APPEND-ONLY VS OVERWRITE: Send more events ===")

            event_count_before = len(events)
            print(f"  Events before: {event_count_before}")

            # Send 2 more events through the subscriber
            for i in range(4, 6):
                msg = UserMessage(content=f"Message {i}")
                msg.sender = user_msg.sender
                msg.team_id = team_id
                persistence_sub.on_message(msg)

            # Update state snapshot (overwrite)
            updated_state = CounterState(count=5)
            state_msg2 = StateChangedMessage(state=updated_state)
            state_msg2.sender = state_msg.sender
            state_msg2.team_id = team_id
            persistence_sub.on_message(state_msg2)

            events_after = event_store.load_events(team_id)
            event_count_after = len(events_after)
            print(f"  Events after:  {event_count_after}")
            assert event_count_after > event_count_before, (
                f"Events should grow: {event_count_after} > {event_count_before}"
            )
            print(
                f"  -> Events are APPEND-ONLY: grew from "
                f"{event_count_before} to {event_count_after}"
            )

            snapshots_after = event_store.load_agent_states(team_id)
            snapshot_count_after = len(snapshots_after)
            print(f"  Snapshots before: {snapshot_count_before}")
            print(f"  Snapshots after:  {snapshot_count_after}")
            assert snapshot_count_after == snapshot_count_before, (
                f"Snapshot count should be unchanged: "
                f"{snapshot_count_after} != {snapshot_count_before}"
            )
            print(
                f"  -> State snapshots are OVERWRITE: "
                f"same count ({snapshot_count_after}), but values updated"
            )

            for snap in snapshots_after:
                print(f"     agent={snap.agent_id:10s}  state={snap.state.model_dump()}")

            # ================================================================
            # CLEANUP
            # ================================================================
            print("\n=== All assertions passed! ===")
            print(
                "Example 04 complete: event sourcing with PersistenceSubscriber + "
                "YamlEventStore demonstrated."
            )

            team_manager.stop_team(team_id)

        finally:
            print("\n=== Shutting down ActorSystem ===")
            actor_system.shutdown()
            print("  ActorSystem shut down cleanly.")


if __name__ == "__main__":
    main()
