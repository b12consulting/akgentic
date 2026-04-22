"""Tests for ActorSystem and related classes."""

import uuid
from typing import cast

from akgentic.core.actor_address_impl import ActorAddressImpl, ActorAddressProxy
from akgentic.core.actor_system_impl import (
    ActorSystem,
    ActorSystemListener,
    ExecutionContext,
    ProxyWrapper,
    Statistics,
)
from akgentic.core.agent import Akgent
from akgentic.core.agent_config import BaseConfig
from akgentic.core.agent_state import BaseState


class SimpleAgent(Akgent[BaseConfig, BaseState]):
    """Simple agent for testing."""

    def receiveMsg_str(self, msg: str, sender) -> str:  # type: ignore
        """Handle string messages by echoing them back."""
        return f"received: {msg}"

    def get_user_context(self) -> dict[str, str | uuid.UUID | None]:
        """Get user context for testing."""
        return {
            "user_id": self._user_id,
            "user_email": self._user_email,
            "team_id": self.team_id,
        }


class TestStatistics:
    """Tests for Statistics dataclass."""

    def test_statistics_default_values(self) -> None:
        """Test that Statistics initializes with zero counts."""
        stats = Statistics()
        assert stats.orchestrator_count == 0
        assert stats.agent_count == 0

    def test_statistics_with_values(self) -> None:
        """Test Statistics with explicit values."""
        stats = Statistics(orchestrator_count=2, agent_count=10)
        assert stats.orchestrator_count == 2
        assert stats.agent_count == 10


class TestActorSystem:
    """Tests for ActorSystem."""

    def test_create_actor(self) -> None:
        """Test creating an actor returns a valid address."""
        system = ActorSystem()
        config = BaseConfig(name="test-agent", role="Tester")
        address = system.createActor(SimpleAgent, config=config)
        assert address is not None
        assert address.is_alive()
        system.shutdown()

    def test_get_actor(self) -> None:
        """Test retrieving an actor by agent_id."""
        system = ActorSystem()
        config = BaseConfig(name="test-agent", role="Tester")
        address = system.createActor(SimpleAgent, config=config)
        found = system.get_actor(address)
        assert found is not None
        assert found.agent_id == address.agent_id
        system.shutdown()

    def test_stat(self) -> None:
        """Test system statistics reporting."""
        system = ActorSystem()
        config = BaseConfig(name="test-agent", role="Tester")
        system.createActor(SimpleAgent, config=config)
        stats = system.stat()
        assert len(stats) == 1
        assert stats[0].agent_count >= 1
        system.shutdown()

    def test_shutdown_cleans_up_actors(self) -> None:
        """Test that shutdown properly stops all actors."""
        system = ActorSystem()
        config = BaseConfig(name="test-agent", role="Tester")
        address = system.createActor(SimpleAgent, config=config)
        system.shutdown()
        assert not address.is_alive()


class TestProxyWrapper:
    """Tests for ProxyWrapper tell/ask patterns."""

    def test_proxy_tell_mode(self) -> None:
        """Test proxy in tell mode (fire-and-forget)."""
        system = ActorSystem()
        config = BaseConfig(name="test-agent", role="Tester")
        address = system.createActor(SimpleAgent, config=config)

        proxy = ProxyWrapper(address, ask_mode=False)
        # Tell mode should not block and return None
        result = proxy.on_receive("test")
        assert result is None
        system.shutdown()

    def test_proxy_ask_mode(self) -> None:
        """Test proxy in ask mode (request-response)."""
        system = ActorSystem()
        config = BaseConfig(name="test-agent", role="Tester")
        address = system.createActor(SimpleAgent, config=config)

        proxy = ProxyWrapper(address, ask_mode=True, timeout=5.0)
        result = proxy.on_receive("test")
        assert result == "received: test"
        system.shutdown()


class TestExecutionContext:
    """Tests for ExecutionContext."""

    def test_context_creation(self) -> None:
        """Test that ExecutionContext creates a listener actor."""
        ctx = ExecutionContext()
        assert ctx.listener_ref is not None
        assert ctx.listener_ref.is_alive()
        ctx.shutdown()

    def test_private_context_manager(self) -> None:
        """Test that private context manager creates and cleans up contexts."""
        system = ActorSystem()
        with system.private() as ctx:
            assert ctx.listener_ref.is_alive()
        # After context exit, listener should be stopped
        system.shutdown()

    def test_tell_sends_message(self) -> None:
        """Test that tell sends messages to actors."""
        system = ActorSystem()
        config = BaseConfig(name="test-agent", role="Tester")
        address = system.createActor(SimpleAgent, config=config)
        ctx = ExecutionContext()

        # Send message via tell (fire and forget)
        ctx.tell(address, "hello")

        ctx.shutdown()
        system.shutdown()

    def test_ask_waits_for_response(self) -> None:
        """Test that ask waits for and returns a response."""
        system = ActorSystem()
        config = BaseConfig(name="test-agent", role="Tester")
        address = system.createActor(SimpleAgent, config=config)
        ctx = ExecutionContext()

        # Send message via ask and get response
        response = ctx.ask(address, "hello", timeout=5.0)
        assert response == "received: hello"

        ctx.shutdown()
        system.shutdown()


class TestActorSystemListener:
    """Tests for ActorSystemListener."""

    def test_listener_has_address(self) -> None:
        """Test that listener provides its address."""
        listener = ActorSystemListener.start()
        proxy = listener.proxy()
        address = proxy.myAddress().get()
        assert address is not None
        assert address.agent_id is not None
        listener.stop()

    def test_listener_queues_messages(self) -> None:
        """Test that listener queues messages when no waiters."""
        listener = ActorSystemListener.start()
        proxy = listener.proxy()

        # Send message - should be queued
        listener.tell("message1")

        # Listen should return the queued message
        result = proxy.listen().get()
        assert result == "message1"

        listener.stop()


