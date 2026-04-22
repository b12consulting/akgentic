"""Tests for message primitives.

Tests base Message class, UserMessage, ResultMessage, and orchestrator messages.
"""

import uuid
from datetime import UTC, datetime

from akgentic.core.messages.message import (
    Message,
    ResultMessage,
    StopRecursively,
    UserMessage,
    date_time_factory,
)
from akgentic.core.messages.orchestrator import (
    ErrorMessage,
    ProcessedMessage,
    ReceivedMessage,
    SentMessage,
    StartMessage,
    StateChangedMessage,
    StopMessage,
)


class TestDateTimeFactory:
    """Tests for date_time_factory function."""

    def test_returns_datetime(self) -> None:
        """Should return datetime object."""
        result = date_time_factory()
        assert isinstance(result, datetime)

    def test_returns_utc_timezone(self) -> None:
        """Should return datetime with UTC timezone."""
        result = date_time_factory()
        assert result.tzinfo == UTC


class TestMessage:
    """Tests for base Message class."""

    def test_auto_generates_id(self) -> None:
        """Should auto-generate UUID for id field."""
        msg = Message()
        assert isinstance(msg.id, uuid.UUID)

    def test_unique_ids(self) -> None:
        """Should generate unique IDs for each message."""
        msg1 = Message()
        msg2 = Message()
        assert msg1.id != msg2.id

    def test_auto_generates_timestamp(self) -> None:
        """Should auto-generate timestamp in UTC."""
        msg = Message()
        assert isinstance(msg.timestamp, datetime)
        assert msg.timestamp is not None
        assert msg.timestamp.tzinfo == UTC

    def test_default_display_type(self) -> None:
        """Should default display_type to 'other'."""
        msg = Message()
        assert msg.display_type == "other"

    def test_default_sender_is_none(self) -> None:
        """Should default sender to None."""
        msg = Message()
        assert msg.sender is None

    def test_default_parent_id_is_none(self) -> None:
        """Should default parent_id to None."""
        msg = Message()
        assert msg.parent_id is None

    def test_default_team_id_is_none(self) -> None:
        """Should default team_id to None."""
        msg = Message()
        assert msg.team_id is None

    def test_init_returns_self(self) -> None:
        """init() should return self for chaining."""
        msg = Message()
        result = msg.init(sender=None)
        assert result is msg

    def test_init_sets_sender(self) -> None:
        """init() should set sender."""
        msg = Message()
        sender_mock = "mock_sender"
        msg.init(sender=sender_mock)
        assert msg.sender == sender_mock

    def test_init_sets_parent_id_from_current_message(self) -> None:
        """init() should set parent_id from current_message.id."""
        parent = Message()
        child = Message()
        child.init(sender=None, current_message=parent)
        assert child.parent_id == parent.id

    def test_init_sets_team_id(self) -> None:
        """init() should set team_id."""
        msg = Message()
        team_id = uuid.uuid4()
        msg.init(sender=None, team_id=team_id)
        assert msg.team_id == team_id

    def test_init_converts_string_team_id(self) -> None:
        """init() should convert string team_id to UUID."""
        msg = Message()
        team_id = uuid.uuid4()
        msg.init(sender=None, team_id=str(team_id))  # type: ignore[arg-type]
        assert msg.team_id == team_id

    def test_explicit_id(self) -> None:
        """Should allow explicit id to be set."""
        explicit_id = uuid.uuid4()
        msg = Message(id=explicit_id)
        assert msg.id == explicit_id

    def test_serialization_includes_model_marker(self) -> None:
        """model_dump() should include __model__ marker."""
        msg = Message()
        data = msg.model_dump()
        assert "__model__" in data
        assert "akgentic.core.messages.message.Message" in data["__model__"]


class TestStopRecursively:
    """Tests for StopRecursively dataclass."""

    def test_instantiation(self) -> None:
        """Should instantiate without arguments."""
        stop = StopRecursively()
        assert stop is not None

    def test_is_dataclass(self) -> None:
        """Should be a dataclass."""
        from dataclasses import is_dataclass

        assert is_dataclass(StopRecursively)


class TestUserMessage:
    """Tests for UserMessage class."""

    def test_display_type_is_human(self) -> None:
        """Should have display_type 'human'."""
        msg = UserMessage(content="Hello")
        assert msg.display_type == "human"

    def test_content_field(self) -> None:
        """Should store content."""
        msg = UserMessage(content="Test content")
        assert msg.content == "Test content"

    def test_inherits_message_fields(self) -> None:
        """Should inherit all Message fields."""
        msg = UserMessage(content="Hello")
        assert isinstance(msg.id, uuid.UUID)
        assert isinstance(msg.timestamp, datetime)
        assert msg.sender is None

    def test_serialization(self) -> None:
        """model_dump() should include all fields."""
        msg = UserMessage(content="Hello")
        data = msg.model_dump()
        assert data["content"] == "Hello"
        assert data["display_type"] == "human"
        assert "__model__" in data


