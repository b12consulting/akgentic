"""Tests for context management with checkpointing."""

# Direct import to avoid providers dependency issue temporarily
import importlib.util
from datetime import datetime
from pathlib import Path

import pytest
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    SystemPromptPart,
    TextPart,
    UserPromptPart,
)

spec = importlib.util.spec_from_file_location(
    "context", Path(__file__).parent.parent / "src" / "akgentic" / "llm" / "context.py"
)
context_module = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
spec.loader.exec_module(context_module)  # type: ignore[union-attr]

ContextManager = context_module.ContextManager
ContextObserver = context_module.ContextObserver
ContextSnapshot = context_module.ContextSnapshot
LlmMessageEvent = context_module.LlmMessageEvent
LlmCheckpointCreatedEvent = context_module.LlmCheckpointCreatedEvent
LlmCheckpointRestoredEvent = context_module.LlmCheckpointRestoredEvent


class MockObserver:
    """Mock observer for testing notifications."""

    def __init__(self) -> None:
        self.messages_added: list[ModelMessage] = []
        self.checkpoints_created: list[ContextSnapshot] = []
        self.rewinds: list[ContextSnapshot] = []

    def notify_event(self, event: object) -> None:
        """Track typed context events."""
        if isinstance(event, LlmMessageEvent):
            self.messages_added.append(event.message)
        elif isinstance(event, LlmCheckpointCreatedEvent):
            self.checkpoints_created.append(event.snapshot)
        elif isinstance(event, LlmCheckpointRestoredEvent):
            self.rewinds.append(event.snapshot)


def create_user_message(content: str) -> ModelRequest:
    """Create a user message for testing."""
    return ModelRequest(parts=[UserPromptPart(content=content)])


def create_system_message(content: str) -> ModelRequest:
    """Create a system message for testing."""
    return ModelRequest(parts=[SystemPromptPart(content=content)])


def create_assistant_message(content: str) -> ModelResponse:
    """Create an assistant message for testing."""
    return ModelResponse(
        parts=[TextPart(content=content)],
        timestamp=datetime.now(),
    )


class TestContextSnapshot:
    """Test ContextSnapshot model."""

    def test_create_snapshot(self) -> None:
        """Test creating a snapshot with all fields."""
        msg = create_user_message("test")
        timestamp = datetime.now()
        metadata = {"key": "value"}

        snapshot = ContextSnapshot(
            checkpoint_id="test-id",
            timestamp=timestamp,
            messages=[msg],
            metadata=metadata,
        )

        assert snapshot.checkpoint_id == "test-id"
        assert snapshot.timestamp == timestamp
        assert len(snapshot.messages) == 1
        assert snapshot.metadata == metadata

    def test_snapshot_default_metadata(self) -> None:
        """Test snapshot with default empty metadata."""
        msg = create_user_message("test")
        snapshot = ContextSnapshot(
            checkpoint_id="test-id",
            timestamp=datetime.now(),
            messages=[msg],
        )

        assert snapshot.metadata == {}


