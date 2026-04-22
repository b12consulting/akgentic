"""Round-trip validation tests for fixture factories.

Every factory must produce a dict that the original model can validate
back into a model instance via ``Model.model_validate()`` (Pydantic) or
direct dataclass construction (for frozen dataclasses).
"""

from __future__ import annotations

from akgentic.core.messages.orchestrator import (
    ErrorMessage,
    EventMessage,
    ProcessedMessage,
    ReceivedMessage,
    SentMessage,
    StartMessage,
)
from akgentic.llm.event import LlmUsageEvent, ToolCallEvent, ToolReturnEvent

from akgentic.infra.cli.client import EventInfo, TeamInfo
from tests.fixtures.events import (
    make_error_message,
    make_event_message,
    make_llm_usage_event,
    make_processed_message,
    make_received_message,
    make_sent_message,
    make_start_message,
    make_tool_call_event,
    make_tool_return_event,
)
from tests.fixtures.models import make_event_info, make_team_info

# ---------------------------------------------------------------------------
# Core event round-trip tests — zero overrides
# ---------------------------------------------------------------------------


class TestSentMessageFactory:
    """Round-trip tests for make_sent_message."""

    def test_round_trip_defaults(self) -> None:
        data = make_sent_message()
        model = SentMessage.model_validate(data)
        assert model.message is not None
        assert model.recipient is not None

    def test_round_trip_with_overrides(self) -> None:
        data = make_sent_message(content="custom content")
        model = SentMessage.model_validate(data)
        assert model.message is not None

    def test_override_appears_in_output(self) -> None:
        data = make_sent_message(content="hello world")
        # content is on the inner message
        assert data["message"]["content"] == "hello world"


class TestEventMessageFactory:
    """Round-trip tests for make_event_message."""

    def test_round_trip_defaults(self) -> None:
        data = make_event_message()
        model = EventMessage.model_validate(data)
        assert model.event is not None

    def test_round_trip_with_overrides(self) -> None:
        custom_event = {"type": "custom", "payload": 42}
        data = make_event_message(event=custom_event)
        model = EventMessage.model_validate(data)
        assert model.event == custom_event

    def test_override_appears_in_output(self) -> None:
        data = make_event_message(event={"x": 1})
        assert data["event"] == {"x": 1}


class TestErrorMessageFactory:
    """Round-trip tests for make_error_message."""

    def test_round_trip_defaults(self) -> None:
        data = make_error_message()
        model = ErrorMessage.model_validate(data)
        assert model.exception_type == "ValueError"

    def test_round_trip_with_overrides(self) -> None:
        data = make_error_message(exception_type="RuntimeError", exception_value="boom")
        model = ErrorMessage.model_validate(data)
        assert model.exception_type == "RuntimeError"
        assert model.exception_value == "boom"

    def test_override_appears_in_output(self) -> None:
        data = make_error_message(exception_type="KeyError")
        assert data["exception_type"] == "KeyError"


class TestStartMessageFactory:
    """Round-trip tests for make_start_message."""

    def test_round_trip_defaults(self) -> None:
        data = make_start_message()
        model = StartMessage.model_validate(data)
        assert model.config is not None

    def test_round_trip_with_overrides(self) -> None:
        from akgentic.core.agent_config import BaseConfig

        custom_cfg = BaseConfig(name="custom-agent", role="custom")
        data = make_start_message(config=custom_cfg)
        model = StartMessage.model_validate(data)
        assert model.config.name == "custom-agent"

    def test_override_appears_in_output(self) -> None:
        from akgentic.core.agent_config import BaseConfig

        custom_cfg = BaseConfig(name="overridden-agent", role="custom")
        data = make_start_message(config=custom_cfg)
        assert data["config"]["name"] == "overridden-agent"


class TestReceivedMessageFactory:
    """Round-trip tests for make_received_message."""

    def test_round_trip_defaults(self) -> None:
        data = make_received_message()
        model = ReceivedMessage.model_validate(data)
        assert model.message_id is not None

    def test_round_trip_with_overrides(self) -> None:
        import uuid

        custom_id = uuid.uuid4()
        data = make_received_message(message_id=custom_id)
        model = ReceivedMessage.model_validate(data)
        assert model.message_id == custom_id

    def test_override_appears_in_output(self) -> None:
        import uuid

        custom_id = uuid.uuid4()
        data = make_received_message(message_id=custom_id)
        assert data["message_id"] == str(custom_id)


