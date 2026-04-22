"""Tests for ActorAddress abstractions.

Tests ActorAddress ABC, ActorAddressImpl, ActorAddressProxy, and ActorAddressStopped.
"""

import uuid
from unittest.mock import MagicMock

import pytest

from akgentic.core.utils.deserializer import ActorAddressDict


class TestActorAddressABC:
    """Tests for ActorAddress abstract base class."""

    def test_cannot_instantiate_abc(self) -> None:
        """ActorAddress ABC cannot be instantiated directly."""
        from akgentic.core.actor_address import ActorAddress

        with pytest.raises(TypeError):
            ActorAddress()  # type: ignore[abstract]


class TestActorAddressProxy:
    """Tests for ActorAddressProxy implementation."""

    @pytest.fixture
    def sample_address_dict(self) -> ActorAddressDict:
        """Create sample ActorAddressDict for testing."""
        return {
            "__actor_address__": True,
            "__actor_type__": "akgentic.actor_address_impl.ActorAddressProxy",
            "agent_id": "12345678-1234-5678-1234-567812345678",
            "name": "test-agent",
            "role": "assistant",
            "team_id": "87654321-4321-8765-4321-876543218765",
            "squad_id": "11111111-2222-3333-4444-555555555555",
            "user_message": True,
        }

    def test_properties_from_dict(self, sample_address_dict: ActorAddressDict) -> None:
        """All properties should be read from dict."""
        from akgentic.core.actor_address_impl import ActorAddressProxy

        proxy = ActorAddressProxy(sample_address_dict)
        assert proxy.agent_id == uuid.UUID("12345678-1234-5678-1234-567812345678")
        assert proxy.name == "test-agent"
        assert proxy.role == "assistant"
        assert proxy.team_id == uuid.UUID("87654321-4321-8765-4321-876543218765")
        assert proxy.squad_id == uuid.UUID("11111111-2222-3333-4444-555555555555")
        assert proxy.handle_user_message() is True
        assert proxy.is_alive() is True

    def test_send_raises_runtime_error(self, sample_address_dict: ActorAddressDict) -> None:
        """send should raise RuntimeError for proxy addresses."""
        from akgentic.core.actor_address_impl import ActorAddressProxy

        proxy = ActorAddressProxy(sample_address_dict)
        with pytest.raises(RuntimeError, match="Cannot send message from mock actor address"):
            proxy.send(proxy, {"content": "test"})

    def test_serialize_returns_dict(self, sample_address_dict: ActorAddressDict) -> None:
        """serialize should return the original ActorAddressDict."""
        from akgentic.core.actor_address_impl import ActorAddressProxy

        proxy = ActorAddressProxy(sample_address_dict)
        serialized = proxy.serialize()
        assert serialized["__actor_address__"] is True
        assert serialized["agent_id"] == "12345678-1234-5678-1234-567812345678"

    def test_equality_and_hashing(self, sample_address_dict: ActorAddressDict) -> None:
        """Equality and hash should be based on agent_id."""
        from akgentic.core.actor_address_impl import ActorAddressProxy

        proxy1 = ActorAddressProxy(sample_address_dict)
        proxy2 = ActorAddressProxy(sample_address_dict)
        dict2 = sample_address_dict.copy()
        dict2["agent_id"] = str(uuid.uuid4())
        proxy3 = ActorAddressProxy(dict2)

        assert proxy1 == proxy2
        assert proxy1 != proxy3
        assert hash(proxy1) == hash(proxy2)
        assert len({proxy1, proxy2}) == 1


class TestActorAddressStopped:
    """Tests for ActorAddressStopped implementation."""

    def test_is_alive_returns_false(self) -> None:
        """is_alive should return False for stopped addresses."""
        from akgentic.core.actor_address_impl import ActorAddressProxy, ActorAddressStopped

        address_dict: ActorAddressDict = {
            "__actor_address__": True,
            "__actor_type__": "akgentic.actor_address_impl.ActorAddressStopped",
            "agent_id": "12345678-1234-5678-1234-567812345678",
            "name": "stopped-agent",
            "role": "worker",
            "team_id": "87654321-4321-8765-4321-876543218765",
            "squad_id": "11111111-2222-3333-4444-555555555555",
            "user_message": False,
        }
        stopped = ActorAddressStopped(address_dict)
        assert isinstance(stopped, ActorAddressProxy)
        assert stopped.is_alive() is False
        assert stopped.name == "stopped-agent"


