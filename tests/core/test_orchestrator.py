"""Tests for Orchestrator agent."""

import uuid
from collections.abc import Generator
from dataclasses import dataclass
from typing import Any, Never

import pykka
import pytest

from akgentic.core.actor_address_impl import ActorAddressImpl, ActorAddressProxy
from akgentic.core.actor_system_impl import ActorSystem
from akgentic.core.agent import Akgent
from akgentic.core.agent_config import BaseConfig
from akgentic.core.agent_state import BaseState
from akgentic.core.messages.message import Message, UserMessage
from akgentic.core.messages.orchestrator import EventMessage, SentMessage, StartMessage
from akgentic.core.orchestrator import EventSubscriber, Orchestrator


@pytest.fixture(autouse=True)
def cleanup_actors() -> Generator[None, None, None]:
    """Ensure all actors are stopped after each test."""
    yield
    pykka.ActorRegistry.stop_all()


class MockEventSubscriber:
    """Test event subscriber for verifying notification pattern."""

    def __init__(self) -> None:
        """Initialize event tracking."""
        self.events: list[str] = []

    def on_agent_started(self) -> None:
        self.events.append("agent_started")

    def on_agent_stopped(self) -> None:
        """Track agent stopped events."""
        self.events.append("agent_stopped")

    def on_state_changed(self) -> None:
        self.events.append("state_changed")

    def on_message_sent(self) -> None:
        self.events.append("message_sent")

    def on_message_received(
        self,
    ) -> None:
        """Track message received events."""
        self.events.append("message_received")

    def on_message_processed(self) -> None:
        """Track message processed events."""
        self.events.append("message_processed")

    def on_error(self) -> None:
        """Track error events."""
        self.events.append("error")


class FailingSubscriber:
    """Subscriber that raises exceptions to test fault tolerance."""

    def on_agent_started(self) -> Never:
        """Always raise exception."""
        raise RuntimeError("Subscriber failure")

    def on_agent_stopped(self) -> Never:
        """Always raise exception."""
        raise RuntimeError("Subscriber failure")

    def on_state_changed(self) -> Never:
        """Always raise exception."""
        raise RuntimeError("Subscriber failure")

    def on_message_sent(self) -> Never:
        raise RuntimeError("Subscriber failure")

    def on_message_received(self) -> Never:
        raise RuntimeError("Subscriber failure")

    def on_message_processed(self) -> Never:
        """Always raise exception."""
        raise RuntimeError("Subscriber failure")

    def on_error(self) -> Never:
        """Always raise exception."""
        raise RuntimeError("Subscriber failure")


class SimpleAgent(Akgent[BaseConfig, BaseState]):
    """Simple agent for testing orchestrator interaction."""

    def receiveMsg_str(self, msg: str) -> str:
        """Handle string messages."""
        return f"received: {msg}"


class TestOrchestratorInitialization:
    """Tests for Orchestrator initialization."""

    def test_orchestrator_initializes_with_empty_state(self) -> None:
        """Test that orchestrator initializes with its own StartMessage."""
        from akgentic.core.messages.orchestrator import StartMessage

        config = BaseConfig(name="test-orchestrator", role="Orchestrator")
        orch_ref = Orchestrator.start(config=config)
        orch = orch_ref.proxy()

        # Verify initialization - orchestrator sends its own StartMessage
        messages = orch.get_messages().get()
        assert len(messages) == 1
        assert isinstance(messages[0], StartMessage)
        assert messages[0].config.name == "test-orchestrator"

        # Other collections remain empty
        assert orch.get_states().get() == {}
        assert orch.get_team().get() == []  # Orchestrator excluded from team

        orch_ref.stop()


class TestTeamManagement:
    """Tests for team management methods."""

    def test_get_team_empty_initially(self) -> None:
        """Test get_team returns empty list initially."""
        config = BaseConfig(name="test-orchestrator", role="Orchestrator")
        orch_ref = Orchestrator.start(config=config)
        orch = orch_ref.proxy()

        team = orch.get_team().get()
        assert team == []

        orch_ref.stop()

    def test_get_team_member_not_found(self) -> None:
        """Test get_team_member returns None for non-existent agent."""
        config = BaseConfig(name="test-orchestrator", role="Orchestrator")
        orch_ref = Orchestrator.start(config=config)
        orch = orch_ref.proxy()

        found = orch.get_team_member("non-existent").get()
        assert found is None

        orch_ref.stop()


class TestQueryMethods:
    """Tests for query methods."""

    def test_get_messages_all(self) -> None:
        """Test get_messages returns all messages when no filter."""
        from akgentic.core.messages.orchestrator import StartMessage

        config = BaseConfig(name="test-orchestrator", role="Orchestrator")
        orch_ref = Orchestrator.start(config=config)
        orch = orch_ref.proxy()

        # Initially contains orchestrator's own StartMessage
        messages = orch.get_messages().get()
        assert len(messages) == 1
        assert isinstance(messages[0], StartMessage)

        orch_ref.stop()

    def test_get_states(self) -> None:
        """Test get_states returns state_dict."""
        config = BaseConfig(name="test-orchestrator", role="Orchestrator")
        orch_ref = Orchestrator.start(config=config)
        orch = orch_ref.proxy()

        # Initially empty
        states = orch.get_states().get()
        assert states == {}

        orch_ref.stop()


class TestZeroDependencies:
    """Tests verifying zero external dependencies."""

    def test_no_redis_imports(self) -> None:
        """Test that orchestrator module has no Redis imports."""
        import akgentic.core.orchestrator as orch_module

        # Check module-level imports
        module_globals = vars(orch_module)
        assert "redis" not in module_globals
        assert "aioredis" not in module_globals

    def test_orchestrator_works_without_redis(self) -> None:
        """Test orchestrator functions without Redis."""
        from akgentic.core.messages.orchestrator import StartMessage

        config = BaseConfig(name="test-orchestrator", role="Orchestrator")
        orch_ref = Orchestrator.start(config=config)
        orch = orch_ref.proxy()

        # Perform basic operations
        team = orch.get_team().get()
        assert team == []

        # Orchestrator records its own StartMessage
        messages = orch.get_messages().get()
        assert len(messages) == 1
        assert isinstance(messages[0], StartMessage)

        orch_ref.stop()