class TestProcessedMessageFactory:
    """Round-trip tests for make_processed_message."""

    def test_round_trip_defaults(self) -> None:
        data = make_processed_message()
        model = ProcessedMessage.model_validate(data)
        assert model.message_id is not None

    def test_round_trip_with_overrides(self) -> None:
        import uuid

        custom_id = uuid.uuid4()
        data = make_processed_message(message_id=custom_id)
        model = ProcessedMessage.model_validate(data)
        assert model.message_id == custom_id

    def test_override_appears_in_output(self) -> None:
        import uuid

        custom_id = uuid.uuid4()
        data = make_processed_message(message_id=custom_id)
        assert data["message_id"] == str(custom_id)


# ---------------------------------------------------------------------------
# LLM event round-trip tests
# ---------------------------------------------------------------------------


class TestToolCallEventFactory:
    """Round-trip tests for make_tool_call_event."""

    def test_round_trip_defaults(self) -> None:
        data = make_tool_call_event()
        fields = {k: v for k, v in data.items() if k != "__model__"}
        event = ToolCallEvent(**fields)
        assert event.tool_name == "test_tool"

    def test_round_trip_with_overrides(self) -> None:
        data = make_tool_call_event(tool_name="search", arguments='{"q": "hello"}')
        fields = {k: v for k, v in data.items() if k != "__model__"}
        event = ToolCallEvent(**fields)
        assert event.tool_name == "search"
        assert event.arguments == '{"q": "hello"}'

    def test_override_appears_in_output(self) -> None:
        data = make_tool_call_event(tool_name="my_tool")
        assert data["tool_name"] == "my_tool"

    def test_model_tag_present(self) -> None:
        data = make_tool_call_event()
        assert data["__model__"] == "akgentic.llm.event.ToolCallEvent"


class TestToolReturnEventFactory:
    """Round-trip tests for make_tool_return_event."""

    def test_round_trip_defaults(self) -> None:
        data = make_tool_return_event()
        fields = {k: v for k, v in data.items() if k != "__model__"}
        event = ToolReturnEvent(**fields)
        assert event.success is True

    def test_round_trip_with_overrides(self) -> None:
        data = make_tool_return_event(success=False)
        fields = {k: v for k, v in data.items() if k != "__model__"}
        event = ToolReturnEvent(**fields)
        assert event.success is False

    def test_override_appears_in_output(self) -> None:
        data = make_tool_return_event(success=False)
        assert data["success"] is False

    def test_model_tag_present(self) -> None:
        data = make_tool_return_event()
        assert data["__model__"] == "akgentic.llm.event.ToolReturnEvent"


class TestLlmUsageEventFactory:
    """Round-trip tests for make_llm_usage_event."""

    def test_round_trip_defaults(self) -> None:
        data = make_llm_usage_event()
        event = LlmUsageEvent(**data)
        assert event.input_tokens == 100
        assert event.output_tokens == 50

    def test_round_trip_with_overrides(self) -> None:
        data = make_llm_usage_event(input_tokens=500, model_name="gpt-4")
        event = LlmUsageEvent(**data)
        assert event.input_tokens == 500
        assert event.model_name == "gpt-4"

    def test_override_appears_in_output(self) -> None:
        data = make_llm_usage_event(requests=3)
        assert data["requests"] == 3


# ---------------------------------------------------------------------------
# CLI model round-trip tests
# ---------------------------------------------------------------------------


class TestTeamInfoFactory:
    """Round-trip tests for make_team_info."""

    def test_round_trip_defaults(self) -> None:
        data = make_team_info()
        model = TeamInfo.model_validate(data)
        assert model.team_id == "team-001"

    def test_round_trip_with_overrides(self) -> None:
        data = make_team_info(name="Custom Team", status="stopped")
        model = TeamInfo.model_validate(data)
        assert model.name == "Custom Team"
        assert model.status == "stopped"

    def test_override_appears_in_output(self) -> None:
        data = make_team_info(name="My Team")
        assert data["name"] == "My Team"


class TestEventInfoFactory:
    """Round-trip tests for make_event_info."""

    def test_round_trip_defaults(self) -> None:
        data = make_event_info()
        model = EventInfo.model_validate(data)
        assert model.sequence == 1

    def test_round_trip_with_overrides(self) -> None:
        data = make_event_info(sequence=42, team_id="team-xyz")
        model = EventInfo.model_validate(data)
        assert model.sequence == 42
        assert model.team_id == "team-xyz"

    def test_override_appears_in_output(self) -> None:
        data = make_event_info(sequence=10)
        assert data["sequence"] == 10