class TestActorAddressImpl:
    """Tests for ActorAddressImpl wrapping Pykka ActorRef."""

    @pytest.fixture
    def mock_actor_ref(self) -> MagicMock:
        """Create mock Pykka ActorRef with actor matching pykka 4.4.2 weakref API.

        Accesses properties via:
        - agent_id: _actor_weakref().agent_id
        - name: _actor_weakref().config.name (with fallback)
        - role: _actor_weakref().config.role (with fallback)
        - team_id: _actor_weakref().team_id (public flat attribute, not via _config)
        - squad_id: _actor_weakref().config.squad_id (from user config)
        - handle_user_message: checks for receiveMsg_UserMessage method
        """
        # Create config object (user config)
        config = MagicMock()
        config.name = "mock-agent"
        config.role = "assistant"
        config.squad_id = uuid.UUID("11111111-2222-3333-4444-555555555555")

        # Create actor with flat public attributes (post-7-1 rename)
        actor = MagicMock()
        actor.agent_id = uuid.UUID("12345678-1234-5678-1234-567812345678")
        actor.config = config
        actor.team_id = uuid.UUID("87654321-4321-8765-4321-876543218765")
        # Add receiveMsg_UserMessage method for handle_user_message check
        actor.receiveMsg_UserMessage = MagicMock()
        # Set __class__ for serialize test
        actor.__class__ = type(
            "MockAgent", (), {"__module__": "test.agents", "__name__": "MockAgent"}
        )

        actor_ref = MagicMock()
        # pykka 4.4.2+: _actor_weakref() returns the actor (callable, not attribute)
        actor_ref._actor_weakref = lambda: actor
        actor_ref.is_alive.return_value = True
        actor_ref.proxy.return_value = MagicMock()
        return actor_ref

    def test_properties_from_actor(self, mock_actor_ref: MagicMock) -> None:
        """All properties should come from the underlying actor via config objects."""
        from akgentic.core.actor_address_impl import ActorAddressImpl

        actor = mock_actor_ref._actor_weakref()
        impl = ActorAddressImpl(mock_actor_ref)
        assert impl.agent_id == actor.agent_id
        assert impl.name == actor.config.name
        assert impl.role == actor.config.role
        assert impl.team_id == actor.team_id
        assert impl.squad_id == actor.config.squad_id
        # pykka 4.4.2+: checks for receiveMsg_UserMessage method existence
        assert impl.handle_user_message() is True

    def test_team_id_reads_flat_attribute(self, mock_actor_ref: MagicMock) -> None:
        """team_id should read team_id directly from actor, not via config."""
        from akgentic.core.actor_address_impl import ActorAddressImpl

        expected = uuid.UUID("87654321-4321-8765-4321-876543218765")
        actor = mock_actor_ref._actor_weakref()
        actor.team_id = expected

        impl = ActorAddressImpl(mock_actor_ref)
        assert impl.team_id == expected

    def test_is_alive_delegates_to_ref(self, mock_actor_ref: MagicMock) -> None:
        """is_alive should delegate to ActorRef."""
        from akgentic.core.actor_address_impl import ActorAddressImpl

        impl = ActorAddressImpl(mock_actor_ref)
        assert impl.is_alive() is True
        mock_actor_ref.is_alive.return_value = False
        assert impl.is_alive() is False

    def test_handle_user_message_returns_false_when_no_receive_method(
        self, mock_actor_ref: MagicMock
    ) -> None:
        """handle_user_message should return False when receiveMsg_UserMessage is absent."""
        from akgentic.core.actor_address_impl import ActorAddressImpl

        # Use spec to prevent MagicMock from auto-creating receiveMsg_UserMessage
        limited_actor = MagicMock(spec=["agent_id", "config"])
        mock_actor_ref._actor_weakref = lambda: limited_actor

        impl = ActorAddressImpl(mock_actor_ref)
        assert impl.handle_user_message() is False

    def test_serialize_produces_correct_dict(self, mock_actor_ref: MagicMock) -> None:
        """serialize should produce correct ActorAddressDict with actual agent class."""
        from akgentic.core.actor_address_impl import ActorAddressImpl

        actor = mock_actor_ref._actor_weakref()
        impl = ActorAddressImpl(mock_actor_ref)
        serialized = impl.serialize()

        assert serialized["__actor_address__"] is True
        # pykka 4.4.2+: serializes the actual agent class, not ActorAddressImpl
        assert serialized["__actor_type__"] == "test.agents.MockAgent"
        assert serialized["agent_id"] == str(actor.agent_id)
        assert serialized["name"] == actor.config.name
        assert serialized["role"] == actor.config.role
        assert serialized["team_id"] == str(actor.team_id)
        assert serialized["squad_id"] == str(actor.config.squad_id)
        assert serialized["user_message"] is True

    def test_resolve_actor_raises_on_gc(self, mock_actor_ref: MagicMock) -> None:
        """_resolve_actor raises RuntimeError containing the actor URN when weakref returns None."""
        from akgentic.core.actor_address_impl import ActorAddressImpl

        # Simulate GC'd actor: weakref returns None
        mock_actor_ref._actor_weakref = lambda: None
        mock_actor_ref.actor_urn = "urn:mock:gc-actor"

        impl = ActorAddressImpl(mock_actor_ref)
        with pytest.raises(RuntimeError, match="urn:mock:gc-actor"):
            _ = impl.agent_id

    def test_equality_and_hashing(self, mock_actor_ref: MagicMock) -> None:
        """Equality and hash should be based on agent_id."""
        from akgentic.core.actor_address_impl import ActorAddressImpl

        impl1 = ActorAddressImpl(mock_actor_ref)
        impl2 = ActorAddressImpl(mock_actor_ref)
        assert impl1 == impl2
        assert hash(impl1) == hash(impl2)