class TestIntegration:
    """Integration tests for Orchestrator with real agent system."""

    def test_orchestrator_integration_with_actor_system(self) -> None:
        """Test orchestrator integrates with ActorSystem."""
        # Create actor system
        system = ActorSystem()

        # Create orchestrator - should integrate without errors
        orch_config = BaseConfig(name="orchestrator", role="Orchestrator")
        orch_addr = system.createActor(Orchestrator, config=orch_config)

        # Verify orchestrator is alive
        assert orch_addr.is_alive()

        # Verify basic functionality
        orch_proxy = orch_addr._actor_ref.proxy()  # type: ignore
        team = orch_proxy.get_team().get()
        assert isinstance(team, list)

        # Cleanup
        system.shutdown()

    def test_zero_infrastructure_requirements(self) -> None:
        """Test that orchestrator requires no external infrastructure."""
        from akgentic.core.messages.orchestrator import StartMessage

        # This test verifies orchestrator can be created and used without Redis/DB
        system = ActorSystem()

        # Create orchestrator - should not throw any connection errors
        orch_config = BaseConfig(name="orchestrator", role="Orchestrator")
        orch_addr = system.createActor(Orchestrator, config=orch_config)

        # Verify basic functionality
        orch_proxy = orch_addr._actor_ref.proxy()  # type: ignore
        messages = orch_proxy.get_messages().get()
        # Orchestrator records its own StartMessage
        assert len(messages) == 1
        assert isinstance(messages[0], StartMessage)

        system.shutdown()


class RecordingSubscriber:
    """Subscriber that records on_message calls for restore tests.

    Implements the full EventSubscriber protocol: on_message, on_stop,
    on_stop_request, set_restoring (all team_id-aware).
    """

    def __init__(self) -> None:
        self.messages: list[Message] = []
        self.stopped: bool = False
        self.restoring: bool = False
        self.stop_team_ids: list[uuid.UUID] = []
        self.stop_request_team_ids: list[uuid.UUID] = []
        self.restoring_calls: list[tuple[uuid.UUID, bool]] = []

    def set_restoring(self, team_id: uuid.UUID, restoring: bool) -> None:  # noqa: FBT001
        """Track restore-replay guard state."""
        self.restoring = restoring
        self.restoring_calls.append((team_id, restoring))

    def on_message(self, msg: Message) -> None:
        """Record received message."""
        self.messages.append(msg)

    def on_stop(self, team_id: uuid.UUID) -> None:
        """Record stop."""
        self.stopped = True
        self.stop_team_ids.append(team_id)

    def on_stop_request(self, team_id: uuid.UUID) -> None:
        """Record stop request."""
        self.stop_request_team_ids.append(team_id)


class TestUnsubscribe:
    """Tests for Orchestrator.unsubscribe."""

    def test_unsubscribe_removes_subscriber(self) -> None:
        """unsubscribe removes a subscriber from notification list."""
        system = ActorSystem()
        orch = system.createActor(Orchestrator, restoring=True)
        proxy = system.proxy_ask(orch, Orchestrator)

        sub = RecordingSubscriber()
        proxy.subscribe(sub)
        assert sub in proxy.subscribers

        proxy.unsubscribe(sub)
        assert sub not in proxy.subscribers

        system.shutdown()

    def test_unsubscribe_unknown_is_noop(self) -> None:
        """unsubscribe on unknown subscriber is idempotent."""
        system = ActorSystem()
        orch = system.createActor(Orchestrator, restoring=True)
        proxy = system.proxy_ask(orch, Orchestrator)

        sub = RecordingSubscriber()
        proxy.unsubscribe(sub)  # should not raise

        system.shutdown()

    def test_unsubscribed_subscriber_stops_receiving_events(self) -> None:
        """After unsubscribe, events are no longer dispatched to the subscriber."""
        system = ActorSystem()
        orch = system.createActor(Orchestrator, restoring=True)
        proxy = system.proxy_ask(orch, Orchestrator)

        sub = RecordingSubscriber()
        proxy.subscribe(sub)
        proxy.restore_message(UserMessage(content="before"))
        assert len(sub.messages) == 1

        proxy.unsubscribe(sub)
        proxy.restore_message(UserMessage(content="after"))
        assert len(sub.messages) == 1  # no new message

        system.shutdown()


class TestRestoreMessage:
    """Tests for Orchestrator.restore_message and end_restoration."""

    def test_restore_message_dispatches_to_subscribers(self) -> None:
        """restore_message notifies subscribers via on_message."""
        system = ActorSystem()
        orch_addr = system.createActor(
            Orchestrator,
            config=BaseConfig(name="orchestrator", role="Orchestrator"),
        )
        orch_proxy = system.proxy_ask(orch_addr, Orchestrator)

        sub = RecordingSubscriber()
        orch_proxy.subscribe(sub)

        msg = UserMessage(content="replayed message")
        orch_proxy.restore_message(msg)

        assert len(sub.messages) == 1
        assert sub.messages[0] is msg

        system.shutdown()

    def test_restore_message_dispatches_multiple(self) -> None:
        """restore_message dispatches to all registered subscribers."""
        system = ActorSystem()
        orch_addr = system.createActor(
            Orchestrator,
            config=BaseConfig(name="orchestrator", role="Orchestrator"),
        )
        orch_proxy = system.proxy_ask(orch_addr, Orchestrator)

        sub1 = RecordingSubscriber()
        sub2 = RecordingSubscriber()
        orch_proxy.subscribe(sub1)
        orch_proxy.subscribe(sub2)

        msg = UserMessage(content="test")
        orch_proxy.restore_message(msg)

        assert len(sub1.messages) == 1
        assert len(sub2.messages) == 1

        system.shutdown()

    def test_end_restoration_toggles_restoring_flag(self) -> None:
        """end_restoration sets _restoring to False."""
        system = ActorSystem()
        orch_addr = system.createActor(
            Orchestrator,
            config=BaseConfig(name="orchestrator", role="Orchestrator"),
            restoring=True,
        )
        orch_proxy = system.proxy_ask(orch_addr, Orchestrator)

        orch_proxy.end_restoration()

        # After end_restoration, _restoring should be False
        # We verify indirectly: the orchestrator is alive and functional
        team = orch_proxy.get_team()
        assert team == []

        system.shutdown()


# ---------------------------------------------------------------------------
# Story 22.1 — Orchestrator.emitMessage fan-out seam
# ---------------------------------------------------------------------------


class _PersistenceLikeSubscriber:
    """Durable-sink fake: records every message handed to ``on_message``.

    Models the persistence half of the standard subscriber pair. Lifecycle
    hooks are no-ops so the subscriber can ride the shutdown fan-out cleanly.
    """

    def __init__(self) -> None:
        self.messages: list[Message] = []

    def set_restoring(self, team_id: uuid.UUID, restoring: bool) -> None:  # noqa: FBT001
        pass

    def on_stop_request(self, team_id: uuid.UUID) -> None:
        pass

    def on_stop(self, team_id: uuid.UUID) -> None:
        pass

    def on_message(self, msg: Message) -> None:
        self.messages.append(msg)