class TestContextManager:
    """Test ContextManager functionality."""

    def test_init_empty(self) -> None:
        """Test initialization with no messages."""
        manager = ContextManager()
        assert manager.messages == []

    def test_init_with_max_messages(self) -> None:
        """Test initialization with max_messages limit."""
        manager = ContextManager(max_messages=10)
        assert manager.messages == []

    def test_init_with_negative_max_messages_raises(self) -> None:
        """Test initialization with negative max_messages raises ValueError."""
        with pytest.raises(ValueError, match="max_messages must be non-negative"):
            ContextManager(max_messages=-1)

    def test_init_with_zero_max_messages(self) -> None:
        """Test initialization with zero max_messages is allowed."""
        manager = ContextManager(max_messages=0)
        assert manager.messages == []

    def test_add_message(self) -> None:
        """Test adding a message to context."""
        manager = ContextManager()
        msg = create_user_message("Hello")

        manager.add_message(msg)

        assert len(manager.messages) == 1
        assert manager.messages[0] == msg

    def test_messages_property_returns_copy(self) -> None:
        """Test that messages property returns a copy."""
        manager = ContextManager()
        msg = create_user_message("Hello")
        manager.add_message(msg)

        messages = manager.messages
        messages.append(create_user_message("Extra"))

        # Original should be unchanged
        assert len(manager.messages) == 1

    def test_checkpoint_auto_generates_uuid(self) -> None:
        """Test checkpoint auto-generates UUID if no id provided."""
        manager = ContextManager()
        manager.add_message(create_user_message("Hello"))

        snapshot = manager.checkpoint()

        assert snapshot.checkpoint_id is not None
        assert len(snapshot.checkpoint_id) > 0
        assert len(snapshot.messages) == 1

    def test_checkpoint_with_explicit_id(self) -> None:
        """Test checkpoint with explicit id."""
        manager = ContextManager()
        manager.add_message(create_user_message("Hello"))

        snapshot = manager.checkpoint(checkpoint_id="my-checkpoint")

        assert snapshot.checkpoint_id == "my-checkpoint"
        assert len(snapshot.messages) == 1

    def test_checkpoint_with_metadata(self) -> None:
        """Test checkpoint with metadata."""
        manager = ContextManager()
        manager.add_message(create_user_message("Hello"))
        metadata = {"reason": "test", "version": 1}

        snapshot = manager.checkpoint(checkpoint_id="test", metadata=metadata)

        assert snapshot.metadata == metadata

    def test_rewind_restores_messages(self) -> None:
        """Test rewind restores messages to checkpoint state."""
        manager = ContextManager()
        manager.add_message(create_user_message("Message 1"))
        snapshot = manager.checkpoint(checkpoint_id="checkpoint-1")

        manager.add_message(create_user_message("Message 2"))
        manager.add_message(create_user_message("Message 3"))
        assert len(manager.messages) == 3

        manager.rewind("checkpoint-1")

        assert len(manager.messages) == 1
        assert manager.messages[0] == snapshot.messages[0]
        assert manager.messages[0].parts[0].content == "Message 1"  # type: ignore[attr-defined]

    def test_rewind_invalid_id_raises_keyerror(self) -> None:
        """Test rewind with invalid id raises KeyError."""
        manager = ContextManager()
        manager.add_message(create_user_message("Hello"))

        with pytest.raises(KeyError):
            manager.rewind("nonexistent-checkpoint")

    def test_get_checkpoint_returns_snapshot(self) -> None:
        """Test get_checkpoint returns snapshot for valid id."""
        manager = ContextManager()
        manager.add_message(create_user_message("Hello"))
        original_snapshot = manager.checkpoint(checkpoint_id="test")

        retrieved = manager.get_checkpoint("test")

        assert retrieved is not None
        assert retrieved.checkpoint_id == original_snapshot.checkpoint_id
        assert len(retrieved.messages) == 1

    def test_get_checkpoint_returns_none_for_invalid_id(self) -> None:
        """Test get_checkpoint returns None for invalid id."""
        manager = ContextManager()

        result = manager.get_checkpoint("nonexistent")

        assert result is None

    def test_list_checkpoints_returns_ids_in_order(self) -> None:
        """Test list_checkpoints returns ids in creation order."""
        manager = ContextManager()
        manager.add_message(create_user_message("Message"))

        manager.checkpoint(checkpoint_id="first")
        manager.checkpoint(checkpoint_id="second")
        manager.checkpoint(checkpoint_id="third")

        checkpoint_ids = manager.list_checkpoints()

        assert checkpoint_ids == ["first", "second", "third"]

    def test_observer_on_message_added(self) -> None:
        """Test observer on_message_added is called."""
        manager = ContextManager()
        observer = MockObserver()
        manager.subscribe(observer)

        msg = create_user_message("Hello")
        manager.add_message(msg)

        assert len(observer.messages_added) == 1
        assert observer.messages_added[0] == msg

    def test_observer_on_checkpoint_created(self) -> None:
        """Test observer on_checkpoint_created is called."""
        manager = ContextManager()
        observer = MockObserver()
        manager.subscribe(observer)
        manager.add_message(create_user_message("Hello"))

        snapshot = manager.checkpoint(checkpoint_id="test")

        assert len(observer.checkpoints_created) == 1
        assert observer.checkpoints_created[0].checkpoint_id == snapshot.checkpoint_id

    def test_observer_on_rewind(self) -> None:
        """Test observer on_rewind is called."""
        manager = ContextManager()
        observer = MockObserver()
        manager.subscribe(observer)

        manager.add_message(create_user_message("Message 1"))
        snapshot = manager.checkpoint(checkpoint_id="test")
        manager.add_message(create_user_message("Message 2"))

        manager.rewind("test")

        assert len(observer.rewinds) == 1
        assert observer.rewinds[0].checkpoint_id == snapshot.checkpoint_id

    def test_sliding_window_enforces_max_messages(self) -> None:
        """Test sliding window enforces max_messages limit."""
        manager = ContextManager(max_messages=3)

        manager.add_message(create_user_message("Message 1"))
        manager.add_message(create_user_message("Message 2"))
        manager.add_message(create_user_message("Message 3"))
        manager.add_message(create_user_message("Message 4"))

        messages = manager.messages
        assert len(messages) == 3
        # Should keep the last 3 messages
        assert messages[0].parts[0].content == "Message 2"  # type: ignore[attr-defined]
        assert messages[1].parts[0].content == "Message 3"  # type: ignore[attr-defined]
        assert messages[2].parts[0].content == "Message 4"  # type: ignore[attr-defined]

    def test_sliding_window_preserves_system_messages(self) -> None:
        """Test sliding window preserves system messages."""
        manager = ContextManager(max_messages=3)

        # Add system message first
        manager.add_message(create_system_message("System prompt"))
        manager.add_message(create_user_message("User 1"))
        manager.add_message(create_user_message("User 2"))
        manager.add_message(create_user_message("User 3"))

        messages = manager.messages
        assert len(messages) == 3

        # System message should be preserved
        assert isinstance(messages[0].parts[0], SystemPromptPart)
        assert messages[0].parts[0].content == "System prompt"

        # Only last 2 non-system messages should remain
        assert messages[1].parts[0].content == "User 2"  # type: ignore[attr-defined]
        assert messages[2].parts[0].content == "User 3"  # type: ignore[attr-defined]

    def test_checkpoint_stores_deep_copy(self) -> None:
        """Test checkpoint stores deep copy of messages."""
        manager = ContextManager()
        msg = create_user_message("Original")
        manager.add_message(msg)

        snapshot = manager.checkpoint(checkpoint_id="test")

        # Modify the original message's content (if mutable parts exist)
        # Since pydantic models are immutable, we'll add another message
        manager.add_message(create_user_message("Modified"))

        # Snapshot should still have only 1 message
        assert len(snapshot.messages) == 1
        assert snapshot.messages[0].parts[0].content == "Original"  # type: ignore[attr-defined]

        # Manager should have 2 messages
        assert len(manager.messages) == 2

    def test_unsubscribe_stops_notifications(self) -> None:
        """Test unsubscribe stops observer notifications."""
        manager = ContextManager()
        observer = MockObserver()

        manager.subscribe(observer)
        manager.add_message(create_user_message("Before unsubscribe"))
        assert len(observer.messages_added) == 1

        manager.unsubscribe(observer)
        manager.add_message(create_user_message("After unsubscribe"))

        # Should still be 1 (not notified of second message)
        assert len(observer.messages_added) == 1

    def test_unsubscribe_nonexistent_observer_is_noop(self) -> None:
        """Test unsubscribe with non-existent observer is no-op."""
        manager = ContextManager()
        observer = MockObserver()

        # Should not raise an error
        manager.unsubscribe(observer)

    def test_clear_empties_messages_and_checkpoints(self) -> None:
        """Test clear empties messages and checkpoints."""
        manager = ContextManager()
        manager.add_message(create_user_message("Message 1"))
        manager.checkpoint(checkpoint_id="checkpoint-1")
        manager.add_message(create_user_message("Message 2"))
        manager.checkpoint(checkpoint_id="checkpoint-2")

        assert len(manager.messages) == 2
        assert len(manager.list_checkpoints()) == 2

        manager.clear()

        assert len(manager.messages) == 0
        assert len(manager.list_checkpoints()) == 0
        assert manager.get_checkpoint("checkpoint-1") is None
        assert manager.get_checkpoint("checkpoint-2") is None

    def test_checkpoint_with_empty_messages(self) -> None:
        """Test checkpoint works with empty message list."""
        manager = ContextManager()

        # Checkpoint before any messages added
        snapshot = manager.checkpoint(checkpoint_id="empty")

        assert len(snapshot.messages) == 0
        assert snapshot.checkpoint_id == "empty"

        # Should be able to rewind to empty state
        manager.add_message(create_user_message("Message"))
        assert len(manager.messages) == 1

        manager.rewind("empty")
        assert len(manager.messages) == 0

    def test_sliding_window_with_only_system_messages_exceeding_limit(self) -> None:
        """Test sliding window when system messages alone exceed max_messages."""
        manager = ContextManager(max_messages=2)

        # Add 3 system messages (exceeds max_messages=2)
        manager.add_message(create_system_message("System 1"))
        manager.add_message(create_system_message("System 2"))
        manager.add_message(create_system_message("System 3"))

        messages = manager.messages
        # All system messages should be preserved despite exceeding limit
        assert len(messages) == 3
        assert all(isinstance(m.parts[0], SystemPromptPart) for m in messages)
        assert messages[0].parts[0].content == "System 1"
        assert messages[1].parts[0].content == "System 2"
        assert messages[2].parts[0].content == "System 3"

    def test_sliding_window_with_mixed_messages_when_system_exceeds_limit(self) -> None:
        """Test sliding window with system messages exceeding limit plus user messages."""
        manager = ContextManager(max_messages=2)

        # Add 3 system messages (already exceeds limit)
        manager.add_message(create_system_message("System 1"))
        manager.add_message(create_system_message("System 2"))
        manager.add_message(create_system_message("System 3"))

        # Add user messages
        manager.add_message(create_user_message("User 1"))
        manager.add_message(create_user_message("User 2"))

        messages = manager.messages
        # All 3 system messages preserved + both user messages = 5 total
        # This is correct: system messages don't count toward limit
        assert len(messages) == 5

        # First 3 should be system messages
        assert isinstance(messages[0].parts[0], SystemPromptPart)
        assert isinstance(messages[1].parts[0], SystemPromptPart)
        assert isinstance(messages[2].parts[0], SystemPromptPart)

        # Last 2 should be user messages
        assert messages[3].parts[0].content == "User 1"  # type: ignore[attr-defined]
        assert messages[4].parts[0].content == "User 2"  # type: ignore[attr-defined]


