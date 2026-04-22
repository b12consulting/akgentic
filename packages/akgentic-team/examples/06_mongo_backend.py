"""Example 06: MongoDB Backend -- MongoEventStore with Backend Portability.

Demonstrates the same create/stop/resume/delete lifecycle as example 03, but
using MongoEventStore backed by mongomock instead of YamlEventStore. Shows that
the EventStore protocol abstraction enables backend portability with zero code
changes to TeamManager or TeamCard.

Run:
    uv run python packages/akgentic-team/examples/06_mongo_backend.py
"""

import logging
import time
import uuid
from datetime import UTC, datetime

import mongomock
from akgentic.core.actor_address import ActorAddress
from akgentic.core.actor_address_impl import ActorAddressProxy
from akgentic.core.actor_system_impl import ActorSystem
from akgentic.core.agent import Akgent
from akgentic.core.agent_card import AgentCard
from akgentic.core.agent_config import BaseConfig
from akgentic.core.agent_state import BaseState
from akgentic.core.messages.message import UserMessage
from akgentic.core.messages.orchestrator import StartMessage
from akgentic.core.orchestrator import Orchestrator
from akgentic.core.utils.deserializer import ActorAddressDict

from akgentic.team.manager import TeamManager
from akgentic.team.models import (
    PersistedEvent,
    TeamCard,
    TeamCardMember,
    TeamRuntime,
    TeamStatus,
)
from akgentic.team.ports import EventStore
from akgentic.team.repositories.mongo import MongoEventStore


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
    """Run the full TeamManager lifecycle with MongoEventStore."""
    # Suppress noisy loggers
    logging.getLogger("akgentic.core.actor_system_impl").setLevel(logging.ERROR)
    logging.getLogger("akgentic.team.manager").setLevel(logging.ERROR)

    # --- 1.2: Create mongomock client and database ---
    client = mongomock.MongoClient()
    db = client["akgentic_example"]

    # --- 1.3: Create ActorSystem, MongoEventStore, TeamManager ---
    actor_system = ActorSystem()
    try:
        event_store = MongoEventStore(db)
        team_manager = TeamManager(actor_system=actor_system, event_store=event_store)

        # --- 1.4: Build TeamCard with EchoAgent entry_point and one worker ---
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
            name="mongo-lifecycle-team",
            description="A team demonstrating MongoEventStore lifecycle",
            entry_point=leader_member,
            members=[],
            message_types=[UserMessage],
        )

        # ============================================================
        # CREATE
        # ============================================================
        print("=== CREATE: Build and persist a new team with MongoEventStore ===")

        # --- 1.5: create_team and seed start events ---
        runtime = team_manager.create_team(team_card, user_id="demo")
        team_id = runtime.id
        print(f"  Team created with id: {team_id}")
        assert isinstance(team_id, uuid.UUID), "runtime.id should be a UUID"

        seed_start_events(runtime, team_card, event_store)

        # --- 1.6: Send a message ---
        print("\n  Sending message to team...")
        runtime.send("Hello from MongoDB-backed team!")
        time.sleep(0.5)

        # ============================================================
        # INSPECT MONGODB COLLECTIONS
        # ============================================================
        print("\n=== INSPECT MONGODB COLLECTIONS: Examine stored data ===")

        teams_count = db["teams"].count_documents({})
        events_count = db["events"].count_documents({})
        agent_states_count = db["agent_states"].count_documents({})

        print("  Document counts:")
        print(f"    teams: {teams_count}")
        print(f"    events: {events_count}")
        print(f"    agent_states: {agent_states_count}")

        assert teams_count == 1, f"Expected 1 team document, got {teams_count}"
        assert events_count > 0, f"Expected > 0 event documents, got {events_count}"

        print("\n  Teams collection:")
        for doc in db["teams"].find({}):
            doc.pop("_id", None)
            print(f"    team_id: {doc.get('team_id')}")
            print(f"    status: {doc.get('status')}")
            print(f"    user_id: {doc.get('user_id')}")

        print(f"\n  Events collection ({events_count} documents):")
        for doc in db["events"].find({}):
            doc.pop("_id", None)
            print(f"    seq={doc.get('sequence')}, team_id={str(doc.get('team_id', ''))[:8]}...")

        print(f"\n  Agent states collection ({agent_states_count} documents):")
        for doc in db["agent_states"].find({}):
            doc.pop("_id", None)
            agent_id = doc.get("agent_id", "")
            team_id_short = str(doc.get("team_id", ""))[:8]
            print(f"    agent_id={agent_id}, team_id={team_id_short}...")

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
        new_runtime.send("Hello again after resume!")
        time.sleep(0.5)

        # ============================================================
        # DELETE
        # ============================================================
        print("\n=== DELETE: Stop then purge all persisted team data ===")

        # --- 1.10: stop then delete ---
        team_manager.stop_team(team_id)
        team_manager.delete_team(team_id)
        process = team_manager.get_team(team_id)
        assert process is None, "get_team should return None after delete"
        print("  Team deleted. get_team returns None.")

        # ============================================================
        # VERIFY CLEANUP
        # ============================================================
        print("\n=== VERIFY CLEANUP: All MongoDB collections should be empty ===")

        # --- 1.11: Verify collections cleaned ---
        teams_after = db["teams"].count_documents({})
        events_after = db["events"].count_documents({})
        agent_states_after = db["agent_states"].count_documents({})

        print("  Document counts after delete:")
        print(f"    teams: {teams_after}")
        print(f"    events: {events_after}")
        print(f"    agent_states: {agent_states_after}")

        assert teams_after == 0, f"Expected 0 team documents after delete, got {teams_after}"
        assert events_after == 0, f"Expected 0 event documents after delete, got {events_after}"
        assert (
            agent_states_after == 0
        ), f"Expected 0 agent_state documents after delete, got {agent_states_after}"

        # ============================================================
        # BACKEND PORTABILITY
        # ============================================================
        print("\n=== BACKEND PORTABILITY: Only the EventStore constructor changes ===")

        print(
            """
  The entire lifecycle above is IDENTICAL to example 03 (YAML backend).
  The ONLY difference is how the EventStore is created:

    # YAML setup (example 03):
    #   event_store = YamlEventStore(Path(tmp_dir))

    # MongoDB setup (this example):
    #   client = mongomock.MongoClient()
    #   db = client["akgentic_example"]
    #   event_store = MongoEventStore(db)

  Everything else is IDENTICAL:
    - TeamManager(actor_system=..., event_store=...)
    - TeamCard definition
    - create_team(), stop_team(), resume_team(), delete_team()
    - seed_start_events() helper
    - All lifecycle operations and assertions

  This is the power of the EventStore Protocol abstraction:
  swap the backend, keep the same TeamManager code."""
        )

        print("\n=== All assertions passed! ===")
        print(
            "Example 06 complete: full lifecycle demonstrated with MongoEventStore "
            "backed by mongomock. Backend portability confirmed."
        )

    finally:
        # --- 1.13: Guaranteed cleanup ---
        print("\n=== Shutting down ActorSystem ===")
        actor_system.shutdown()
        client.close()
        print("  ActorSystem shut down cleanly.")


if __name__ == "__main__":
    main()
