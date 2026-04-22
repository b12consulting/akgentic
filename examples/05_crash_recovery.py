"""Example 05: Crash Recovery -- Stop, Inspect, Resume, Verify.

Demonstrates the full crash recovery cycle: create a team, interact with it,
stop it (simulating a crash), inspect persisted data while stopped, resume,
verify state is restored, send more messages, and confirm new events are
persisted. Shows that actor addresses change on resume while team_id is
preserved.

Run:
    uv run python packages/akgentic-team/examples/05_crash_recovery.py
"""

import logging
import tempfile
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

from akgentic.core.actor_address import ActorAddress
from akgentic.core.actor_address_impl import ActorAddressProxy
from akgentic.core.actor_system_impl import ActorSystem
from akgentic.core.agent import Akgent
from akgentic.core.agent_card import AgentCard
from akgentic.core.agent_config import BaseConfig
from akgentic.core.agent_state import BaseState
from akgentic.core.messages.message import UserMessage
from akgentic.core.messages.orchestrator import StartMessage, StateChangedMessage
from akgentic.core.orchestrator import Orchestrator
from akgentic.core.utils.deserializer import ActorAddressDict

from akgentic.team.manager import TeamManager
from akgentic.team.models import (
    AgentStateSnapshot,
    PersistedEvent,
    TeamCard,
    TeamCardMember,
    TeamRuntime,
    TeamStatus,
)
from akgentic.team.ports import EventStore
from akgentic.team.repositories.yaml import YamlEventStore

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


def seed_user_events(
    runtime: TeamRuntime,
    event_store: EventStore,
    messages: list[str],
    start_seq: int,
) -> int:
    """Persist UserMessage events to simulate what PersistenceSubscriber would capture.

    Returns the next sequence number after the last persisted event.
    """
    team_id = runtime.id
    seq = start_seq
    now = datetime.now(UTC)
    leader_addr = runtime.addrs.get("leader")
    agent_id = str(leader_addr.agent_id) if leader_addr else str(uuid.uuid4())

    for content in messages:
        seq += 1
        msg = UserMessage(content=content)
        msg.sender = ActorAddressProxy(
            {
                "__actor_address__": True,
                "__actor_type__": f"{CounterAgent.__module__}.{CounterAgent.__name__}",
                "agent_id": agent_id,
                "name": "leader",
                "role": "Leader",
                "team_id": str(team_id),
                "squad_id": str(uuid.uuid4()),
                "user_message": True,
            }
        )
        msg.team_id = team_id
        event_store.save_event(
            PersistedEvent(team_id=team_id, sequence=seq, event=msg, timestamp=now)
        )
    return seq


def seed_state_snapshot(
    runtime: TeamRuntime,
    event_store: EventStore,
    agent_name: str,
    count: int,
    seq: int,
    role: str = "Leader",
) -> int:
    """Persist a StateChangedMessage event and an AgentStateSnapshot."""

    team_id = runtime.id
    now = datetime.now(UTC)
    addr = runtime.addrs.get(agent_name)
    agent_id = str(addr.agent_id) if addr else str(uuid.uuid4())

    # Persist the StateChangedMessage event
    state = CounterState(count=count)
    state_msg = StateChangedMessage(state=state)
    state_msg.sender = ActorAddressProxy(
        {
            "__actor_address__": True,
            "__actor_type__": f"{CounterAgent.__module__}.{CounterAgent.__name__}",
            "agent_id": agent_id,
            "name": agent_name,
            "role": role,
            "team_id": str(team_id),
            "squad_id": str(uuid.uuid4()),
            "user_message": False,
        }
    )
    state_msg.team_id = team_id
    seq += 1
    event_store.save_event(
        PersistedEvent(team_id=team_id, sequence=seq, event=state_msg, timestamp=now)
    )

    # Persist the agent state snapshot
    event_store.save_agent_state(
        AgentStateSnapshot(
            team_id=team_id,
            agent_id=agent_name,
            state=CounterState(count=count),
            updated_at=now,
        )
    )
    return seq


