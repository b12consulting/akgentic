"""Tests for Akgent base class and message dispatch.

Tests cover:
- Agent initialization and lifecycle
- Message dispatch with receiveMsg_<Type> pattern
- SUPER sentinel fallthrough behavior
- Child actor creation with context propagation
- State management with observer pattern
- Proxy helpers (tell/ask modes)
- Telemetry integration (orchestrator notifications)
"""

import uuid
from typing import Any, cast
from unittest.mock import MagicMock

import pykka
import pytest

from akgentic.core.actor_address import ActorAddress
from akgentic.core.actor_address_impl import ActorAddressImpl
from akgentic.core.agent import Akgent, ProxyWrapper
from akgentic.core.agent_config import BaseConfig
from akgentic.core.agent_state import BaseState
from akgentic.core.messages.message import Message, StopRecursively


class SampleMessage(Message):
    """Test message for dispatch testing."""

    content: str = ""


class DerivedSampleMessage(SampleMessage):
    """Derived test message for MRO testing."""

    extra: str = ""


class SampleAgent(Akgent[BaseConfig, BaseState]):
    """Test agent with message handler."""

    def __init__(self, **kwargs) -> None:
        self.received_messages: list = []
        super().__init__(**kwargs)

    def receiveMsg_SampleMessage(self, msg: SampleMessage, sender: Any):
        """Handle SampleMessage and derived types."""
        self.received_messages.append(msg)
        return msg.content


class SuperSampleAgent(Akgent[BaseConfig, BaseState]):
    """Test agent that uses SUPER sentinel."""

    def __init__(self, **kwargs) -> None:
        self.handled_at_base = False
        super().__init__(**kwargs)

    def receiveMsg_SampleMessage(self, msg: SampleMessage, sender: Any):
        """Decline to handle - return SUPER."""
        return self.SUPER

    def receiveMsg_Message(self, msg: Message, sender: Any) -> str:
        """Base Message handler - catches fallthrough."""
        self.handled_at_base = True
        return "base_handler"


@pytest.fixture
def agent_setup():
    """Create agent with required context."""
    agent_id = uuid.uuid4()
    team_id = uuid.uuid4()
    config = BaseConfig(name="test-agent", role="Tester")
    return agent_id, config, team_id


@pytest.fixture(autouse=True)
def cleanup_actors():
    """Ensure all actors stopped after each test."""
    yield
    try:
        pykka.ActorRegistry.stop_all()
    except Exception:
        pass