class _StreamLikeSubscriber:
    """Live-stream-sink fake: records every message handed to ``on_message``.

    Models the streaming half of the standard subscriber pair, distinct from
    the persistence sink so a single ``emitMessage`` fan-out to BOTH can be
    asserted.
    """

    def __init__(self) -> None:
        self.messages: list[Message] = []

    def set_restoring(self, team_id: uuid.UUID, restoring: bool) -> None:  # noqa: FBT001
        pass

    def on_stop_request(self, team_id: uuid.UUID) -> None:
        pass

    def on_stop(self, team_id: uuid.UUID) -> None:
        pass

    def on_message(self, msg: Message) -> None:
        self.messages.append(msg)


class TestEmitMessage:
    """Story 22.1 — `emitMessage` publishes a pre-formed message to subscribers.

    Behavioural invariants under test (ACs #1–#3, #5):

    - Both a persistence-like and a stream-like subscriber receive the emitted
      message via ``on_message`` in a single fan-out.
    - ``team_id`` is stamped from the orchestrator (even when the incoming
      message carries a different/unset ``team_id``), and the orchestrator is set
      as the sender (``Message.init``).
    - The message is appended to ``self.messages`` (the team's record), like the
      other emission paths; it is NOT routed to any agent for processing.
    """

    def test_emit_fans_out_to_both_sinks_and_stamps_team_id(self) -> None:
        """AC #1, #3, #5: both sinks receive the emitted message; team_id stamped."""
        system = ActorSystem()
        team_id = uuid.uuid4()
        orch_addr = system.createActor(
            Orchestrator,
            config=BaseConfig(name="orchestrator", role="Orchestrator"),
            team_id=team_id,
        )
        orch_proxy = system.proxy_ask(orch_addr, Orchestrator)

        persistence = _PersistenceLikeSubscriber()
        stream = _StreamLikeSubscriber()
        orch_proxy.subscribe(persistence)
        orch_proxy.subscribe(stream)

        msg = UserMessage(content="injected notification")
        orch_proxy.emitMessage(msg)

        # AC #3: single fan-out reaches BOTH sinks exactly once.
        assert len(persistence.messages) == 1
        assert len(stream.messages) == 1
        # AC #1: both sinks receive the emitted message. emitMessage now sets the
        # sender address (Message.init), so subscribers get a snapshot copy whose
        # content is preserved (identity is no longer expected).
        assert persistence.messages[0].content == "injected notification"
        assert stream.messages[0].content == "injected notification"
        # AC #1: team_id stamped from the orchestrator.
        assert persistence.messages[0].team_id == team_id
        assert stream.messages[0].team_id == team_id

        system.shutdown()

    def test_emit_appends_to_history(self) -> None:
        """AC #2: emitMessage appends the message to self.messages (team record)."""
        system = ActorSystem()
        orch_addr = system.createActor(
            Orchestrator,
            config=BaseConfig(name="orchestrator", role="Orchestrator"),
        )
        orch_proxy = system.proxy_ask(orch_addr, Orchestrator)

        sub = _PersistenceLikeSubscriber()
        orch_proxy.subscribe(sub)

        before = len(orch_proxy.get_messages())
        orch_proxy.emitMessage(UserMessage(content="added to history"))
        after = len(orch_proxy.get_messages())

        # Subscriber saw it AND it is now part of the team's message record.
        assert len(sub.messages) == 1
        assert after == before + 1

        system.shutdown()

    def test_emit_stamps_team_id_over_a_different_incoming_value(self) -> None:
        """AC #1: emitMessage overwrites a foreign/unset incoming team_id."""
        system = ActorSystem()
        orch_team_id = uuid.uuid4()
        orch_addr = system.createActor(
            Orchestrator,
            config=BaseConfig(name="orchestrator", role="Orchestrator"),
            team_id=orch_team_id,
        )
        orch_proxy = system.proxy_ask(orch_addr, Orchestrator)

        sub = _StreamLikeSubscriber()
        orch_proxy.subscribe(sub)

        # Incoming message carries a DIFFERENT team_id.
        foreign_team_id = uuid.uuid4()
        msg = UserMessage(content="stamp me")
        msg.team_id = foreign_team_id
        assert foreign_team_id != orch_team_id

        orch_proxy.emitMessage(msg)

        assert len(sub.messages) == 1
        assert sub.messages[0].team_id == orch_team_id

        system.shutdown()


# ---------------------------------------------------------------------------
# Test event types for event_class filtering
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CostEvent:
    """Test event type for cost tracking."""

    amount: float


@dataclass(frozen=True)
class UsageEvent:
    """Test event type for usage tracking."""

    tokens: int


class TestEventMessagePersistence:
    """Tests for EventMessage persistence (AC #1, #2) — Task 3."""

    def test_receive_event_message_appends_to_messages(self) -> None:
        """receiveMsg_EventMessage appends to self.messages (AC #1)."""
        system = ActorSystem()
        orch_addr = system.createActor(Orchestrator, restoring=True)
        proxy = system.proxy_ask(orch_addr, Orchestrator)

        msg = EventMessage(event=CostEvent(amount=1.5))
        msg.init(orch_addr, None)

        proxy.receiveMsg_EventMessage(msg, orch_addr)

        assert msg in proxy.messages
        system.shutdown()

    def test_event_message_appears_in_get_messages(self) -> None:
        """EventMessage appears in get_messages() (AC #2)."""
        system = ActorSystem()
        orch_addr = system.createActor(
            Orchestrator,
            config=BaseConfig(name="orchestrator", role="Orchestrator"),
        )
        proxy = system.proxy_ask(orch_addr, Orchestrator)

        msg = EventMessage(event=CostEvent(amount=2.0))
        msg.init(orch_addr, None)
        proxy.receiveMsg_EventMessage(msg, orch_addr)

        all_messages = proxy.get_messages()
        event_messages = [m for m in all_messages if isinstance(m, EventMessage)]
        assert len(event_messages) == 1
        assert event_messages[0] is msg

        system.shutdown()

    def test_subscribers_still_notified_after_persistence(self) -> None:
        """Subscribers receive on_message after EventMessage is persisted (AC #1)."""
        system = ActorSystem()
        orch_addr = system.createActor(Orchestrator, restoring=True)
        proxy = system.proxy_ask(orch_addr, Orchestrator)

        sub = RecordingSubscriber()
        proxy.subscribe(sub)

        msg = EventMessage(event=CostEvent(amount=3.0))
        msg.init(orch_addr, None)
        proxy.receiveMsg_EventMessage(msg, orch_addr)

        # Subscriber was notified
        assert len(sub.messages) == 1
        # After _snapshot_for_subscribers, the subscriber may receive a copy
        # (with ActorAddressProxy sender) so we compare by message id, not identity
        assert sub.messages[0].id == msg.id

        # AND message was persisted
        assert msg in proxy.messages

        system.shutdown()


