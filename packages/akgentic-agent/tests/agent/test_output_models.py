"""Tests for output models typed message protocol (Story 2-1).

Tests REPLY_PROTOCOLS, Request.message_type, and structured_output template.
"""

from pydantic import ValidationError

from akgentic.agent.output_models import (
    REPLY_PROTOCOLS,
    Request,
    StructuredOutput,
    structured_output,
)


class TestReplyProtocols:
    """Tests for REPLY_PROTOCOLS dict."""

    def test_all_five_types_present(self) -> None:
        """Should contain entries for all 5 message types."""
        expected = {"request", "response", "notification", "instruction", "acknowledgment"}
        assert set(REPLY_PROTOCOLS.keys()) == expected

    def test_sender_placeholder_in_all_values(self) -> None:
        """All protocol entries that reference sender should contain {sender} placeholder."""
        for msg_type in ("request", "notification", "instruction"):
            assert "{sender}" in REPLY_PROTOCOLS[msg_type], f"{msg_type} missing {{sender}}"

    def test_format_sender_placeholder(self) -> None:
        """Should format {sender} placeholder correctly."""
        formatted = REPLY_PROTOCOLS["request"].format(sender="@Alice")
        assert "@Alice" in formatted
        assert "{sender}" not in formatted

    def test_no_reply_types_indicate_no_action(self) -> None:
        """Notification and acknowledgment should instruct no reply."""
        assert "Do NOT reply" in REPLY_PROTOCOLS["notification"]
        assert "No further action" in REPLY_PROTOCOLS["acknowledgment"]


class TestRequestMessageType:
    """Tests for Request.message_type field."""

    def test_message_type_is_required(self) -> None:
        """Should raise ValidationError when message_type is missing."""
        try:
            Request(message="hello", recipient="@Bob")  # type: ignore[call-arg]
            assert False, "Should have raised ValidationError"
        except ValidationError:
            pass

    def test_accepts_all_five_types(self) -> None:
        """Should accept all 5 valid message types."""
        for msg_type in ("request", "response", "notification", "instruction", "acknowledgment"):
            req = Request(message="hello", recipient="@Bob", message_type=msg_type)  # type: ignore[arg-type]
            assert req.message_type == msg_type

    def test_rejects_invalid_type(self) -> None:
        """Should reject invalid message_type values."""
        try:
            Request(message="hello", recipient="@Bob", message_type="invalid")  # type: ignore[arg-type]
            assert False, "Should have raised ValidationError"
        except ValidationError:
            pass

    def test_request_serialization_includes_message_type(self) -> None:
        """Should include message_type in serialized output."""
        req = Request(message="do this", recipient="@Dev", message_type="instruction")
        data = req.model_dump()
        assert data["message_type"] == "instruction"


class TestStructuredOutputTemplate:
    """Tests for structured_output template string."""

    def test_contains_message_type_placeholder(self) -> None:
        """Template should contain {message_type} placeholder."""
        assert "{message_type}" in structured_output

    def test_contains_reply_protocol_placeholder(self) -> None:
        """Template should contain {reply_protocol} placeholder."""
        assert "{reply_protocol}" in structured_output

    def test_contains_sender_placeholder(self) -> None:
        """Template should contain {sender} placeholder."""
        assert "{sender}" in structured_output

    def test_contains_team_placeholder(self) -> None:
        """Template should contain {team} placeholder."""
        assert "{team}" in structured_output

    def test_contains_roles_placeholder(self) -> None:
        """Template should contain {roles} placeholder."""
        assert "{roles}" in structured_output

    def test_full_format(self) -> None:
        """Should format all placeholders without error."""
        result = structured_output.format(
            sender="@Alice",
            message_type="request",
            reply_protocol=REPLY_PROTOCOLS["request"].format(sender="@Alice"),
            team="@Bob, @Carol",
            roles="Developer, Tester",
        )
        assert "@Alice" in result
        assert "request" in result
        assert "@Bob" in result
        assert "Developer" in result


class TestStructuredOutputModel:
    """Tests for StructuredOutput Pydantic model."""

    def test_default_empty_messages(self) -> None:
        """Should default to empty messages list."""
        output = StructuredOutput()
        assert output.messages == []

    def test_messages_with_typed_requests(self) -> None:
        """Should accept Request objects with message_type."""
        req = Request(message="do this", recipient="@Dev", message_type="instruction")
        output = StructuredOutput(messages=[req])
        assert len(output.messages) == 1
        assert output.messages[0].message_type == "instruction"
