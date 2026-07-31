"""Tests for the output-model typed message protocol (Stories 2-1, 5-5).

Covers REPLY_PROTOCOLS wording/order, Request.message_type, the plain-string
(un-enumerated) recipient schema, and the prompt-carried reply protocol.
"""

from pydantic import ValidationError

from akgentic.agent.output_models import (
    REPLY_PROTOCOLS,
    Request,
    StructuredOutput,
)


class TestReplyProtocols:
    """Tests for REPLY_PROTOCOLS dict."""

    def test_all_five_types_present(self) -> None:
        """Should contain entries for all 5 message types."""
        expected = {"request", "response", "notification", "instruction", "acknowledgment"}
        assert set(REPLY_PROTOCOLS.keys()) == expected

    def test_order_matches_intent_sequence(self) -> None:
        """AC-5: entries ordered request, response, instruction, notification, acknowledgment."""
        assert list(REPLY_PROTOCOLS.keys()) == [
            "request",
            "response",
            "instruction",
            "notification",
            "acknowledgment",
        ]

    def test_carry_out_then_form(self) -> None:
        """AC-5: reply-directed entries use the 'carry out …, then …' phrasing."""
        assert REPLY_PROTOCOLS["request"].startswith("Carry out the task, then ")
        assert REPLY_PROTOCOLS["instruction"].startswith("Carry out the task, then ")

    def test_sender_placeholder_in_reply_directed_values(self) -> None:
        """request and instruction reference the sender via {sender}; others do not."""
        assert "{sender}" in REPLY_PROTOCOLS["request"]
        assert "{sender}" in REPLY_PROTOCOLS["instruction"]
        for msg_type in ("response", "notification", "acknowledgment"):
            assert "{sender}" not in REPLY_PROTOCOLS[msg_type]

    def test_format_sender_placeholder(self) -> None:
        """Should format {sender} placeholder correctly."""
        formatted = REPLY_PROTOCOLS["request"].format(sender="@Alice")
        assert "@Alice" in formatted
        assert "{sender}" not in formatted

    def test_no_reply_types_indicate_no_action(self) -> None:
        """AC-5: notification/acknowledgment no longer say 'Return an empty list.'."""
        assert "No reply is expected" in REPLY_PROTOCOLS["notification"]
        assert "No further action needed" in REPLY_PROTOCOLS["acknowledgment"]
        for msg_type in ("notification", "acknowledgment"):
            assert "Return an empty list" not in REPLY_PROTOCOLS[msg_type]


class TestPromptCarriedReplyProtocol:
    """AC-3: the reply protocol is carried in the prompt, not the output schema."""

    def test_request_prompt_prefix(self) -> None:
        """A request from @Manager composes the exact AC-3 prompt prefix."""
        sender = "@Manager"
        protocol = REPLY_PROTOCOLS["request"].format(sender=sender)
        prefix = f"You received a request from {sender}. {protocol}"
        assert prefix == (
            "You received a request from @Manager. "
            "Carry out the task, then respond to @Manager. You may also delegate to others."
        )


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

    def test_message_type_description_order(self) -> None:
        """AC-6: field description lists intents in REPLY_PROTOCOLS order."""
        description = Request.model_fields["message_type"].description
        assert description is not None
        positions = [
            description.index(f"'{intent}'")
            for intent in ("request", "response", "instruction", "notification", "acknowledgment")
        ]
        assert positions == sorted(positions)


class TestRecipientSchema:
    """AC-2: recipient is a plain string with no enum constraint."""

    def test_recipient_has_no_enum(self) -> None:
        """The generated JSON schema for recipient must be a plain string."""
        schema = Request.model_json_schema()
        recipient = schema["properties"]["recipient"]
        assert recipient["type"] == "string"
        assert "enum" not in recipient


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