class TestGetEvents:
    """Tests for get_events() query method (AC #3–#7) — Task 4."""

    def test_get_events_no_filter_returns_all(self) -> None:
        """get_events() with no args returns all EventMessages (AC #3)."""
        system = ActorSystem()
        orch_addr = system.createActor(Orchestrator, restoring=True)
        proxy = system.proxy_ask(orch_addr, Orchestrator)

        msg1 = EventMessage(event=CostEvent(amount=1.0))
        msg1.init(orch_addr, None)
        msg2 = EventMessage(event=UsageEvent(tokens=100))
        msg2.init(orch_addr, None)

        proxy.receiveMsg_EventMessage(msg1, orch_addr)
        proxy.receiveMsg_EventMessage(msg2, orch_addr)

        # Also add a non-event message to verify filtering
        proxy.restore_message(UserMessage(content="not an event"))

        events = proxy.get_events()
        assert len(events) == 2
        assert msg1 in events
        assert msg2 in events

        system.shutdown()

    def test_get_events_agent_id_filter(self) -> None:
        """get_events(agent_id=...) filters by sender agent_id (AC #4)."""
        system = ActorSystem()

        # Create two orchestrators to get different addresses for filtering
        orch_addr = system.createActor(Orchestrator, restoring=True)
        agent_addr = system.createActor(
            Orchestrator,
            config=BaseConfig(name="worker-a", role="Worker"),
            restoring=True,
        )
        proxy = system.proxy_ask(orch_addr, Orchestrator)

        msg_orch = EventMessage(event=CostEvent(amount=1.0))
        msg_orch.init(orch_addr, None)

        msg_agent = EventMessage(event=CostEvent(amount=2.0))
        msg_agent.init(agent_addr, None)

        proxy.receiveMsg_EventMessage(msg_orch, orch_addr)
        proxy.receiveMsg_EventMessage(msg_agent, agent_addr)

        # Filter by agent_addr's agent_id
        filtered = proxy.get_events(agent_id=str(agent_addr.agent_id))
        assert len(filtered) == 1
        assert filtered[0] is msg_agent

        system.shutdown()

    def test_get_events_event_class_filter(self) -> None:
        """get_events(event_class=...) filters by event payload type (AC #5)."""
        system = ActorSystem()
        orch_addr = system.createActor(Orchestrator, restoring=True)
        proxy = system.proxy_ask(orch_addr, Orchestrator)

        msg_cost = EventMessage(event=CostEvent(amount=1.0))
        msg_cost.init(orch_addr, None)
        msg_usage = EventMessage(event=UsageEvent(tokens=100))
        msg_usage.init(orch_addr, None)

        proxy.receiveMsg_EventMessage(msg_cost, orch_addr)
        proxy.receiveMsg_EventMessage(msg_usage, orch_addr)

        filtered = proxy.get_events(event_class=CostEvent)
        assert len(filtered) == 1
        assert filtered[0] is msg_cost

        system.shutdown()

    def test_get_events_combined_filters(self) -> None:
        """get_events(agent_id=..., event_class=...) applies both filters (AC #6)."""
        system = ActorSystem()
        orch_addr = system.createActor(Orchestrator, restoring=True)
        agent_addr = system.createActor(
            Orchestrator,
            config=BaseConfig(name="worker-b", role="Worker"),
            restoring=True,
        )
        proxy = system.proxy_ask(orch_addr, Orchestrator)

        # Four messages: 2 senders x 2 event types
        msg1 = EventMessage(event=CostEvent(amount=1.0))
        msg1.init(orch_addr, None)
        msg2 = EventMessage(event=UsageEvent(tokens=50))
        msg2.init(orch_addr, None)
        msg3 = EventMessage(event=CostEvent(amount=2.0))
        msg3.init(agent_addr, None)
        msg4 = EventMessage(event=UsageEvent(tokens=99))
        msg4.init(agent_addr, None)

        for m in (msg1, msg2, msg3, msg4):
            proxy.receiveMsg_EventMessage(m, orch_addr)

        # Only agent_addr + CostEvent → msg3
        filtered = proxy.get_events(
            agent_id=str(agent_addr.agent_id),
            event_class=CostEvent,
        )
        assert len(filtered) == 1
        assert filtered[0] is msg3

        system.shutdown()

    def test_get_events_empty_result(self) -> None:
        """get_events() returns empty list when no EventMessages match (AC #7)."""
        system = ActorSystem()
        orch_addr = system.createActor(Orchestrator, restoring=True)
        proxy = system.proxy_ask(orch_addr, Orchestrator)

        # Add a non-matching event
        msg = EventMessage(event=CostEvent(amount=1.0))
        msg.init(orch_addr, None)
        proxy.receiveMsg_EventMessage(msg, orch_addr)

        # Filter by a class that doesn't match
        filtered = proxy.get_events(event_class=UsageEvent)
        assert filtered == []

        system.shutdown()

    def test_get_events_no_events_at_all(self) -> None:
        """get_events() returns empty list when no EventMessages exist."""
        system = ActorSystem()
        orch_addr = system.createActor(Orchestrator, restoring=True)
        proxy = system.proxy_ask(orch_addr, Orchestrator)

        # Only non-event messages
        proxy.restore_message(UserMessage(content="not an event"))

        filtered = proxy.get_events()
        assert filtered == []

        system.shutdown()