class TestResultMessage:
    """Tests for ResultMessage class."""

    def test_display_type_is_ai(self) -> None:
        """Should have display_type 'ai'."""
        msg = ResultMessage(content="Response")
        assert msg.display_type == "ai"

    def test_content_field(self) -> None:
        """Should store content."""
        msg = ResultMessage(content="AI response")
        assert msg.content == "AI response"

    def test_inherits_message_fields(self) -> None:
        """Should inherit all Message fields."""
        msg = ResultMessage(content="Response")
        assert isinstance(msg.id, uuid.UUID)
        assert isinstance(msg.timestamp, datetime)


class TestSentMessage:
    """Tests for SentMessage orchestrator message."""

    def test_instantiation(self) -> None:
        """Should instantiate with message and recipient."""
        from akgentic.core.actor_address_impl import ActorAddressProxy

        inner_msg = Message()
        recipient = ActorAddressProxy(
            {
                "name": "test",
                "role": "Worker",
                "agent_id": str(uuid.uuid4()),
            }
        )
        sent = SentMessage(message=inner_msg, recipient=recipient)
        assert sent.message is inner_msg
        assert sent.recipient == recipient

    def test_inherits_message_fields(self) -> None:
        """Should inherit all Message fields."""
        from akgentic.core.actor_address_impl import ActorAddressProxy

        inner_msg = Message()
        recipient = ActorAddressProxy(
            {
                "name": "test",
                "role": "Worker",
                "agent_id": str(uuid.uuid4()),
            }
        )
        sent = SentMessage(message=inner_msg, recipient=recipient)
        assert isinstance(sent.id, uuid.UUID)


class TestReceivedMessage:
    """Tests for ReceivedMessage orchestrator message."""

    def test_instantiation(self) -> None:
        """Should instantiate with message_id."""
        msg_id = uuid.uuid4()
        received = ReceivedMessage(message_id=msg_id)
        assert received.message_id == msg_id

    def test_does_not_have_message_field(self) -> None:
        """Should not have a message field."""
        msg_id = uuid.uuid4()
        received = ReceivedMessage(message_id=msg_id)
        assert not hasattr(received, "message")


class TestProcessedMessage:
    """Tests for ProcessedMessage orchestrator message."""

    def test_instantiation(self) -> None:
        """Should instantiate with message_id."""
        msg_id = uuid.uuid4()
        processed = ProcessedMessage(message_id=msg_id)
        assert processed.message_id == msg_id


class TestStartMessage:
    """Tests for StartMessage orchestrator message."""

    def test_instantiation(self) -> None:
        """Should instantiate with config."""
        from akgentic.core.agent_config import BaseConfig

        config = BaseConfig(name="test", role="Worker")
        start = StartMessage(config=config)
        assert start.config == config

    def test_parent_defaults_to_none(self) -> None:
        """parent should default to None."""
        from akgentic.core.agent_config import BaseConfig

        config = BaseConfig(name="test", role="Worker")
        start = StartMessage(config=config)
        assert start.parent is None

    def test_parent_can_be_set(self) -> None:
        """parent can be explicitly set."""
        from akgentic.core.actor_address_impl import ActorAddressProxy
        from akgentic.core.agent_config import BaseConfig

        config = BaseConfig(name="test", role="Worker")
        parent = ActorAddressProxy(
            {
                "name": "parent",
                "role": "Parent",
                "agent_id": str(uuid.uuid4()),
            }
        )
        start = StartMessage(config=config, parent=parent)
        assert start.parent == parent


class TestStopMessage:
    """Tests for StopMessage orchestrator message."""

    def test_instantiation(self) -> None:
        """Should instantiate without additional fields."""
        stop = StopMessage()
        assert isinstance(stop.id, uuid.UUID)


class TestErrorMessage:
    """Tests for ErrorMessage orchestrator message."""

    def test_instantiation(self) -> None:
        """Should instantiate with exception details."""
        error = ErrorMessage(
            exception_type="ValueError",
            exception_value="Invalid input",
        )
        assert error.exception_type == "ValueError"
        assert error.exception_value == "Invalid input"

    def test_current_message_defaults_to_none(self) -> None:
        """current_message should default to None."""
        error = ErrorMessage(
            exception_type="Error",
            exception_value="msg",
        )
        assert error.current_message is None

    def test_current_message_can_be_set(self) -> None:
        """current_message can be explicitly set."""
        msg = Message()
        error = ErrorMessage(
            exception_type="Error",
            exception_value="msg",
            current_message=msg,
        )
        assert error.current_message is msg


class TestStateChangedMessage:
    """Tests for StateChangedMessage orchestrator message."""

    def test_instantiation(self) -> None:
        """Should instantiate with state."""
        from akgentic.core.agent_state import BaseState

        state = BaseState()
        state_changed = StateChangedMessage(state=state)
        assert state_changed.state == state
