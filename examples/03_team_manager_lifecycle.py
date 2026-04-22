"""Example 03: Team Manager Lifecycle -- Create, Stop, Resume, Delete.

Demonstrates the full TeamManager lifecycle including create_team, get_team,
stop_team, resume_team, and delete_team with YamlEventStore persistence.
Exercises the state machine: RUNNING -> STOPPED -> RUNNING -> STOPPED -> DELETED.
Also demonstrates error paths for invalid state transitions.

Run:
    uv run python packages/akgentic-team/examples/03_team_manager_lifecycle.py
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
from akgentic.core.messages.orchestrator import StartMessage  # seed_start_events workaround
from akgentic.core.orchestrator import Orchestrator  # seed_start_events workaround
from akgentic.core.utils.deserializer import ActorAddressDict  # seed_start_events workaround

from akgentic.team.manager import TeamManager
from akgentic.team.models import (
    PersistedEvent,
    TeamCard,
    TeamCardMember,
    TeamRuntime,
    TeamStatus,
)
from akgentic.team.ports import EventStore
from akgentic.team.repositories.yaml import YamlEventStore


# --- 1.1: Inline EchoAgent ---
class EchoAgent(Akgent[BaseConfig, BaseState]):
    """Minimal agent that echoes messages back."""

    def receiveMsg_UserMessage(  # noqa: N802
        self, message: UserMessage, sender: ActorAddress
    ) -> None:
        """Echo the message content."""
        print(f"  [{self.config.name}] received: {message.content!r}")


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

    See the companion doc (03-team-manager-lifecycle.md) for details on
    why this seeding step is required before resume_team() will work.
    """
    team_id = runtime.id
    seq = 0
    now = datetime.now(UTC)

    # 1. Orchestrator StartMessage
    seq += 1
    # NOTE: squad_id is fabricated here because the runtime does not expose
    # the orchestrator's actual squad_id. This is harmless — squad_id is not
    # used during restore — but the persisted value won't match the original.
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
        PersistedEvent(
            team_id=team_id, sequence=seq, event=orch_start, timestamp=now
        )
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
            PersistedEvent(
                team_id=team_id, sequence=seq, event=sm, timestamp=now
            )
        )
        for child in member.members:
            _seed_member(child)

    _seed_member(team_card.entry_point)
    for member in team_card.members:
        _seed_member(member)