class TestGetChildrenOrCreate:
    """Tests for Orchestrator.getChildrenOrCreate singleton lookup."""

    def test_creates_child_when_absent(self) -> None:
        """getChildrenOrCreate creates a new child when none exists with the name."""
        system = ActorSystem()
        orch_addr = system.createActor(
            Orchestrator,
            config=BaseConfig(name="orchestrator", role="Orchestrator"),
        )
        proxy = system.proxy_ask(orch_addr, Orchestrator)

        child_addr = proxy.getChildrenOrCreate(
            SimpleAgent, config=BaseConfig(name="#Foo", role="Worker")
        )

        assert child_addr.is_alive()
        assert child_addr.name == "#Foo"

        system.shutdown()

    def test_returns_existing_live_child(self) -> None:
        """getChildrenOrCreate returns existing live child instead of creating a duplicate."""
        system = ActorSystem()
        orch_addr = system.createActor(
            Orchestrator,
            config=BaseConfig(name="orchestrator", role="Orchestrator"),
        )
        proxy = system.proxy_ask(orch_addr, Orchestrator)

        first = proxy.getChildrenOrCreate(
            SimpleAgent, config=BaseConfig(name="#Foo", role="Worker")
        )
        second = proxy.getChildrenOrCreate(
            SimpleAgent, config=BaseConfig(name="#Foo", role="Worker")
        )

        assert first.agent_id == second.agent_id
        assert first.name == second.name

        system.shutdown()

    def test_dead_child_is_not_returned(self) -> None:
        """getChildrenOrCreate creates a new actor when the existing child is dead."""
        system = ActorSystem()
        orch_addr = system.createActor(
            Orchestrator,
            config=BaseConfig(name="orchestrator", role="Orchestrator"),
        )
        proxy = system.proxy_ask(orch_addr, Orchestrator)

        first = proxy.getChildrenOrCreate(
            SimpleAgent, config=BaseConfig(name="#Foo", role="Worker")
        )
        first_id = first.agent_id

        # Stop the child
        system.proxy_ask(first, Akgent).stop()

        # Now request the same name again
        second = proxy.getChildrenOrCreate(
            SimpleAgent, config=BaseConfig(name="#Foo", role="Worker")
        )

        assert second.is_alive()
        assert second.agent_id != first_id

        system.shutdown()

    def test_back_to_back_idempotency(self) -> None:
        """Two sequential calls with same name return same ActorAddress and only one actor."""
        system = ActorSystem()
        orch_addr = system.createActor(
            Orchestrator,
            config=BaseConfig(name="orchestrator", role="Orchestrator"),
        )
        proxy = system.proxy_ask(orch_addr, Orchestrator)

        addr1 = proxy.getChildrenOrCreate(
            SimpleAgent, config=BaseConfig(name="#Bar", role="Worker")
        )
        addr2 = proxy.getChildrenOrCreate(
            SimpleAgent, config=BaseConfig(name="#Bar", role="Worker")
        )

        # Same actor returned
        assert addr1.agent_id == addr2.agent_id

        # Verify only one child with that name by checking team via messages
        # (both calls should have resulted in only one StartMessage for #Bar)
        messages = proxy.get_messages()
        bar_starts = [
            m for m in messages if isinstance(m, StartMessage) and m.config.name == "#Bar"
        ]
        assert len(bar_starts) == 1

        system.shutdown()

    def test_different_names_create_different_children(self) -> None:
        """getChildrenOrCreate creates separate children for different names."""
        system = ActorSystem()
        orch_addr = system.createActor(
            Orchestrator,
            config=BaseConfig(name="orchestrator", role="Orchestrator"),
        )
        proxy = system.proxy_ask(orch_addr, Orchestrator)

        foo = proxy.getChildrenOrCreate(SimpleAgent, config=BaseConfig(name="#Foo", role="Worker"))
        bar = proxy.getChildrenOrCreate(SimpleAgent, config=BaseConfig(name="#Bar", role="Worker"))

        assert foo.agent_id != bar.agent_id
        assert foo.name == "#Foo"
        assert bar.name == "#Bar"

        system.shutdown()