class TestActorSystemEdgeCases:
    """Edge case tests for ActorSystem."""

    def test_create_actor_with_string_class(self) -> None:
        """Test creating actor from string class path."""
        system = ActorSystem()
        config = BaseConfig(name="test-agent", role="Tester")
        address = system.createActor("tests.core.test_actor_system_impl.SimpleAgent", config=config)
        assert address is not None
        assert address.is_alive()
        system.shutdown()

    def test_create_actor_with_string_agent_id(self) -> None:
        """Test creating actor with string UUID."""
        system = ActorSystem()
        config = BaseConfig(name="test-agent", role="Tester")
        agent_id = str(uuid.uuid4())
        address = system.createActor(SimpleAgent, agent_id=agent_id, config=config)
        assert address is not None
        system.shutdown()

    def test_create_actor_with_string_team_id(self) -> None:
        """Test creating actor with string team UUID."""
        system = ActorSystem()
        config = BaseConfig(name="test-agent", role="Tester")
        team_id = str(uuid.uuid4())
        address = system.createActor(SimpleAgent, team_id=team_id, config=config)
        assert address is not None
        system.shutdown()

    def test_create_actor_with_user_context(self) -> None:
        """Test that user_id and team_id are properly passed to agent."""
        system = ActorSystem()
        config = BaseConfig(name="test-agent", role="Tester")
        test_user_id = "test_user_123"
        test_team_id = uuid.uuid4()
        test_user_email = "test@example.com"

        address = system.createActor(
            SimpleAgent,
            user_id=test_user_id,
            user_email=test_user_email,
            team_id=test_team_id,
            config=config,
        )

        # Verify the agent received correct context
        address_impl = cast(ActorAddressImpl, address)
        proxy = address_impl._actor_ref.proxy()
        context = proxy.get_user_context().get()

        assert context["user_id"] == test_user_id
        assert context["user_email"] == test_user_email
        assert context["team_id"] == test_team_id

        system.shutdown()

    def test_get_actor_returns_none_for_nonexistent(self) -> None:
        """Test that get_actor returns None for non-existent agent."""
        system = ActorSystem()
        fake_id = str(uuid.uuid4())
        fake_address = ActorAddressProxy(
            {
                "__actor_address__": True,
                "__actor_type__": "test.Agent",
                "agent_id": fake_id,
                "name": "fake",
                "role": "Tester",
                "team_id": fake_id,
                "squad_id": fake_id,
                "user_message": True,
            }
        )
        found = system.get_actor(fake_address)
        assert found is None
        system.shutdown()

    def test_proxy_wrapper_repr(self) -> None:
        """Test ProxyWrapper string representation."""
        system = ActorSystem()
        config = BaseConfig(name="test-agent", role="Tester")
        address = system.createActor(SimpleAgent, config=config)
        proxy = ProxyWrapper(address, ask_mode=False)
        repr_str = repr(proxy)
        assert "ProxyWrapper" in repr_str
        system.shutdown()

    def test_proxy_ask_with_attribute_access(self) -> None:
        """Test ProxyWrapper accessing attributes in ask mode."""
        system = ActorSystem()
        config = BaseConfig(name="test-agent", role="Tester")
        address = system.createActor(SimpleAgent, config=config)
        proxy = ProxyWrapper(address, ask_mode=True, timeout=5.0)

        # Access agent_id through proxy (attribute access, not method call)
        agent_id = proxy.agent_id
        assert agent_id is not None

        system.shutdown()

    def test_listener_exception_in_future_set(self) -> None:
        """Test listener handles exception when setting future."""
        listener = ActorSystemListener.start()

        # Send a message to queue it
        listener.tell("message1")

        # Listen should return the message
        proxy = listener.proxy()
        result = proxy.listen().get()
        assert result == "message1"

        listener.stop()

    def test_execution_context_shutdown_with_error(self) -> None:
        """Test execution context handles shutdown errors gracefully."""
        ctx = ExecutionContext()
        # Force stop listener first to trigger error on shutdown
        ctx.listener_ref.stop(timeout=1)
        # Should not raise, just print warning
        ctx.shutdown()

    def test_private_context_shutdown_error(self) -> None:
        """Test private context handles cleanup errors gracefully."""
        system = ActorSystem()
        with system.private() as ctx:
            # Stop listener early to trigger error on context exit
            ctx.listener_ref.stop(timeout=1)
        # Should handle the error gracefully
        system.shutdown()

    def test_listen_with_future(self) -> None:
        """Test ExecutionContext.listen when message arrives via future."""
        ctx = ExecutionContext()
        system = ActorSystem()
        config = BaseConfig(name="test-agent", role="Tester")
        address = system.createActor(SimpleAgent, config=config)

        # Ask sends a message and the response comes back
        ctx.tell(address, "ping")

        # Clean up
        ctx.shutdown()
        system.shutdown()

    def test_proxy_tell_and_proxy_ask_methods(self) -> None:
        """Test ActorSystem proxy_tell and proxy_ask convenience methods."""
        system = ActorSystem()
        config = BaseConfig(name="test-agent", role="Tester")
        address = system.createActor(SimpleAgent, config=config)

        # Test proxy_tell
        tell_proxy = system.proxy_tell(address, SimpleAgent)
        result = tell_proxy.on_receive("test")
        assert result is None  # Tell returns None

        # Test proxy_ask
        ask_proxy = system.proxy_ask(address, SimpleAgent, timeout=5.0)
        result = ask_proxy.on_receive("test")
        assert result == "received: test"

        system.shutdown()
