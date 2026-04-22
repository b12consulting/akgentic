"""Example 02: Team Factory -- Building a Running Team.

Demonstrates how to use TeamFactory.build() to create a running team from a
TeamCard, inspect the TeamRuntime, send messages, handle error paths, and
perform clean shutdown.

Run:
    uv run python packages/akgentic-team/examples/02_team_factory.py
"""

import logging
import time
import uuid

from akgentic.core.actor_address import ActorAddress
from akgentic.core.actor_system_impl import ActorSystem
from akgentic.core.agent import Akgent
from akgentic.core.agent_card import AgentCard
from akgentic.core.agent_config import BaseConfig
from akgentic.core.agent_state import BaseState
from akgentic.core.messages.message import UserMessage

from akgentic.team.factory import TeamFactory
from akgentic.team.models import TeamCard, TeamCardMember


# --- 2.1: Inline EchoAgent ---
class EchoAgent(Akgent[BaseConfig, BaseState]):
    """Minimal agent that echoes messages back."""

    def receiveMsg_UserMessage(  # noqa: N802
        self, message: UserMessage, sender: ActorAddress
    ) -> None:
        """Echo the message content."""
        print(f"  [{self.config.name}] received: {message.content!r}")


def main() -> None:
    """Build a running team, inspect it, send messages, and shut down cleanly."""
    # Suppress expected shutdown warnings from the actor system (e.g. "Stopping
    # remaining N actors") so example output stays clean.
    logging.getLogger("akgentic.core.actor_system_impl").setLevel(logging.ERROR)

    # --- 2.2: Build a TeamCard with 2 EchoAgent members ---
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
    # Leader supervises the worker (worker is a subordinate of leader)
    leader_member = TeamCardMember(card=leader_card, members=[worker_member])

    team_card = TeamCard(
        name="echo-team",
        description="A simple team with a leader supervising a worker",
        entry_point=leader_member,
        members=[],
        message_types=[UserMessage],
    )

    # --- 2.3: Create ActorSystem and build team ---
    actor_system = ActorSystem()
    try:
        print("=== Building team with TeamFactory ===")
        runtime = TeamFactory.build(
            team_card=team_card,
            actor_system=actor_system,
            subscribers=[],
        )

        # --- 2.4: Inspect TeamRuntime ---
        print("\n=== TeamRuntime ===")
        print(f"  runtime.id: {runtime.id}")
        print(f"  runtime.orchestrator_addr: {runtime.orchestrator_addr}")
        print(f"  runtime.entry_addr: {runtime.entry_addr}")
        print(f"  runtime.addrs: {dict(runtime.addrs)}")

        # --- 2.8: Assert runtime attributes ---
        assert isinstance(runtime.id, uuid.UUID), "runtime.id should be a UUID"
        assert "leader" in runtime.addrs, "leader should be in runtime.addrs"
        assert "worker" in runtime.addrs, "worker should be in runtime.addrs"
        assert runtime.entry_addr is not None, "entry_addr should not be None"
        assert runtime.orchestrator_addr is not None, "orchestrator_addr should not be None"

        print(f"\n  Addresses ({len(runtime.addrs)} agents):")
        for name, addr in runtime.addrs.items():
            print(f"    {name}: {addr}")

        # --- 2.5: Send messages ---
        # runtime.send() broadcasts to supervisor addresses. In this team,
        # the leader is the only supervisor (it has worker as subordinate),
        # so send() delivers to the leader -- not the worker.
        print("\n=== Sending broadcast to supervisors ===")
        runtime.send("Hello team!")
        time.sleep(0.5)  # Allow actor threads to process

        # Note: runtime.send_to(agent_name, content) is also available for
        # directed messaging to a specific agent. It uses the orchestrator's
        # agent registry, which requires agents to have completed their startup
        # handshake (StartMessage). In long-running teams this works naturally.

        # --- 2.6: Demonstrate error path ---
        print("\n=== Error path: entry_point with headcount != 1 ===")
        bad_member = TeamCardMember(card=leader_card, headcount=2)
        bad_card = TeamCard(
            name="bad-team",
            description="Team with invalid entry point headcount",
            entry_point=bad_member,
            members=[],
            message_types=[UserMessage],
        )
        error_caught = False
        try:
            TeamFactory.build(bad_card, actor_system, subscribers=[])
        except ValueError as e:
            error_caught = True
            print(f"  Caught expected ValueError: {e}")
        assert error_caught, "TeamFactory.build should have raised ValueError"

        print("\n=== All assertions passed! ===")
        print("Example 02 complete: team built, inspected, messaged, and error path verified.")

    finally:
        # --- 2.7: Clean shutdown ---
        print("\n=== Shutting down ActorSystem ===")
        actor_system.shutdown()
        print("  ActorSystem shut down cleanly.")


if __name__ == "__main__":
    main()