class TestSnapshotForSubscribers:
    """Tests for _snapshot_for_subscribers address serialization (AC: 1-4)."""

    def test_subscriber_receives_actor_address_proxy_not_impl(self) -> None:
        """Subscriber's captured message has sender as ActorAddressProxy (AC: 3, 4)."""
        system = ActorSystem()
        orch_addr = system.createActor(
            Orchestrator,
            config=BaseConfig(name="orchestrator", role="Orchestrator"),
        )

        # Create a child agent so we get an ActorAddressImpl sender
        child_addr = system.createActor(
            SimpleAgent,
            config=BaseConfig(name="test-agent", role="TestAgent"),
        )

        orch_proxy = system.proxy_ask(orch_addr, Orchestrator)

        sub = RecordingSubscriber()
        orch_proxy.subscribe(sub)

        # The child_addr is an ActorAddressImpl (live Pykka actor)
        assert isinstance(child_addr, ActorAddressImpl)

        # Create a StartMessage with ActorAddressImpl sender
        msg = StartMessage(config=BaseConfig(name="test-agent", role="TestAgent"))
        msg.init(child_addr, child_addr.team_id)

        # Dispatch through receiveMsg_StartMessage
        orch_proxy.receiveMsg_StartMessage(msg, child_addr)

        # Subscriber should have received one message (excluding orchestrator's own)
        # Find the message dispatched for our test agent
        sub_msgs = [
            m for m in sub.messages if isinstance(m, StartMessage) and m.config.name == "test-agent"
        ]
        assert len(sub_msgs) == 1

        dispatched = sub_msgs[0]
        # AC 4: sender is ActorAddressProxy, not ActorAddressImpl
        assert isinstance(dispatched.sender, ActorAddressProxy)
        assert not isinstance(dispatched.sender, ActorAddressImpl)

        # AC 4: sender attributes match original values
        assert dispatched.sender.name == "test-agent"
        assert dispatched.sender.role == "TestAgent"
        assert dispatched.sender.team_id == child_addr.team_id

        system.shutdown()

    def test_orchestrator_messages_retain_actor_address_impl(self) -> None:
        """Orchestrator's internal self.messages retains ActorAddressImpl (AC: 2, 4)."""
        system = ActorSystem()
        orch_addr = system.createActor(
            Orchestrator,
            config=BaseConfig(name="orchestrator", role="Orchestrator"),
        )

        child_addr = system.createActor(
            SimpleAgent,
            config=BaseConfig(name="test-agent", role="TestAgent"),
        )

        orch_proxy = system.proxy_ask(orch_addr, Orchestrator)

        sub = RecordingSubscriber()
        orch_proxy.subscribe(sub)

        assert isinstance(child_addr, ActorAddressImpl)

        msg = StartMessage(config=BaseConfig(name="test-agent", role="TestAgent"))
        msg.init(child_addr, child_addr.team_id)

        orch_proxy.receiveMsg_StartMessage(msg, child_addr)

        # AC 4: Orchestrator's internal messages list has ActorAddressImpl
        internal_msgs = orch_proxy.get_messages()
        agent_starts = [
            m
            for m in internal_msgs
            if isinstance(m, StartMessage) and m.config.name == "test-agent"
        ]
        assert len(agent_starts) == 1
        assert isinstance(agent_starts[0].sender, ActorAddressImpl)

        system.shutdown()

    def test_snapshot_no_copy_when_no_impl(self) -> None:
        """No copy when message has no ActorAddressImpl fields (AC: 1)."""
        system = ActorSystem()
        orch_addr = system.createActor(Orchestrator, restoring=True)
        proxy = system.proxy_ask(orch_addr, Orchestrator)

        sub = RecordingSubscriber()
        proxy.subscribe(sub)

        # UserMessage has no sender/recipient set (both None)
        msg = UserMessage(content="plain message")
        proxy.restore_message(msg)

        assert len(sub.messages) == 1
        # The original message is returned unchanged (no copy)
        assert sub.messages[0] is msg

        system.shutdown()

    def test_snapshot_replaces_recipient_and_nested_message(self) -> None:
        """Snapshot serializes SentMessage.recipient and recurses into .message."""
        system = ActorSystem()
        orch_addr = system.createActor(
            Orchestrator,
            config=BaseConfig(name="orchestrator", role="Orchestrator"),
        )

        sender_addr = system.createActor(
            SimpleAgent,
            config=BaseConfig(name="sender-agent", role="Sender"),
        )
        recipient_addr = system.createActor(
            SimpleAgent,
            config=BaseConfig(name="recipient-agent", role="Recipient"),
        )

        orch_proxy = system.proxy_ask(orch_addr, Orchestrator)

        sub = RecordingSubscriber()
        orch_proxy.subscribe(sub)

        assert isinstance(sender_addr, ActorAddressImpl)
        assert isinstance(recipient_addr, ActorAddressImpl)

        # Build an inner message with ActorAddressImpl sender
        inner = UserMessage(content="hello")
        inner.init(sender_addr, sender_addr.team_id)

        # SentMessage has a top-level `recipient` and a nested `message`
        msg = SentMessage(recipient=recipient_addr, message=inner)
        msg.init(sender_addr, sender_addr.team_id)

        orch_proxy.receiveMsg_SentMessage(msg, sender_addr)

        # Find the dispatched SentMessage
        sent_msgs = [m for m in sub.messages if isinstance(m, SentMessage)]
        assert len(sent_msgs) == 1

        dispatched = sent_msgs[0]
        # Top-level addresses serialized
        assert isinstance(dispatched.sender, ActorAddressProxy)
        assert isinstance(dispatched.recipient, ActorAddressProxy)
        assert dispatched.sender.name == "sender-agent"
        assert dispatched.recipient.name == "recipient-agent"

        # Nested message addresses also serialized (recursive)
        assert isinstance(dispatched.message.sender, ActorAddressProxy)
        assert dispatched.message.sender.name == "sender-agent"

        system.shutdown()

    def test_snapshot_replaces_subclass_address_fields(self) -> None:
        """Snapshot replaces ActorAddressImpl on subclass-specific fields like parent."""
        system = ActorSystem()
        orch_addr = system.createActor(
            Orchestrator,
            config=BaseConfig(name="orchestrator", role="Orchestrator"),
        )

        child_addr = system.createActor(
            SimpleAgent,
            config=BaseConfig(name="child-agent", role="Child"),
        )
        parent_addr = system.createActor(
            SimpleAgent,
            config=BaseConfig(name="parent-agent", role="Parent"),
        )

        orch_proxy = system.proxy_ask(orch_addr, Orchestrator)

        sub = RecordingSubscriber()
        orch_proxy.subscribe(sub)

        assert isinstance(child_addr, ActorAddressImpl)
        assert isinstance(parent_addr, ActorAddressImpl)

        # StartMessage has a `parent` field typed as ActorAddress | None
        msg = StartMessage(
            config=BaseConfig(name="child-agent", role="Child"),
            parent=parent_addr,
        )
        msg.init(child_addr, child_addr.team_id)

        orch_proxy.receiveMsg_StartMessage(msg, child_addr)

        # Find the dispatched StartMessage for child-agent
        start_msgs = [
            m
            for m in sub.messages
            if isinstance(m, StartMessage) and m.config.name == "child-agent"
        ]
        assert len(start_msgs) == 1

        dispatched = start_msgs[0]
        # sender and parent should both be serialized
        assert isinstance(dispatched.sender, ActorAddressProxy)
        assert isinstance(dispatched.parent, ActorAddressProxy)
        assert dispatched.parent.name == "parent-agent"
        assert dispatched.parent.role == "Parent"

        # Orchestrator's internal copy retains live references
        internal_msgs = orch_proxy.get_messages()
        internal_starts = [
            m
            for m in internal_msgs
            if isinstance(m, StartMessage) and m.config.name == "child-agent"
        ]
        assert len(internal_starts) == 1
        assert isinstance(internal_starts[0].parent, ActorAddressImpl)

        system.shutdown()


# ---------------------------------------------------------------------------
# Story 17.1 — team_id propagation through _notify_subscribers_lifecycle
# ---------------------------------------------------------------------------


class _LifecycleRaisingSubscriber:
    """Subscriber whose lifecycle hooks raise to exercise fault-isolation.

    on_message is a no-op so we can place this subscriber in the dispatch
    chain without interfering with message-dispatch tests.
    """

    def set_restoring(self, team_id: uuid.UUID, restoring: bool) -> Never:  # noqa: FBT001
        raise RuntimeError("set_restoring boom")

    def on_stop_request(self, team_id: uuid.UUID) -> Never:
        raise RuntimeError("on_stop_request boom")

    def on_stop(self, team_id: uuid.UUID) -> Never:
        raise RuntimeError("on_stop boom")

    def on_message(self, msg: Message) -> None:
        pass


class _NotifyExposingOrchestrator(Orchestrator):
    """Orchestrator subclass that exposes the notify helpers to Pykka proxies.

    Pykka's proxy filters out underscore-prefixed methods, so tests that need
    to drive ``_notify_subscribers_lifecycle`` directly (lifecycle dispatch in
    Story 17.1 — there is no in-source caller for ``set_restoring``) cannot
    invoke it through ``system.proxy_ask(...)``. This thin wrapper exposes
    each helper under a public name without changing production semantics.
    """

    def notify_subscribers_lifecycle(
        self,
        event_method: str,
        team_id: uuid.UUID,
        **kwargs: Any,
    ) -> None:
        """Public façade over ``_notify_subscribers_lifecycle`` for proxy-driven tests."""
        self._notify_subscribers_lifecycle(event_method, team_id, **kwargs)

    def notify_subscribers_message(self, event_method: str, message: Message) -> None:
        """Public façade over ``_notify_subscribers_message`` for proxy-driven tests."""
        self._notify_subscribers_message(event_method, message)


