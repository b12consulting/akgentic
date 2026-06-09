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

        # Create a real actor instance so ``type(actor)`` (ADR-013 §1 caches the
        # actor type at construction) resolves to the intended agent class for the
        # serialize ``__actor_type__`` check.
        mock_agent_cls = type(
            "MockAgent", (), {"__module__": "test.agents"}
        )
        actor = mock_agent_cls()
        # Flat public attributes (post-7-1 rename)
        actor.agent_id = uuid.UUID("12345678-1234-5678-1234-567812345678")
        actor.config = config
        actor.team_id = uuid.UUID("87654321-4321-8765-4321-876543218765")
        # Add receiveMsg_UserMessage method for handle_user_message check
        actor.receiveMsg_UserMessage = MagicMock()

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

        # Use spec to prevent MagicMock from auto-creating receiveMsg_UserMessage,
        # while keeping every metadata attribute the constructor snapshots so the
        # cache captures cleanly and only the user-message flag resolves to False.
        limited_actor = MagicMock(spec=["agent_id", "config", "team_id"])
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

    def test_dead_ref_at_construction_leaves_safe_snapshot(
        self, mock_actor_ref: MagicMock
    ) -> None:
        """An already-dead ref at construction yields a safe snapshot, never raises.

        Snapshot semantics (ADR-013): metadata is captured at construction. If the
        weakref is already None when the address is built (rare), the accessors
        return the safe defaults (None/False) instead of raising RuntimeError.
        """
        from akgentic.core.actor_address_impl import ActorAddressImpl

        # Simulate a ref whose actor is already gone at construction time.
        mock_actor_ref._actor_weakref = lambda: None
        mock_actor_ref.actor_urn = "urn:mock:gc-actor"

        impl = ActorAddressImpl(mock_actor_ref)
        # No RuntimeError on any read — everything falls back to the safe snapshot.
        assert impl.agent_id is None
        assert impl.name is None
        assert impl.role is None
        assert impl.team_id is None
        assert impl.squad_id is None
        assert impl.handle_user_message() is False
        serialized = impl.serialize()
        assert serialized["__actor_type__"] == ""
        assert serialized["agent_id"] == ""

    def test_equality_and_hashing(self, mock_actor_ref: MagicMock) -> None:
        """Equality and hash should be based on agent_id."""
        from akgentic.core.actor_address_impl import ActorAddressImpl

        impl1 = ActorAddressImpl(mock_actor_ref)
        impl2 = ActorAddressImpl(mock_actor_ref)
        assert impl1 == impl2
        assert hash(impl1) == hash(impl2)

    def test_metadata_reflects_construction_snapshot(self, mock_actor_ref: MagicMock) -> None:
        """Metadata is a snapshot captured at construction, not a live view (ADR-013).

        An address built earlier keeps the values it captured even after the
        underlying actor's config is mutated. This documents the
        snapshot-not-live semantics: reads never re-resolve the actor.
        """
        from akgentic.core.actor_address_impl import ActorAddressImpl

        actor = mock_actor_ref._actor_weakref()
        impl = ActorAddressImpl(mock_actor_ref)
        captured_name = impl.name
        captured_role = impl.role

        # Mutate the live actor's config AFTER the address was built.
        actor.config.name = "renamed-agent"
        actor.config.role = "renamed-role"

        # The address keeps its construction-time snapshot — it does not track
        # later mutations.
        assert impl.name == captured_name == "mock-agent"
        assert impl.role == captured_role == "assistant"


class TestActorAddressImplResilientAfterGC:
    """ActorAddressImpl reads survive a real actor being stopped and GC-collected.

    Uses real ``akgentic-core`` actors (no mocks) so the Pykka 4.4.2+ weakref is
    genuinely cleared by ``gc.collect()`` after the actor stops. All assertions
    are behaviour-only (Golden Rule #8).
    """

    @staticmethod
    def _build_dead_address() -> "ActorAddressImpl":  # type: ignore[name-defined]  # noqa: F821
        """Create an actor, capture its address, then stop + GC the actor.

        Returns an ``ActorAddressImpl`` whose underlying actor has been collected
        (its weakref now resolves to None), plus the metadata captured before
        teardown is verified by the caller via the returned address only.
        """
        import gc

        from akgentic.core.actor_address_impl import ActorAddressImpl
        from akgentic.core.actor_system_impl import ActorSystem
        from akgentic.core.agent import Akgent
        from akgentic.core.agent_config import BaseConfig

        squad = uuid.uuid4()
        system = ActorSystem()
        address = system.createActor(
            Akgent,
            config=BaseConfig(name="gc-agent", role="worker", squad_id=squad),
        )
        assert isinstance(address, ActorAddressImpl)

        # Stop the actor and drop every strong reference, then force collection so
        # the ActorRef's weakref resolves to None.
        system.proxy_ask(address, Akgent).stop()
        system.shutdown()
        gc.collect()
        return address

    def test_metadata_survives_actor_gc(self) -> None:
        """All metadata accessors return captured values after stop + GC (no raise)."""
        address = self._build_dead_address()
        assert address.name == "gc-agent"
        assert address.role == "worker"
        assert isinstance(address.agent_id, uuid.UUID)
        assert isinstance(address.team_id, uuid.UUID)
        assert isinstance(address.squad_id, uuid.UUID)
        # Base Akgent has no receiveMsg_UserMessage handler.
        assert address.handle_user_message() is False

    def test_serialize_survives_actor_gc(self) -> None:
        """serialize() returns the full dict after stop + GC (the §3.1 serialize case)."""
        address = self._build_dead_address()
        serialized = address.serialize()
        assert serialized["__actor_address__"] is True
        assert serialized["__actor_type__"].endswith(".Akgent")
        assert serialized["name"] == "gc-agent"
        assert serialized["role"] == "worker"
        assert serialized["agent_id"] != ""
        assert serialized["team_id"] != ""
        assert serialized["squad_id"] != ""
        assert serialized["user_message"] is False

    def test_is_alive_false_after_gc(self) -> None:
        """is_alive() returns False (not raises) after stop + GC."""
        address = self._build_dead_address()
        assert address.is_alive() is False

    def test_eq_hash_survive_gc(self) -> None:
        """__eq__ / __hash__ keep working after stop + GC (cached agent_id)."""
        address = self._build_dead_address()
        # Self-equality and hashability both rely on the cached agent_id.
        assert address == address
        assert hash(address) == hash(address.agent_id)
        assert len({address, address}) == 1