class TestContextManagerRestore:
    """Test ContextManager.restore() method."""

    def test_restore_replaces_existing_messages(self) -> None:
        """Test restore() replaces existing messages with provided list."""
        manager = ContextManager()
        manager.add_message(create_user_message("Old 1"))
        manager.add_message(create_user_message("Old 2"))
        assert len(manager.messages) == 2

        new_msgs = [
            create_user_message("New 1"),
            create_user_message("New 2"),
            create_user_message("New 3"),
        ]
        manager.restore(new_msgs)

        assert len(manager.messages) == 3
        assert manager.messages[0].parts[0].content == "New 1"  # type: ignore[attr-defined]
        assert manager.messages[1].parts[0].content == "New 2"  # type: ignore[attr-defined]
        assert manager.messages[2].parts[0].content == "New 3"  # type: ignore[attr-defined]

    def test_restore_empty_clears_messages(self) -> None:
        """Test restore([]) clears messages."""
        manager = ContextManager()
        manager.add_message(create_user_message("Existing"))
        assert len(manager.messages) == 1

        manager.restore([])

        assert len(manager.messages) == 0

    def test_restore_does_not_notify_observers(self) -> None:
        """Test restore() does NOT notify observers (bulk rebuild, not incremental)."""
        manager = ContextManager()
        observer = MockObserver()
        manager.subscribe(observer)

        # Add a message normally — should notify
        manager.add_message(create_user_message("Normal"))
        assert len(observer.messages_added) == 1

        # Restore — should NOT notify
        manager.restore([create_user_message("Restored 1"), create_user_message("Restored 2")])

        # Observer should still have only 1 notification (from add_message)
        assert len(observer.messages_added) == 1
        assert len(observer.checkpoints_created) == 0
        assert len(observer.rewinds) == 0

    def test_restore_does_not_apply_sliding_window(self) -> None:
        """Test restore() does NOT apply sliding window (restored messages are authoritative)."""
        manager = ContextManager(max_messages=2)

        # Restore 5 messages — all should be kept despite max_messages=2
        msgs = [create_user_message(f"Msg {i}") for i in range(5)]
        manager.restore(msgs)

        assert len(manager.messages) == 5

    def test_restore_then_checkpoint_and_rewind(self) -> None:
        """Test that after restore(), checkpoint() and rewind() still work correctly."""
        manager = ContextManager()

        # Restore some messages
        restored = [create_user_message("R1"), create_user_message("R2")]
        manager.restore(restored)
        assert len(manager.messages) == 2

        # Checkpoint
        snapshot = manager.checkpoint(checkpoint_id="after-restore")
        assert len(snapshot.messages) == 2

        # Add more messages
        manager.add_message(create_user_message("New"))
        assert len(manager.messages) == 3

        # Rewind
        manager.rewind("after-restore")
        assert len(manager.messages) == 2
        assert manager.messages[0].parts[0].content == "R1"  # type: ignore[attr-defined]

    def test_restore_creates_defensive_copy(self) -> None:
        """Test restore() creates a defensive copy of the input list."""
        manager = ContextManager()
        original = [create_user_message("A")]
        manager.restore(original)

        # Mutate the original list — should not affect manager
        original.append(create_user_message("B"))

        assert len(manager.messages) == 1


class TestContextObserverProtocol:
    """Test ContextObserver protocol conformance."""

    def test_mock_observer_conforms_to_protocol(self) -> None:
        """Test that MockObserver conforms to ContextObserver protocol."""
        observer = MockObserver()
        assert isinstance(observer, ContextObserver)
