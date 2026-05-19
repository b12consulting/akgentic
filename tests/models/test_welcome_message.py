"""Tests for the WelcomeMessage type and its public-API export."""

from __future__ import annotations

import pytest
from akgentic.core.messages.message import Message
from akgentic.core.utils.deserializer import deserialize_object
from pydantic import ValidationError

import akgentic.team
from akgentic.team.messages import WelcomeMessage


class TestWelcomeMessage:
    """Tests for the WelcomeMessage message class."""

    def test_is_message_subclass(self) -> None:
        """WelcomeMessage is a subclass of the core Message type."""
        assert issubclass(WelcomeMessage, Message)

    def test_display_type_defaults_to_other(self) -> None:
        """display_type defaults to 'other' — the greeting is a synthetic system
        announcement, not an AI turn (see ADR-17 amendment 2026-05-18)."""
        msg = WelcomeMessage(content="hi")
        assert msg.display_type == "other"

    def test_content_is_required(self) -> None:
        """content is a required field — omitting it raises ValidationError."""
        with pytest.raises(ValidationError):
            WelcomeMessage()  # type: ignore[call-arg]

    def test_display_type_can_be_overridden(self) -> None:
        """display_type accepts any value of the three-value Literal."""
        msg = WelcomeMessage(content="hi", display_type="ai")
        assert msg.display_type == "ai"

    def test_round_trip_via_deserialize_object(self) -> None:
        """A WelcomeMessage round-trips through model_dump / deserialize_object.

        Polymorphic reconstruction via the __model__ tag must restore the
        concrete WelcomeMessage type and all fields identically.
        """
        original = WelcomeMessage(content="Welcome to the team!")
        data = original.model_dump()
        restored = deserialize_object(data)
        assert type(restored) is WelcomeMessage
        assert restored == original
        assert restored.content == "Welcome to the team!"
        assert restored.display_type == "other"
        assert restored.id == original.id


class TestWelcomeMessageExport:
    """Tests for the public-API export of WelcomeMessage."""

    def test_importable_from_package(self) -> None:
        """WelcomeMessage is importable from the akgentic.team package."""
        from akgentic.team import WelcomeMessage as ExportedWelcomeMessage

        assert ExportedWelcomeMessage is WelcomeMessage

    def test_present_in_all(self) -> None:
        """WelcomeMessage is listed in akgentic.team.__all__."""
        assert "WelcomeMessage" in akgentic.team.__all__