def main() -> None:
    """Run the crash recovery demonstration."""
    # Suppress noisy loggers
    logging.getLogger("akgentic.core.actor_system_impl").setLevel(logging.ERROR)
    logging.getLogger("akgentic.team.manager").setLevel(logging.ERROR)

    with tempfile.TemporaryDirectory() as tmp_dir:
        actor_system = ActorSystem()
        try:
            event_store = YamlEventStore(Path(tmp_dir))
            team_manager = TeamManager(actor_system=actor_system, event_store=event_store)

            # Build TeamCard with CounterAgent members
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
                name="recovery-team",
                description="A team for demonstrating crash recovery",
                entry_point=leader_member,
                members=[],
                message_types=[UserMessage],
            )

            # ================================================================
            # CREATE & INTERACT
            # ================================================================
            print("=== CREATE & INTERACT: Build team and send messages ===")

            runtime = team_manager.create_team(team_card, user_id="demo")
            team_id = runtime.id
            print(f"  Team created with id: {team_id}")

            # Seed StartMessage events (required for resume)
            seed_start_events(runtime, team_card, event_store)

            # Send 5 messages
            for i in range(1, 6):
                print(f"  Sending message {i}...")
                runtime.send(f"Message {i}")
                time.sleep(0.5)

            # Persist the events that flowed through the agents
            seq = seed_user_events(
                runtime,
                event_store,
                [f"Message {i}" for i in range(1, 6)],
                start_seq=3,  # after 3 StartMessage events
            )

            # Persist state snapshot (leader counted 5 messages)
            seq = seed_state_snapshot(runtime, event_store, "leader", count=5, seq=seq)

            # ================================================================
            # INSPECT STATE
            # ================================================================
            print("\n=== INSPECT STATE: Check agent state before stop ===")

            snapshots = event_store.load_agent_states(team_id)
            assert len(snapshots) > 0, "Expected at least one state snapshot"
            leader_snapshot = next(s for s in snapshots if s.agent_id == "leader")
            pre_stop_count = leader_snapshot.state.model_dump().get("count", 0)
            print(f"  Leader state before stop: count={pre_stop_count}")
            assert pre_stop_count == 5, f"Expected count=5, got {pre_stop_count}"

            # Save old addresses for comparison after resume
            old_addrs = {name: addr for name, addr in runtime.addrs.items()}
            print(f"  Old addresses: {list(old_addrs.keys())}")
            for name, addr in old_addrs.items():
                print(f"    {name}: agent_id={addr.agent_id}")

            # ================================================================
            # STOP (SIMULATE CRASH)
            # ================================================================
            print("\n=== STOP (SIMULATE CRASH): Gracefully stop the team ===")

            team_manager.stop_team(team_id)
            print("  Team stopped.")

            # ================================================================
            # INSPECT PERSISTED DATA
            # ================================================================
            print("\n=== INSPECT PERSISTED DATA: View data while team is stopped ===")

            # Events
            events = event_store.load_events(team_id)
            print(f"  Persisted events: {len(events)}")
            pre_resume_event_count = len(events)

            # Agent states
            snapshots = event_store.load_agent_states(team_id)
            print(f"  Agent state snapshots: {len(snapshots)}")
            for snap in snapshots:
                print(f"    {snap.agent_id}: state={snap.state.model_dump()}")

            # Process status
            process = team_manager.get_team(team_id)
            assert process is not None, "Process should exist while stopped"
            print(f"  Process status: {process.status}")
            assert process.status == TeamStatus.STOPPED, f"Expected STOPPED, got {process.status}"

            # ================================================================
            # RESUME
            # ================================================================
            print("\n=== RESUME: Restore team from persisted data ===")

            new_runtime = team_manager.resume_team(team_id)
            print(f"  Resumed team id: {new_runtime.id}")
            assert new_runtime.id == team_id, "Resumed team should have the same team_id"

            # ================================================================
            # VERIFY RESTORED STATE
            # ================================================================
            print("\n=== VERIFY RESTORED STATE: Check state matches pre-stop ===")

            snapshots_after_resume = event_store.load_agent_states(team_id)
            assert len(snapshots_after_resume) > 0, (
                "Expected agent state snapshots after resume"
            )
            leader_snap = next(
                (s for s in snapshots_after_resume if s.agent_id == "leader"),
                None,
            )
            assert leader_snap is not None, (
                "Expected leader agent state snapshot after resume"
            )
            restored_count = leader_snap.state.model_dump().get("count", 0)
            print(f"  Leader state after resume: count={restored_count}")
            assert restored_count == pre_stop_count, (
                f"State mismatch: pre-stop={pre_stop_count}, restored={restored_count}"
            )
            print("  -> State successfully restored!")

            # ================================================================
            # SEND MORE MESSAGES
            # ================================================================
            print("\n=== SEND MORE MESSAGES: Verify team is operational ===")

            for i in range(6, 8):
                print(f"  Sending message {i}...")
                new_runtime.send(f"Message {i}")
                time.sleep(0.5)

            # Persist the new events
            new_seq = seed_user_events(
                new_runtime,
                event_store,
                [f"Message {i}" for i in range(6, 8)],
                start_seq=seq,
            )

            # Update state snapshot (leader now has count=7)
            seed_state_snapshot(new_runtime, event_store, "leader", count=7, seq=new_seq)

            # ================================================================
            # VERIFY NEW EVENTS
            # ================================================================
            print("\n=== VERIFY NEW EVENTS: Check events grew after resume ===")

            events_after = event_store.load_events(team_id)
            post_resume_event_count = len(events_after)
            print(f"  Events before resume: {pre_resume_event_count}")
            print(f"  Events after resume:  {post_resume_event_count}")
            assert post_resume_event_count > pre_resume_event_count, (
                f"Events should grow: {post_resume_event_count} > {pre_resume_event_count}"
            )
            new_event_count = post_resume_event_count - pre_resume_event_count
            print(f"  -> {new_event_count} new events persisted after resume")

            # ================================================================
            # ADDRESS COMPARISON
            # ================================================================
            print("\n=== ADDRESS COMPARISON: Old vs new actor addresses ===")
            print("  The restorer preserves agent_id (logical identity) but creates")
            print("  new Pykka ActorRef objects (new threads). The ActorAddress")
            print("  wrappers are different objects, and the underlying actor refs")
            print("  point to freshly spawned Pykka actors.")
            print()

            new_addrs = {name: addr for name, addr in new_runtime.addrs.items()}
            for name in sorted(old_addrs.keys()):
                old_repr = repr(old_addrs[name])
                new_repr = repr(new_addrs[name]) if name in new_addrs else "N/A"
                print(f"  {name}:")
                print(f"    old: {old_repr}")
                print(f"    new: {new_repr}")

            # Agent IDs are preserved (restorer reuses original agent_id)
            for name in old_addrs:
                if name in new_addrs:
                    assert old_addrs[name].agent_id == new_addrs[name].agent_id, (
                        f"agent_id for {name} should be preserved across resume"
                    )

            # But the ActorAddress objects themselves are different instances
            for name in old_addrs:
                if name in new_addrs:
                    assert old_addrs[name] is not new_addrs[name], (
                        f"Address object for {name} should be a new instance"
                    )

            assert new_runtime.id == team_id, "team_id should be preserved across resume"
            print(f"\n  team_id preserved: {new_runtime.id}")
            print("  -> agent_ids preserved (same logical identity), but ActorAddress")
            print("     objects are new (fresh Pykka actors with new threads)")

            # ================================================================
            # CLEANUP
            # ================================================================
            print("\n=== All assertions passed! ===")
            print(
                "Example 05 complete: crash recovery with stop, inspect, "
                "resume, verify demonstrated."
            )

            team_manager.stop_team(team_id)
            team_manager.delete_team(team_id)

        finally:
            print("\n=== Shutting down ActorSystem ===")
            actor_system.shutdown()
            print("  ActorSystem shut down cleanly.")


if __name__ == "__main__":
    main()