class TestNotifySubscribersTeamIdPropagation:
    """Story 17.1 — `_notify_subscribers_lifecycle` propagates team_id to lifecycle hooks."""

    def test_set_restoring_passes_team_id_to_subscribers(self) -> None:
        """AC #2: lifecycle dispatch forwards team_id + restoring=True."""
        system = ActorSystem()
        orch_addr = system.createActor(
            _NotifyExposingOrchestrator,
            config=BaseConfig(name="orchestrator", role="Orchestrator"),
        )
        orch_proxy = system.proxy_ask(orch_addr, _NotifyExposingOrchestrator)

        sub = RecordingSubscriber()
        orch_proxy.subscribe(sub)

        orch_team_id = orch_addr.team_id
        orch_proxy.notify_subscribers_lifecycle("set_restoring", orch_team_id, restoring=True)

        assert sub.restoring_calls == [(orch_team_id, True)]
        assert sub.restoring is True

        system.shutdown()

    def test_on_stop_request_passes_team_id_to_subscribers(self) -> None:
        """AC #3: `_notify_subscribers_lifecycle("on_stop_request", team_id)` forwards team_id."""
        system = ActorSystem()
        orch_addr = system.createActor(
            _NotifyExposingOrchestrator,
            config=BaseConfig(name="orchestrator", role="Orchestrator"),
        )
        orch_proxy = system.proxy_ask(orch_addr, _NotifyExposingOrchestrator)

        sub = RecordingSubscriber()
        orch_proxy.subscribe(sub)

        orch_team_id = orch_addr.team_id
        orch_proxy.notify_subscribers_lifecycle("on_stop_request", orch_team_id)

        assert sub.stop_request_team_ids == [orch_team_id]

        system.shutdown()

    def test_on_stop_passes_team_id_to_subscribers(self) -> None:
        """AC #1: `_notify_subscribers_lifecycle("on_stop", team_id)` forwards team_id.

        Exercises the call directly via `_notify_subscribers_lifecycle` (not
        the full Pykka stop() path) so the test isolates the signature change
        in 17.1 from the body-reordering 17.2 will deliver.
        """
        system = ActorSystem()
        orch_addr = system.createActor(
            _NotifyExposingOrchestrator,
            config=BaseConfig(name="orchestrator", role="Orchestrator"),
        )
        orch_proxy = system.proxy_ask(orch_addr, _NotifyExposingOrchestrator)

        sub = RecordingSubscriber()
        orch_proxy.subscribe(sub)

        orch_team_id = orch_addr.team_id
        orch_proxy.notify_subscribers_lifecycle("on_stop", orch_team_id)

        assert sub.stop_team_ids == [orch_team_id]
        assert sub.stopped is True

        system.shutdown()

    def test_timeout_handler_passes_orchestrator_team_id(self) -> None:
        """AC #3: `_timeout_handler` call site supplies the orchestrator's own team_id."""
        config = BaseConfig(name="test-orchestrator", role="Orchestrator")
        team_id = uuid.uuid4()
        orch_ref = Orchestrator.start(config=config, team_id=team_id)
        orch_proxy = orch_ref.proxy()

        sub = RecordingSubscriber()
        orch_proxy.subscribe(sub).get()

        # Fire the handler directly (same callback threading.Timer uses)
        timer = orch_proxy.get_timer().get()
        timer.timeout_callback()

        assert sub.stop_request_team_ids == [team_id]

        orch_ref.stop()


class TestLifecycleFanOutFaultIsolation:
    """AC #4: per-subscriber try/except keeps the dispatch loop alive across hooks."""

    def test_set_restoring_fault_isolation(self) -> None:
        """A middle subscriber raising in set_restoring does not block its neighbours."""
        system = ActorSystem()
        orch_addr = system.createActor(
            _NotifyExposingOrchestrator,
            config=BaseConfig(name="orchestrator", role="Orchestrator"),
        )
        orch_proxy = system.proxy_ask(orch_addr, _NotifyExposingOrchestrator)

        first = RecordingSubscriber()
        middle = _LifecycleRaisingSubscriber()
        third = RecordingSubscriber()

        orch_proxy.subscribe(first)
        orch_proxy.subscribe(middle)
        orch_proxy.subscribe(third)

        orch_team_id = orch_addr.team_id
        orch_proxy.notify_subscribers_lifecycle("set_restoring", orch_team_id, restoring=False)

        assert first.restoring_calls == [(orch_team_id, False)]
        assert third.restoring_calls == [(orch_team_id, False)]

        system.shutdown()

    def test_on_stop_request_fault_isolation(self) -> None:
        """A middle subscriber raising in on_stop_request does not block its neighbours."""
        system = ActorSystem()
        orch_addr = system.createActor(
            _NotifyExposingOrchestrator,
            config=BaseConfig(name="orchestrator", role="Orchestrator"),
        )
        orch_proxy = system.proxy_ask(orch_addr, _NotifyExposingOrchestrator)

        first = RecordingSubscriber()
        middle = _LifecycleRaisingSubscriber()
        third = RecordingSubscriber()

        orch_proxy.subscribe(first)
        orch_proxy.subscribe(middle)
        orch_proxy.subscribe(third)

        orch_team_id = orch_addr.team_id
        orch_proxy.notify_subscribers_lifecycle("on_stop_request", orch_team_id)

        assert first.stop_request_team_ids == [orch_team_id]
        assert third.stop_request_team_ids == [orch_team_id]

        system.shutdown()

    def test_on_stop_fault_isolation(self) -> None:
        """A middle subscriber raising in on_stop does not block its neighbours."""
        system = ActorSystem()
        orch_addr = system.createActor(
            _NotifyExposingOrchestrator,
            config=BaseConfig(name="orchestrator", role="Orchestrator"),
        )
        orch_proxy = system.proxy_ask(orch_addr, _NotifyExposingOrchestrator)

        first = RecordingSubscriber()
        middle = _LifecycleRaisingSubscriber()
        third = RecordingSubscriber()

        orch_proxy.subscribe(first)
        orch_proxy.subscribe(middle)
        orch_proxy.subscribe(third)

        orch_team_id = orch_addr.team_id
        orch_proxy.notify_subscribers_lifecycle("on_stop", orch_team_id)

        assert first.stop_team_ids == [orch_team_id]
        assert third.stop_team_ids == [orch_team_id]

        system.shutdown()


class TestOnMessageDispatchUnchanged:
    """AC #5: `_notify_subscribers_message("on_message", message)` still dispatches as before."""

    def test_on_message_does_not_receive_team_id(self) -> None:
        """Message dispatch passes the snapshotted message and NO team_id kwarg."""
        system = ActorSystem()
        orch_addr = system.createActor(
            Orchestrator,
            config=BaseConfig(name="orchestrator", role="Orchestrator"),
        )
        orch_proxy = system.proxy_ask(orch_addr, Orchestrator)

        sub = RecordingSubscriber()
        orch_proxy.subscribe(sub)

        msg = UserMessage(content="hello")
        orch_proxy.restore_message(msg)

        assert len(sub.messages) == 1
        # team_id-bearing lifecycle counters remain untouched by on_message dispatch
        assert sub.stop_team_ids == []
        assert sub.stop_request_team_ids == []
        assert sub.restoring_calls == []

        system.shutdown()