class TestAgentInitialization:
    """Tests for agent lifecycle."""

    def test_agent_starts_and_stops(self, agent_setup) -> None:
        """Agent can be started and stopped cleanly."""
        agent_id, config, team_id = agent_setup
        ref = SampleAgent.start(
            agent_id=agent_id,
            config=config,
            team_id=team_id,
        )
        try:
            assert ref.is_alive()
            address = ActorAddressImpl(ref)
            assert address.agent_id == agent_id
        finally:
            ref.stop()

    def test_agent_keyword_arg_initialization(self) -> None:
        """Agent can be initialized with explicit keyword arguments."""
        agent_id = uuid.uuid4()
        team_id = uuid.uuid4()
        user_id = uuid.uuid4()
        config = BaseConfig(name="test-agent", role="Tester")

        ref = SampleAgent.start(
            agent_id=agent_id,
            config=config,
            user_id=user_id,
            user_email="test@example.com",
            team_id=team_id,
            parent=None,
            orchestrator=None,
        )
        try:
            assert ref.is_alive()
            agent_agent_id = ref.proxy().agent_id.get()
            agent_config_name = ref.proxy().config.get().name

            assert agent_agent_id == agent_id
            assert agent_config_name == "test-agent"

            # Verify child creation propagates context correctly
            child_config = BaseConfig(name="child")
            child_address = (
                ref.proxy().createActor(SampleAgent, uuid.uuid4(), child_config).get(timeout=5)
            )
            assert child_address.is_alive()
        finally:
            ref.stop()

    def test_agent_keyword_args_with_defaults(self) -> None:
        """Agent keyword args use defaults when not specified."""
        config = BaseConfig(name="test-agent")

        ref = SampleAgent.start(config=config)
        try:
            assert ref.is_alive()
            agent_agent_id = ref.proxy().agent_id.get()
            agent_config = ref.proxy().config.get()

            # agent_id defaults to uuid4()
            assert agent_agent_id is not None
            assert isinstance(agent_agent_id, uuid.UUID)

            # config is set
            assert agent_config.name == "test-agent"
        finally:
            ref.stop()

    def test_agent_receives_uuid_and_config(self, agent_setup) -> None:
        """Agent initialization extracts args correctly."""
        agent_id, config, team_id = agent_setup
        ref = SampleAgent.start(
            agent_id=agent_id,
            config=config,
            team_id=team_id,
        )
        try:
            # Access via proxy to get agent attributes
            agent_config_name = ref.proxy().config.get().name
            agent_agent_id = ref.proxy().agent_id.get()

            assert agent_config_name == "test-agent"
            assert agent_agent_id == agent_id
        finally:
            ref.stop()

    def test_agent_defaults_name_and_role(self, agent_setup) -> None:
        """Agent sets default name and role if not provided."""
        agent_id, _, team_id = agent_setup
        config = BaseConfig()  # No name or role
        ref = SampleAgent.start(
            agent_id=agent_id,
            config=config,
            team_id=team_id,
        )
        try:
            agent_config_name = ref.proxy().config.get().name
            agent_config_role = ref.proxy().config.get().role

            # Name defaults to actor ref string
            assert agent_config_name is not None
            assert len(agent_config_name) > 0

            # Role defaults to class name
            assert agent_config_role == "SampleAgent"
        finally:
            ref.stop()

    def test_team_id_auto_generated_when_not_provided(self) -> None:
        """Actor started without team_id gets a valid uuid.UUID auto-generated."""
        config = BaseConfig(name="no-team-agent")
        ref = SampleAgent.start(config=config)
        try:
            address = ActorAddressImpl(ref)
            team_id = address.team_id
            assert isinstance(team_id, uuid.UUID)
        finally:
            ref.stop()

    def test_team_id_explicit_value_preserved(self) -> None:
        """Actor started with an explicit team_id stores that exact value."""
        expected_team_id = uuid.uuid4()
        config = BaseConfig(name="explicit-team-agent")
        ref = SampleAgent.start(config=config, team_id=expected_team_id)
        try:
            address = ActorAddressImpl(ref)
            assert address.team_id == expected_team_id
        finally:
            ref.stop()

    def test_init_hook_called(self, agent_setup) -> None:
        """Agent on_start() hook is called during initialization."""

        class InitTestAgent(Akgent[BaseConfig, BaseState]):
            def __init__(self, **kwargs) -> None:
                self.init_called = False
                super().__init__(**kwargs)

            def on_start(self) -> None:
                self.init_called = True

        agent_id, config, team_id = agent_setup
        ref = InitTestAgent.start(
            agent_id=agent_id,
            config=config,
            team_id=team_id,
        )
        try:
            init_called = ref.proxy().init_called.get()
            assert init_called is True
        finally:
            ref.stop()


class TestMessageDispatch:
    """Tests for receiveMsg_<Type> pattern."""

    def test_message_handler_called(self, agent_setup) -> None:
        """Message dispatch invokes correct handler."""
        agent_id, config, team_id = agent_setup
        ref = SampleAgent.start(agent_id=agent_id, config=config, team_id=team_id)
        try:
            # Send message and verify handler called
            msg = SampleMessage(content="hello")
            result = ref.proxy().on_receive(msg).get(timeout=5)
            assert result == "hello"

            received = ref.proxy().received_messages.get()
            assert len(received) == 1
            assert received[0].content == "hello"
        finally:
            ref.stop()

    def test_derived_message_uses_handler(self, agent_setup) -> None:
        """Derived message types use parent handler via MRO."""
        agent_id, config, team_id = agent_setup
        ref = SampleAgent.start(agent_id=agent_id, config=config, team_id=team_id)
        try:
            # DerivedSampleMessage should use receiveMsg_SampleMessage
            msg = DerivedSampleMessage(content="derived", extra="data")
            result = ref.proxy().on_receive(msg).get(timeout=5)
            assert result == "derived"

            received = ref.proxy().received_messages.get()
            assert len(received) == 1
            assert received[0].content == "derived"
        finally:
            ref.stop()

    def test_unhandled_message_logs_warning(self, agent_setup, caplog) -> None:
        """Unhandled message type logs warning."""
        agent_id, config, team_id = agent_setup
        ref = SampleAgent.start(agent_id=agent_id, config=config, team_id=team_id)
        try:

            class UnhandledMessage:
                pass

            msg = UnhandledMessage()
            ref.proxy().on_receive(msg).get(timeout=5)

            # Check warning logged
            assert any("Unknown message" in record.message for record in caplog.records)
        finally:
            ref.stop()


class TestSuperSentinel:
    """Tests for SUPER fallthrough behavior."""

    def test_super_continues_mro_search(self, agent_setup) -> None:
        """Returning SUPER causes dispatcher to continue MRO walk."""
        agent_id, config, team_id = agent_setup
        ref = SuperSampleAgent.start(agent_id=agent_id, config=config, team_id=team_id)
        try:
            # receiveMsg_SampleMessage returns SUPER
            # Should fallback to receiveMsg_Message
            msg = SampleMessage(content="test")
            result = ref.proxy().on_receive(msg).get(timeout=5)

            assert result == "base_handler"
            handled = ref.proxy().handled_at_base.get()
            assert handled is True
        finally:
            ref.stop()


