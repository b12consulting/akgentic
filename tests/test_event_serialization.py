"""Tests for LlmMessageEvent serialization round-trip.

Verifies that LlmMessageEvent (a plain dataclass wrapping pydantic-ai
ModelMessage types) survives serialize → deserialize_object round-trip
with all nested dataclass types preserved (not flattened to dicts).

Regression: the core serializer's plain-dataclass branch used
``dataclasses.asdict()`` which recursively flattens nested dataclasses
into plain dicts *before* ``serialize()`` can tag them with ``__model__``.
This causes deserialization to reconstruct the outer event but leave
inner ModelRequest/ModelResponse as raw dicts.
"""

from datetime import datetime, timezone

from akgentic.core.utils.deserializer import deserialize_object
from akgentic.core.utils.serializer import serialize
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    SystemPromptPart,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.usage import RequestUsage

from akgentic.llm.event import LlmMessageEvent


def _make_user_request(content: str = "Hello") -> ModelRequest:
    return ModelRequest(
        parts=[UserPromptPart(content=content)],
        timestamp=datetime.now(timezone.utc),
    )


def _make_system_request(content: str = "You are helpful.") -> ModelRequest:
    return ModelRequest(
        parts=[SystemPromptPart(content=content)],
        timestamp=datetime.now(timezone.utc),
    )


def _make_response(content: str = "Hi there!") -> ModelResponse:
    return ModelResponse(
        parts=[TextPart(content=content)],
        timestamp=datetime.now(timezone.utc),
        usage=RequestUsage(input_tokens=10, output_tokens=5),
    )


def _make_tool_call_response() -> ModelResponse:
    return ModelResponse(
        parts=[
            ToolCallPart(
                tool_name="search",
                args='{"query": "test"}',
                tool_call_id="call-1",
            ),
        ],
        timestamp=datetime.now(timezone.utc),
        usage=RequestUsage(input_tokens=15, output_tokens=8),
    )


def _make_tool_return_request() -> ModelRequest:
    return ModelRequest(
        parts=[
            ToolReturnPart(
                tool_name="search",
                content="result data",
                tool_call_id="call-1",
            ),
        ],
        timestamp=datetime.now(timezone.utc),
    )


class TestLlmMessageEventSerialization:
    """Round-trip serialization of LlmMessageEvent with various ModelMessage types."""

    def test_user_request_round_trip(self) -> None:
        """UserPromptPart inside ModelRequest survives round-trip."""
        original = LlmMessageEvent(message=_make_user_request("Hello world"))

        serialized = serialize(original)
        restored = deserialize_object(serialized)

        assert isinstance(restored, LlmMessageEvent)
        assert isinstance(restored.message, ModelRequest)
        assert len(restored.message.parts) == 1
        assert isinstance(restored.message.parts[0], UserPromptPart)
        assert restored.message.parts[0].content == "Hello world"

    def test_system_prompt_round_trip(self) -> None:
        """SystemPromptPart inside ModelRequest survives round-trip."""
        original = LlmMessageEvent(message=_make_system_request("Be concise."))

        serialized = serialize(original)
        restored = deserialize_object(serialized)

        assert isinstance(restored, LlmMessageEvent)
        assert isinstance(restored.message, ModelRequest)
        assert isinstance(restored.message.parts[0], SystemPromptPart)
        assert restored.message.parts[0].content == "Be concise."

    def test_text_response_round_trip(self) -> None:
        """TextPart inside ModelResponse survives round-trip."""
        original = LlmMessageEvent(message=_make_response("Answer"))

        serialized = serialize(original)
        restored = deserialize_object(serialized)

        assert isinstance(restored, LlmMessageEvent)
        assert isinstance(restored.message, ModelResponse)
        assert isinstance(restored.message.parts[0], TextPart)
        assert restored.message.parts[0].content == "Answer"

    def test_tool_call_round_trip(self) -> None:
        """ToolCallPart inside ModelResponse survives round-trip."""
        original = LlmMessageEvent(message=_make_tool_call_response())

        serialized = serialize(original)
        restored = deserialize_object(serialized)

        assert isinstance(restored, LlmMessageEvent)
        assert isinstance(restored.message, ModelResponse)
        part = restored.message.parts[0]
        assert isinstance(part, ToolCallPart)
        assert part.tool_name == "search"
        assert part.tool_call_id == "call-1"

    def test_tool_return_round_trip(self) -> None:
        """ToolReturnPart inside ModelRequest survives round-trip."""
        original = LlmMessageEvent(message=_make_tool_return_request())

        serialized = serialize(original)
        restored = deserialize_object(serialized)

        assert isinstance(restored, LlmMessageEvent)
        assert isinstance(restored.message, ModelRequest)
        part = restored.message.parts[0]
        assert isinstance(part, ToolReturnPart)
        assert part.tool_name == "search"
        assert part.content == "result data"

    def test_multi_part_request_round_trip(self) -> None:
        """ModelRequest with multiple part types survives round-trip."""
        original = LlmMessageEvent(
            message=ModelRequest(
                parts=[
                    SystemPromptPart(content="System instructions"),
                    UserPromptPart(content="User question"),
                ],
                timestamp=datetime.now(timezone.utc),
            )
        )

        serialized = serialize(original)
        restored = deserialize_object(serialized)

        assert isinstance(restored, LlmMessageEvent)
        assert isinstance(restored.message, ModelRequest)
        assert len(restored.message.parts) == 2
        assert isinstance(restored.message.parts[0], SystemPromptPart)
        assert isinstance(restored.message.parts[1], UserPromptPart)

    def test_timestamp_preserved(self) -> None:
        """Timestamps on ModelRequest/ModelResponse survive round-trip.

        pydantic-ai declares ModelRequest.timestamp as ``datetime | None``
        and ModelResponse.timestamp as ``datetime`` — the deserialized value
        must be a datetime, not an ISO string.
        """
        ts = datetime(2026, 3, 24, 12, 0, 0, tzinfo=timezone.utc)
        original = LlmMessageEvent(
            message=ModelRequest(
                parts=[UserPromptPart(content="test")],
                timestamp=ts,
            )
        )

        serialized = serialize(original)
        restored = deserialize_object(serialized)

        assert isinstance(restored, LlmMessageEvent)
        assert isinstance(restored.message, ModelRequest)
        assert isinstance(restored.message.timestamp, datetime)
        assert restored.message.timestamp == ts

    def test_usage_preserved_on_response(self) -> None:
        """RequestUsage on ModelResponse survives round-trip."""
        original = LlmMessageEvent(message=_make_response())

        serialized = serialize(original)
        restored = deserialize_object(serialized)

        assert isinstance(restored, LlmMessageEvent)
        assert isinstance(restored.message, ModelResponse)
        assert restored.message.usage.input_tokens == 10
        assert restored.message.usage.output_tokens == 5

    def test_serialized_has_model_tags(self) -> None:
        """Serialized output has __model__ tags for both outer event and inner message."""
        original = LlmMessageEvent(message=_make_user_request())
        serialized = serialize(original)

        assert isinstance(serialized, dict)
        assert "__model__" in serialized
        assert "akgentic.llm.event.LlmMessageEvent" in serialized["__model__"]

        # The inner message must also have a __model__ tag (not be a plain dict)
        inner = serialized["message"]
        assert isinstance(inner, dict)
        assert "__model__" in inner, (
            "Inner ModelRequest lost its __model__ tag — "
            "asdict() flattened it before serialize() could tag it"
        )
