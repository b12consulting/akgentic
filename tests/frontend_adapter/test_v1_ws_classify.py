"""Unit tests — V1 WS envelope type classification.

Reclassified from integration/test_spec_frontend.py (TestLlmContextEnvelope)
and integration/test_adr004_v1_compat.py (TestWebSocketErrorEnvelope).
These tests call _classify_envelope_type and wrap_event directly — no real app needed.
"""

from __future__ import annotations


class TestLlmContextEnvelope:
    """Verify the V1 WS handler emits llm_context envelope type."""

    def test_classify_envelope_type_for_context_changed(self) -> None:
        """_classify_envelope_type returns 'llm_context' for ContextChangedMessage."""
        from akgentic.infra.server.routes.frontend_adapter.angular_v1.ws import (
            _classify_envelope_type,
        )

        # _classify_envelope_type checks type(event).__name__ == "ContextChangedMessage"
        class ContextChangedMessage:  # noqa: N801
            """Fake message whose class name matches the real ContextChangedMessage."""

            def __init__(self) -> None:
                self.id = "test-id"
                self.sender = None

        result = _classify_envelope_type(ContextChangedMessage())  # type: ignore[arg-type]
        assert result == "llm_context"

    def test_classify_envelope_returns_message_for_user_message(self) -> None:
        """_classify_envelope_type returns 'message' for UserMessage."""
        from akgentic.core.messages.message import UserMessage

        from akgentic.infra.server.routes.frontend_adapter.angular_v1.ws import (
            _classify_envelope_type,
        )

        msg = UserMessage(content="hello")
        result = _classify_envelope_type(msg)
        assert result == "message"


class TestWebSocketErrorEnvelope:
    """Verify WebSocket error envelope classification and wrapping.

    Reclassified from integration/test_adr004_v1_compat.py — these tests call
    _classify_envelope_type and wrap_event directly, no real app needed.
    """

    def test_classify_error_message_returns_error(self) -> None:
        """_classify_envelope_type(ErrorMessage) returns 'error'."""
        from akgentic.core.messages.orchestrator import ErrorMessage

        from akgentic.infra.server.routes.frontend_adapter.angular_v1.ws import (
            _classify_envelope_type,
        )

        msg = ErrorMessage(exception_value="test error", exception_type="ValueError")
        result = _classify_envelope_type(msg)
        assert result == "error"

    def test_wrap_event_error_produces_error_payload(self) -> None:
        """wrap_event with ErrorMessage produces ErrorPayload with type=='error'."""
        from akgentic.core.messages.orchestrator import ErrorMessage

        from akgentic.infra.server.routes.frontend_adapter import ErrorPayload
        from akgentic.infra.server.routes.frontend_adapter.angular_v1.ws import (
            wrap_event,
        )

        msg = ErrorMessage(exception_value="something broke", exception_type="RuntimeError")
        wrapped = wrap_event(msg)
        assert isinstance(wrapped.payload, ErrorPayload)
        assert wrapped.payload.type == "error"
        assert wrapped.payload.message == "something broke"