class TestChildActorCreation:
    """Tests for createActor and context propagation."""

    def test_create_child_actor(self, agent_setup) -> None:
        """Parent can create child actor."""
        agent_id, config, team_id = agent_setup
        config.squad_id = uuid.uuid4()
        ref = SampleAgent.start(agent_id=agent_id, config=config, team_id=team_id)
        try:
            # Create child via proxy
            child_config = BaseConfig(name="child-agent")
            child_address = (
                ref.proxy().createActor(SampleAgent, uuid.uuid4(), child_config).get(timeout=5)
            )

            assert child_address is not None
            assert child_address.is_alive()
            assert child_address.agent_id is not None
        finally:
            ref.stop()

    def test_child_inherits_squad_id(self, agent_setup) -> None:
        """Child actor inherits parent's squad_id if not specified."""
        agent_id, config, team_id = agent_setup
        parent_squad_id = uuid.uuid4()
        config.squad_id = parent_squad_id
        ref = SampleAgent.start(agent_id=agent_id, config=config, team_id=team_id)
        try:
            # Create child without squad_id
            child_config = BaseConfig(name="child-agent")
            child_address = (
                ref.proxy().createActor(SampleAgent, uuid.uuid4(), child_config).get(timeout=5)
            )

            child_ref = cast(ActorAddressImpl, child_address)._actor_ref
            child_squad_id = child_ref.proxy().config.get().squad_id

            assert child_squad_id == parent_squad_id
        finally:
            ref.stop()

    def test_child_overrides_squad_id(self, agent_setup) -> None:
        """Child can override parent's squad_id."""
        agent_id, config, team_id = agent_setup
        parent_squad_id = uuid.uuid4()
        child_squad_id = uuid.uuid4()
        config.squad_id = parent_squad_id
        ref = SampleAgent.start(agent_id=agent_id, config=config, team_id=team_id)
        try:
            # Create child with explicit squad_id
            child_config = BaseConfig(name="child-agent", squad_id=child_squad_id)
            child_address = (
                ref.proxy().createActor(SampleAgent, uuid.uuid4(), child_config).get(timeout=5)
            )

            child_ref = cast(ActorAddressImpl, child_address)._actor_ref
            actual_squad_id = child_ref.proxy().config.get().squad_id

            assert actual_squad_id == child_squad_id
        finally:
            ref.stop()


class TestStateManagement:
    """Tests for state_changed, update_state, init_state."""

    def test_init_state_preserves_observer(self, agent_setup) -> None:
        """init_state preserves observer reference."""
        agent_id, config, team_id = agent_setup
        ref = SampleAgent.start(agent_id=agent_id, config=config, team_id=team_id)
        try:
            # Get initial state observer
            initial_observer = ref.proxy().state.get()._observer

            # Create new state and initialize
            new_state = BaseState()
            ref.proxy().init_state(new_state).get(timeout=5)

            # Verify observer preserved
            updated_observer = ref.proxy().state.get()._observer
            assert updated_observer == initial_observer
        finally:
            ref.stop()

    def test_update_state_merges_updates(self, agent_setup) -> None:
        """update_state merges dictionary updates into state."""
        agent_id, config, team_id = agent_setup
        ref = SampleAgent.start(agent_id=agent_id, config=config, team_id=team_id)
        try:
            # BaseState is simple model - just verify update_state doesn't error
            # Full state update testing requires custom state types
            updates = {}
            ref.proxy().update_state(updates).get(timeout=5)

            # Verify state exists and is accessible
            state = ref.proxy().state.get()
            assert isinstance(state, BaseState)
        finally:
            ref.stop()


class TestStopBehavior:
    """Tests for stop and recursive cleanup."""

    def test_stop_cleans_up_children(self, agent_setup) -> None:
        """Stopping parent stops all children recursively."""
        agent_id, config, team_id = agent_setup
        ref = SampleAgent.start(agent_id=agent_id, config=config, team_id=team_id)
        try:
            # Create child
            child_config = BaseConfig(name="child-agent")
            child_address = (
                ref.proxy().createActor(SampleAgent, uuid.uuid4(), child_config).get(timeout=5)
            )

            assert child_address.is_alive()

            # Stop parent
            ref.proxy().stop().get(timeout=5)

            # Child should be stopped
            assert not child_address.is_alive()
        finally:
            # Cleanup
            try:
                ref.stop()
            except Exception:
                pass

    def test_stop_recursively_message(self, agent_setup) -> None:
        """StopRecursively message triggers recursive stop."""
        agent_id, config, team_id = agent_setup
        ref = SampleAgent.start(agent_id=agent_id, config=config, team_id=team_id)
        try:
            msg = StopRecursively()
            ref.proxy().on_receive(msg).get(timeout=5)

            # Agent should be stopped
            assert not ref.is_alive()
        finally:
            try:
                ref.stop()
            except Exception:
                pass


