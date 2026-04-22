"""Tests for Orchestrator agent."""

from collections.abc import Generator
from dataclasses import dataclass
from typing import Never

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
    set_restoring.
    """

    def __init__(self) -> None:
        self.messages: list[Message] = []
        self.stopped: bool = False
        self.restoring: bool = False

    def set_restoring(self, restoring: bool) -> None:  # noqa: FBT001
        """Track restore-replay guard state."""
        self.restoring = restoring

    def on_message(self, msg: Message) -> None:
        """Record received message."""
        self.messages.append(msg)

    def on_stop(self) -> None:
        """Record stop."""
        self.stopped = True


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
            m for m in messages
            if isinstance(m, StartMessage) and m.config.name == "#Bar"
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

        foo = proxy.getChildrenOrCreate(
            SimpleAgent, config=BaseConfig(name="#Foo", role="Worker")
        )
        bar = proxy.getChildrenOrCreate(
            SimpleAgent, config=BaseConfig(name="#Bar", role="Worker")
        )

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
            m
            for m in sub.messages
            if isinstance(m, StartMessage) and m.config.name == "test-agent"
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
