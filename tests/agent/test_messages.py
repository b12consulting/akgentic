"""Tests for AgentMessage typed message protocol (Story 2-1).

Tests the 5-type message protocol on AgentMessage: request, response,
notification, instruction, acknowledgment.
"""

from pydantic import ValidationError

from akgentic.agent.messages import AgentMessage


class TestAgentMessageType:
    """Tests for AgentMessage.type field."""

    def test_default_type_is_request(self) -> None:
        """Should default to 'request' for backward compatibility."""
        msg = AgentMessage(content="hello")
        assert msg.type == "request"

    def test_explicit_request(self) -> None:
        """Should accept explicit 'request' type."""
        msg = AgentMessage(content="do this", type="request")
        assert msg.type == "request"

    def test_response_type(self) -> None:
        """Should accept 'response' type."""
        msg = AgentMessage(content="done", type="response")
        assert msg.type == "response"

    def test_notification_type(self) -> None:
        """Should accept 'notification' type."""
        msg = AgentMessage(content="fyi", type="notification")
        assert msg.type == "notification"

    def test_instruction_type(self) -> None:
        """Should accept 'instruction' type."""
        msg = AgentMessage(content="do X", type="instruction")
        assert msg.type == "instruction"

    def test_acknowledgment_type(self) -> None:
        """Should accept 'acknowledgment' type."""
        msg = AgentMessage(content="ack", type="acknowledgment")
        assert msg.type == "acknowledgment"

    def test_invalid_type_rejected(self) -> None:
        """Should reject invalid type values."""
        try:
            AgentMessage(content="hello", type="invalid")  # type: ignore[arg-type]
            assert False, "Should have raised ValidationError"
        except ValidationError:
            pass

    def test_type_preserved_in_serialization(self) -> None:
        """Should preserve type through serialization round-trip."""
        msg = AgentMessage(content="hello", type="instruction")
        data = msg.model_dump()
        restored = AgentMessage.model_validate(data)
        assert restored.type == "instruction"