class TestProxyHelpers:
    """Tests for proxy_tell and proxy_ask."""

    def test_proxy_tell_fire_and_forget(self, agent_setup) -> None:
        """proxy_tell creates fire-and-forget proxy."""
        agent_id, config, team_id = agent_setup
        ref = SampleAgent.start(agent_id=agent_id, config=config, team_id=team_id)
        try:
            address = ActorAddressImpl(ref)

            # Create tell proxy
            proxy = ref.proxy().proxy_tell(address, SampleAgent).get()

            assert isinstance(proxy, ProxyWrapper)
            assert proxy._ask_mode is False
        finally:
            ref.stop()

    def test_proxy_ask_blocking(self, agent_setup) -> None:
        """proxy_ask creates blocking proxy."""
        agent_id, config, team_id = agent_setup
        ref = SampleAgent.start(agent_id=agent_id, config=config, team_id=team_id)
        try:
            address = ActorAddressImpl(ref)

            # Create ask proxy with timeout
            proxy = ref.proxy().proxy_ask(address, SampleAgent, timeout=10).get()

            assert isinstance(proxy, ProxyWrapper)
            assert proxy._ask_mode is True
            assert proxy._timeout == 10
        finally:
            ref.stop()


class TestProxyWrapper:
    """Tests for ProxyWrapper functionality."""

    def test_proxy_wrapper_ask_mode_resolves_futures(self, agent_setup) -> None:
        """Ask mode automatically resolves pykka futures."""
        agent_id, config, team_id = agent_setup
        ref = SampleAgent.start(agent_id=agent_id, config=config, team_id=team_id)
        try:
            address = ActorAddressImpl(ref)
            wrapper = ProxyWrapper(address, ask_mode=True, timeout=5)

            # Call method - should auto-resolve future
            result = wrapper.myAddress
            assert isinstance(result, ActorAddress)
        finally:
            ref.stop()

    def test_proxy_wrapper_tell_mode_returns_none(self, agent_setup) -> None:
        """Tell mode returns None without blocking."""
        agent_id, config, team_id = agent_setup
        ref = SampleAgent.start(agent_id=agent_id, config=config, team_id=team_id)
        try:
            address = ActorAddressImpl(ref)
            wrapper = ProxyWrapper(address, ask_mode=False)

            # Call method - should return None immediately
            result = wrapper.on_start()
            assert result is None
        finally:
            ref.stop()


class TestOrchestratorIntegration:
    """Tests for orchestrator notification integration."""

    def test_notify_orchestrator_sends_start_message(self, agent_setup) -> None:
        """Agent notifies orchestrator on initialization."""
        agent_id, config, team_id = agent_setup

        # Create mock orchestrator
        mock_orch_ref = MagicMock()
        mock_orch_ref.is_alive.return_value = True
        mock_orch = ActorAddressImpl(mock_orch_ref)

        ref = SampleAgent.start(
            agent_id=agent_id,
            config=config,
            team_id=team_id,
            orchestrator=mock_orch,
        )
        try:
            # Verify StartMessage sent
            assert mock_orch_ref.tell.called
            call_args = mock_orch_ref.tell.call_args_list[0][0]
            from akgentic.core.messages.orchestrator import StartMessage

            assert any(isinstance(arg, StartMessage) for arg in call_args)
        finally:
            ref.stop()

    def test_send_notifies_orchestrator(self, agent_setup) -> None:
        """send() notifies orchestrator with SentMessage."""
        agent_id, config, team_id = agent_setup

        # Create mock orchestrator
        mock_orch_ref = MagicMock()
        mock_orch_ref.is_alive.return_value = True
        mock_orch = ActorAddressImpl(mock_orch_ref)

        ref = SampleAgent.start(
            agent_id=agent_id,
            config=config,
            team_id=team_id,
            orchestrator=mock_orch,
        )
        try:
            # Create recipient
            recipient_ref = SampleAgent.start(
                agent_id=uuid.uuid4(),
                config=BaseConfig(),
                team_id=team_id,
            )
            recipient_address = ActorAddressImpl(recipient_ref)

            # Send message
            msg = SampleMessage(content="test")
            ref.proxy().send(recipient_address, msg).get(timeout=5)

            # Verify SentMessage sent to orchestrator
            from akgentic.core.messages.orchestrator import SentMessage

            sent_calls = [
                call
                for call in mock_orch_ref.tell.call_args_list
                if any(isinstance(arg, SentMessage) for arg in call[0])
            ]
            assert len(sent_calls) > 0

            recipient_ref.stop()
        finally:
            ref.stop()
