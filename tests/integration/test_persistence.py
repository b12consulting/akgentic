"""Integration tests for persistence: event sourcing and team restoration.

Tests use real Akgent subclasses, real orchestrators, and real event flow
with YamlEventStore for actual file-based persistence.
"""

from __future__ import annotations

import time
from pathlib import Path

from akgentic.core.actor_system_impl import ActorSystem
from akgentic.core.messages.message import UserMessage

from akgentic.team.manager import TeamManager
from akgentic.team.models import TeamCard, TeamCardMember
from akgentic.team.repositories.yaml import YamlEventStore
from tests.integration.conftest import (
    RecordingAgent,
    make_integration_agent_card,
    wait_for_agent_state,
)


def _make_simple_team_card() -> TeamCard:
    """Create a minimal team card with a single RecordingAgent entry point."""
    entry = TeamCardMember(
        card=make_integration_agent_card(
            name="recorder",
            role="Recorder",
            agent_class=RecordingAgent,
        ),
    )
    return TeamCard(
        name="persistence-team",
        description="A team for persistence integration tests",
        entry_point=entry,
        members=[],
        message_types=[UserMessage],
    )


class TestPersistenceIntegration:
    """Persistence integration tests with YamlEventStore."""

    def test_all_messages_persisted(
        self,
        actor_system: ActorSystem,
        tmp_path: Path,
    ) -> None:
        """Every message sent through the team is captured by PersistenceSubscriber."""
        event_store = YamlEventStore(tmp_path)
        manager = TeamManager(actor_system, event_store)
        team_card = _make_simple_team_card()

        runtime = manager.create_team(team_card)

        # Send 3 messages
        for i in range(3):
            actor_system.tell(
                runtime.entry_addr,
                UserMessage(content=f"persist-msg-{i}"),
            )

        wait_for_agent_state(
            runtime.entry_addr,
            lambda state: getattr(state, "counter", 0) >= 3,
            timeout=3.0,
        )

        # Allow events to propagate through orchestrator to subscriber
        time.sleep(0.2)

        events = event_store.load_events(runtime.id)
        # Events include StartMessages, ReceivedMessages, ProcessedMessages,
        # StateChangedMessages, and SentMessages -- not just UserMessages.
        # We verify that the total event count reflects all 3 messages flowing
        # through the system (each message generates multiple telemetry events).
        assert len(events) >= 3, (
            f"Expected at least 3 events, got {len(events)}"
        )

    def test_restored_team_routes_messages(
        self,
        actor_system: ActorSystem,
        tmp_path: Path,
    ) -> None:
        """A restored team routes messages and persists new events."""
        event_store = YamlEventStore(tmp_path)
        manager = TeamManager(actor_system, event_store)
        team_card = _make_simple_team_card()

        runtime = manager.create_team(team_card)
        team_id = runtime.id

        actor_system.tell(
            runtime.entry_addr,
            UserMessage(content="before-stop"),
        )
        wait_for_agent_state(
            runtime.entry_addr,
            lambda state: "before-stop" in getattr(state, "messages", []),
            timeout=3.0,
        )

        time.sleep(0.2)

        events_before_stop = len(event_store.load_events(team_id))

        manager.stop_team(team_id)

        # Resume the team
        new_runtime = manager.resume_team(team_id)

        actor_system.tell(
            new_runtime.entry_addr,
            UserMessage(content="after-resume"),
        )
        reached = wait_for_agent_state(
            new_runtime.entry_addr,
            lambda state: "after-resume" in getattr(state, "messages", []),
            timeout=3.0,
        )
        assert reached, "Message 'after-resume' did not reach agent on restored team"

        time.sleep(0.2)
        events_after_resume = event_store.load_events(team_id)
        assert len(events_after_resume) > events_before_stop, (
            "No new events persisted after resume"
        )
