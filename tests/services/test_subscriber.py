"""Tests for PersistenceSubscriber — AC 1-7."""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock

from akgentic.core.actor_address import ActorAddress
from akgentic.core.messages.message import UserMessage
from akgentic.core.messages.orchestrator import StateChangedMessage
from akgentic.core.orchestrator import EventSubscriber

from akgentic.team.models import AgentStateSnapshot, PersistedEvent
from akgentic.team.subscriber import PersistenceSubscriber
from tests.models.conftest import SampleAgentState
from tests.services.conftest import InMemoryEventStore


class TestPersistenceSubscriber:
    """AC 1-7: PersistenceSubscriber persists events and state snapshots."""

    def _make_subscriber(
        self,
    ) -> tuple[PersistenceSubscriber, InMemoryEventStore, uuid.UUID]:
        """Create a PersistenceSubscriber with an InMemoryEventStore."""
        team_id = uuid.uuid4()
        store = InMemoryEventStore()
        sub = PersistenceSubscriber(team_id=team_id, event_store=store)
        return sub, store, team_id

    # -- 3.2: Event persistence on normal message --------------------------

    def test_event_persistence_on_user_message(self) -> None:
        """AC 1: UserMessage produces one PersistedEvent with correct fields."""
        sub, store, team_id = self._make_subscriber()
        msg = UserMessage(content="hello")

        sub.on_message(msg)

        assert len(store.events) == 1
        event = store.events[0]
        assert isinstance(event, PersistedEvent)
        assert event.team_id == team_id
        assert event.sequence == 1
        assert event.event == msg
        assert event.timestamp is not None

    # -- 3.3: Sequence numbering -------------------------------------------

    def test_sequence_increments_monotonically(self) -> None:
        """AC 1: Sequence numbers increment 1, 2, 3 across messages."""
        sub, store, _team_id = self._make_subscriber()

        sub.on_message(UserMessage(content="first"))
        sub.on_message(UserMessage(content="second"))
        sub.on_message(UserMessage(content="third"))

        sequences = [e.sequence for e in store.events]
        assert sequences == [1, 2, 3]

    # -- 3.4: Agent state snapshot on StateChangedMessage ------------------

    def test_state_snapshot_on_state_changed_message(self) -> None:
        """AC 1-3: StateChangedMessage writes snapshot only — no PersistedEvent, no sequence bump."""
        sub, store, team_id = self._make_subscriber()

        sender = MagicMock(spec=ActorAddress)
        sender.name = "worker-agent"
        state = SampleAgentState(task_count=5)
        msg = StateChangedMessage(sender=sender, state=state)

        sub.on_message(msg)

        # No event saved (AC 1: StateChangedMessage excluded from event log)
        assert len(store.events) == 0

        # Sequence not advanced (AC 3: gap-free numbering over behavioral events)
        assert sub._sequence == 0  # noqa: SLF001

        # Agent state snapshot saved (AC 2: snapshot still written)
        key = (team_id, "worker-agent")
        assert key in store.agent_states
        snapshot = store.agent_states[key]
        assert isinstance(snapshot, AgentStateSnapshot)
        assert snapshot.team_id == team_id
        assert snapshot.agent_id == "worker-agent"
        assert snapshot.updated_at is not None
        assert isinstance(snapshot.state, SampleAgentState)
        assert snapshot.state.task_count == 5

    def test_state_changed_does_not_break_sequence_continuity(self) -> None:
        """AC 3: Interleaved StateChangedMessage keeps event-log sequence gap-free."""
        sub, store, _team_id = self._make_subscriber()

        sender = MagicMock(spec=ActorAddress)
        sender.name = "agent-a"

        sub.on_message(UserMessage(content="first"))  # seq 1
        sub.on_message(StateChangedMessage(sender=sender, state=SampleAgentState(task_count=1)))
        sub.on_message(UserMessage(content="second"))  # seq 2
        sub.on_message(StateChangedMessage(sender=sender, state=SampleAgentState(task_count=2)))
        sub.on_message(UserMessage(content="third"))  # seq 3

        sequences = [e.sequence for e in store.events]
        assert sequences == [1, 2, 3]
        assert len(store.agent_states) == 1  # overwritten by last snapshot

    def test_state_changed_message_with_none_sender_skips_snapshot(self) -> None:
        """AC 2: StateChangedMessage with sender=None saves event but no snapshot."""
        sub, store, _team_id = self._make_subscriber()

        state = SampleAgentState(task_count=3)
        msg = StateChangedMessage(sender=None, state=state)

        sub.on_message(msg)

        assert len(store.events) == 1
        assert len(store.agent_states) == 0

    # -- 3.5: Restoring flag -----------------------------------------------

    def test_restoring_flag_skips_persistence(self) -> None:
        """AC 4: set_restoring(True) skips all persistence."""
        sub, store, _team_id = self._make_subscriber()

        sub.set_restoring(True)
        sub.on_message(UserMessage(content="ignored"))
        sub.on_message(UserMessage(content="also ignored"))

        assert len(store.events) == 0
        assert len(store.agent_states) == 0

    def test_restoring_flag_resumes_with_correct_sequence(self) -> None:
        """AC 4: After set_restoring(False), persistence resumes with correct sequence."""
        sub, store, _team_id = self._make_subscriber()

        # Send one message normally
        sub.on_message(UserMessage(content="first"))
        assert store.events[0].sequence == 1

        # Enable restoring, send messages
        sub.set_restoring(True)
        sub.on_message(UserMessage(content="skipped"))

        # Disable restoring, send message — sequence continues
        sub.set_restoring(False)
        sub.on_message(UserMessage(content="resumed"))

        assert len(store.events) == 2
        assert store.events[1].sequence == 2

    # -- 3.6: on_stop is callable (no-op) ----------------------------------

    def test_on_stop_is_callable(self) -> None:
        """Task 1.5: on_stop exists and does not raise."""
        sub, _store, _team_id = self._make_subscriber()
        sub.on_stop()  # Should not raise

    # -- 3.7: Explicit inheritance from EventSubscriber --------------------

    def test_is_instance_of_event_subscriber(self) -> None:
        """AC 3: PersistenceSubscriber explicitly inherits from EventSubscriber."""
        assert EventSubscriber in PersistenceSubscriber.__mro__

    # -- Story 14.8: initial_sequence parameter ------------------------------

    def test_initial_sequence_continues_from_provided_value(self) -> None:
        """AC 14.8: Sequence starts from initial_sequence, not 0."""
        team_id = uuid.uuid4()
        store = InMemoryEventStore()
        sub = PersistenceSubscriber(team_id=team_id, event_store=store, initial_sequence=22)

        sub.on_message(UserMessage(content="hello after resume"))

        events = store.events
        assert len(events) == 1
        assert events[0].sequence == 23

    def test_default_initial_sequence_starts_at_one(self) -> None:
        """AC 14.8: Without initial_sequence, first event gets sequence 1 (no regression)."""
        team_id = uuid.uuid4()
        store = InMemoryEventStore()
        sub = PersistenceSubscriber(team_id=team_id, event_store=store)

        sub.on_message(UserMessage(content="first"))

        events = store.events
        assert len(events) == 1
        assert events[0].sequence == 1

    def test_initial_sequence_with_multiple_messages(self) -> None:
        """AC 14.8: Multiple messages after initial_sequence continue monotonically."""
        team_id = uuid.uuid4()
        store = InMemoryEventStore()
        sub = PersistenceSubscriber(team_id=team_id, event_store=store, initial_sequence=10)

        sub.on_message(UserMessage(content="msg1"))
        sub.on_message(UserMessage(content="msg2"))
        sub.on_message(UserMessage(content="msg3"))

        sequences = [e.sequence for e in store.events]
        assert sequences == [11, 12, 13]