def main() -> None:
    """Run the full TeamManager lifecycle demonstration."""
    # --- 1.3: Suppress actor system shutdown noise and TeamManager warnings ---
    logging.getLogger("akgentic.core.actor_system_impl").setLevel(logging.ERROR)
    logging.getLogger("akgentic.team.manager").setLevel(logging.ERROR)

    # --- 1.2: Temp directory for YAML storage ---
    with tempfile.TemporaryDirectory() as tmp_dir:
        # --- 1.3: Create ActorSystem, YamlEventStore, TeamManager ---
        actor_system = ActorSystem()
        try:
            event_store = YamlEventStore(Path(tmp_dir))
            team_manager = TeamManager(actor_system=actor_system, event_store=event_store)

            # --- 1.4: Build TeamCard with 2 EchoAgent members ---
            leader_card = AgentCard(
                role="Leader",
                description="Entry-point agent that receives external messages",
                skills=["coordination"],
                agent_class=EchoAgent,
                config=BaseConfig(name="leader", role="Leader"),
            )
            worker_card = AgentCard(
                role="Worker",
                description="Worker agent that processes tasks",
                skills=["processing"],
                agent_class=EchoAgent,
                config=BaseConfig(name="worker", role="Worker"),
            )

            worker_member = TeamCardMember(card=worker_card)
            leader_member = TeamCardMember(card=leader_card, members=[worker_member])

            team_card = TeamCard(
                name="lifecycle-team",
                description="A team for demonstrating TeamManager lifecycle",
                entry_point=leader_member,
                members=[],
                message_types=[UserMessage],
            )

            # ============================================================
            # CREATE
            # ============================================================
            print("=== CREATE: Build and persist a new team ===")

            # --- 1.5: create_team ---
            runtime = team_manager.create_team(team_card, user_id="demo")
            team_id = runtime.id
            print(f"  Team created with id: {team_id}")
            assert isinstance(team_id, uuid.UUID), "runtime.id should be a UUID"

            # Seed StartMessage events for resume capability. In the current
            # framework, agents are spawned without an orchestrator reference,
            # so their StartMessages must be persisted explicitly. This is the
            # same pattern used in the framework's test suite.
            seed_start_events(runtime, team_card, event_store)

            # ============================================================
            # INSPECT
            # ============================================================
            print("\n=== INSPECT: Query team metadata ===")

            # --- 1.6: get_team ---
            process = team_manager.get_team(team_id)
            assert process is not None, "get_team should return a Process"
            print(f"  Status: {process.status}")
            print(f"  User: {process.user_id}")
            print(f"  Created: {process.created_at}")
            assert process.status == TeamStatus.RUNNING, "Team should be RUNNING after create"

            # --- 1.7: Send a message ---
            print("\n  Sending message to team...")
            runtime.send("Hello team!")
            time.sleep(0.5)

            # ============================================================
            # STOP
            # ============================================================
            print("\n=== STOP: Gracefully stop the running team ===")

            # --- 1.8: stop_team ---
            team_manager.stop_team(team_id)
            process = team_manager.get_team(team_id)
            assert process is not None, "get_team should return a Process after stop"
            print(f"  Status after stop: {process.status}")
            assert process.status == TeamStatus.STOPPED, "Team should be STOPPED after stop"

            # ============================================================
            # RESUME
            # ============================================================
            print("\n=== RESUME: Restore team from persisted data ===")

            # --- 1.9: resume_team ---
            new_runtime = team_manager.resume_team(team_id)
            print(f"  Resumed team id: {new_runtime.id}")
            assert new_runtime.id == team_id, "Resumed team should have the same id"

            process = team_manager.get_team(team_id)
            assert process is not None, "get_team should return a Process after resume"
            assert process.status == TeamStatus.RUNNING, "Team should be RUNNING after resume"

            print("  Sending message to resumed team...")
            new_runtime.send("Hello again!")
            time.sleep(0.5)

            # ============================================================
            # STOP (again, before delete)
            # ============================================================
            print("\n=== STOP (again): Prepare for deletion ===")

            # --- 1.10: stop again ---
            team_manager.stop_team(team_id)
            process = team_manager.get_team(team_id)
            assert process is not None, "get_team should return a Process after second stop"
            assert process.status == TeamStatus.STOPPED, "Team should be STOPPED before delete"
            print(f"  Status: {process.status}")

            # ============================================================
            # DELETE
            # ============================================================
            print("\n=== DELETE: Purge all persisted team data ===")

            # --- 1.11: delete_team ---
            team_manager.delete_team(team_id)
            process = team_manager.get_team(team_id)
            assert process is None, "get_team should return None after delete"
            print("  Team deleted. get_team returns None.")

            # ============================================================
            # ERROR PATHS
            # ============================================================
            print("\n=== ERROR PATHS: Invalid state transitions ===")

            # --- 1.12: Error path demonstrations ---

            # Error 1: resume a RUNNING team
            print("\n  --- Error: resume a RUNNING team ---")
            runtime_b = team_manager.create_team(team_card, user_id="demo")
            team_b_id = runtime_b.id
            try:
                team_manager.resume_team(team_b_id)
                assert False, "resume_team on RUNNING should raise ValueError"  # noqa: B011
            except ValueError as e:
                print(f"  Caught expected ValueError: {e}")

            # Error 2: delete a RUNNING team
            print("\n  --- Error: delete a RUNNING team ---")
            try:
                team_manager.delete_team(team_b_id)
                assert False, "delete_team on RUNNING should raise ValueError"  # noqa: B011
            except ValueError as e:
                print(f"  Caught expected ValueError: {e}")

            # Clean up team B for the next error path test
            team_manager.stop_team(team_b_id)
            team_manager.delete_team(team_b_id)

            # Error 3: resume a DELETED team
            print("\n  --- Error: resume a DELETED team ---")
            try:
                team_manager.resume_team(team_b_id)
                assert False, "resume_team on DELETED should raise ValueError"  # noqa: B011
            except ValueError as e:
                print(f"  Caught expected ValueError: {e}")

            print("\n=== All assertions passed! ===")
            print(
                "Example 03 complete: full TeamManager lifecycle demonstrated "
                "with create, stop, resume, delete, and error paths."
            )

        finally:
            # --- 1.13: Guaranteed cleanup ---
            print("\n=== Shutting down ActorSystem ===")
            actor_system.shutdown()
            print("  ActorSystem shut down cleanly.")


if __name__ == "__main__":
    main()