# ---------------------------------------------------------------------------
# Story 17.2 — Orchestrator.on_stop fan-out before clear (ADR-011 §3)
# ---------------------------------------------------------------------------


class _LenAtStopRecorder:
    """Subscriber that snapshots ``len(subscribers)`` at the moment ``on_stop`` fires.

    Used to verify AC #1 — the subscriber is still attached to the orchestrator
    when its ``on_stop`` body executes (i.e. fan-out runs BEFORE
    ``self.subscribers.clear()``).
    """

    def __init__(self) -> None:
        self.subscribers_list_ref: list[EventSubscriber] | None = None
        self.calls: list[tuple[uuid.UUID, int]] = []

    def set_restoring(self, team_id: uuid.UUID, restoring: bool) -> None:  # noqa: FBT001
        pass

    def on_stop_request(self, team_id: uuid.UUID) -> None:
        pass

    def on_stop(self, team_id: uuid.UUID) -> None:
        # Capture len() at the moment fan-out fires. AC #1 requires this to be
        # ≥ 1 (subscriber is still attached when its on_stop runs).
        snapshot = len(self.subscribers_list_ref) if self.subscribers_list_ref is not None else -1
        self.calls.append((team_id, snapshot))

    def on_message(self, msg: Message) -> None:
        pass


class _OnStopRaisingSubscriber:
    """Subscriber whose ``on_stop`` raises; other hooks are no-ops.

    Used to verify AC #4 — a raising subscriber does not block the post-stop
    invariants (``super().on_stop()`` still runs, ``subscribers.clear()`` still
    runs, ``Stopped !`` log line still runs).
    """

    def set_restoring(self, team_id: uuid.UUID, restoring: bool) -> None:  # noqa: FBT001
        pass

    def on_stop_request(self, team_id: uuid.UUID) -> None:
        pass

    def on_stop(self, team_id: uuid.UUID) -> Never:
        raise RuntimeError("boom")

    def on_message(self, msg: Message) -> None:
        pass


class TestOnStopFanOutThenClear:
    """Story 17.2 — Orchestrator.on_stop fans out THEN clears subscribers.

    Behavioural invariants under test (ACs #1–#5):

    - Fan-out fires while the subscriber is still attached (snapshot
      ``len(subscribers) >= 1`` from inside ``on_stop``).
    - ``team_id`` passed to ``on_stop`` is the orchestrator's own ``team_id``.
    - ``subscribers`` list is empty after ``on_stop`` returns.
    - A subscriber raising in ``on_stop`` does not block ``clear()`` running.
    - Three-subscriber fault isolation contract from 17.1 is preserved (first
      and third still receive their call when the middle one raises).
    """

    def test_on_stop_fan_out_before_clear_then_list_empty(self) -> None:
        """AC #1, #2, #3: fan-out runs while attached; subscribers list cleared after stop."""
        system = ActorSystem()
        orch_addr = system.createActor(
            Orchestrator,
            config=BaseConfig(name="orchestrator", role="Orchestrator"),
        )
        orch_proxy = system.proxy_ask(orch_addr, Orchestrator)

        recorder = _LenAtStopRecorder()
        orch_proxy.subscribe(recorder)

        # Capture the live subscribers-list reference BEFORE stop so we can
        # observe the post-stop ``clear()`` via the same Python list object.
        subscribers_list = orch_proxy.subscribers
        recorder.subscribers_list_ref = subscribers_list
        assert recorder in subscribers_list

        orch_team_id = orch_addr.team_id

        # Trigger the real on_stop via the system shutdown path.
        system.shutdown()

        # AC #1: fan-out fired exactly once, with the orchestrator's team_id,
        # and the subscriber was still attached at the moment of the call.
        assert len(recorder.calls) == 1
        recorded_team_id, len_at_fan_out = recorder.calls[0]
        assert recorded_team_id == orch_team_id
        assert len_at_fan_out >= 1

        # AC #2 + AC #3: subscribers list cleared after on_stop returned.
        assert subscribers_list == []

    def test_on_stop_raising_subscriber_does_not_block_clear(self) -> None:
        """AC #4: clear() still runs and recorder still fires when a sibling raises."""
        system = ActorSystem()
        orch_addr = system.createActor(
            Orchestrator,
            config=BaseConfig(name="orchestrator", role="Orchestrator"),
        )
        orch_proxy = system.proxy_ask(orch_addr, Orchestrator)

        recorder = _LenAtStopRecorder()
        failing = _OnStopRaisingSubscriber()
        orch_proxy.subscribe(recorder)
        orch_proxy.subscribe(failing)

        subscribers_list = orch_proxy.subscribers
        recorder.subscribers_list_ref = subscribers_list

        # Stop the orchestrator. The failing subscriber raises inside
        # ``_notify_subscribers_lifecycle`` but its per-subscriber try/except
        # (landed in 17.1) swallows it — fan-out completes for the recorder,
        # ``super().on_stop()`` runs, and ``clear()`` runs unconditionally.
        system.shutdown()  # MUST NOT raise

        # Recorder still received its call exactly once.
        assert len(recorder.calls) == 1

        # AC #4: ``clear()`` ran even though one subscriber raised.
        assert subscribers_list == []

    def test_on_stop_three_subscribers_middle_raises(self) -> None:
        """AC #5: first and third still receive on_stop when the middle one raises."""
        system = ActorSystem()
        orch_addr = system.createActor(
            Orchestrator,
            config=BaseConfig(name="orchestrator", role="Orchestrator"),
        )
        orch_proxy = system.proxy_ask(orch_addr, Orchestrator)

        first = _LenAtStopRecorder()
        middle = _OnStopRaisingSubscriber()
        third = _LenAtStopRecorder()

        orch_proxy.subscribe(first)
        orch_proxy.subscribe(middle)
        orch_proxy.subscribe(third)

        subscribers_list = orch_proxy.subscribers
        first.subscribers_list_ref = subscribers_list
        third.subscribers_list_ref = subscribers_list

        orch_team_id = orch_addr.team_id

        system.shutdown()

        # AC #5: both recorders received their call exactly once with the
        # orchestrator's own team_id (fault-isolation from 17.1 preserved).
        assert len(first.calls) == 1
        assert len(third.calls) == 1
        assert first.calls[0][0] == orch_team_id
        assert third.calls[0][0] == orch_team_id

        # AC #4 redux: clear() still ran post-stop.
        assert subscribers_list == []
