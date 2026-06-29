"""Sibling-free shape tests for the context-compaction events (AC 5, 6, 7).

Imports only ``dataclasses``, ``pytest``, and ``akgentic.llm.event`` — no akgentic
sibling (not even ``akgentic.core``) and no ``pydantic_ai.messages.ModelMessage`` (NFR1).
"""

import dataclasses

import pytest

from akgentic.llm.event import LlmContextClearedEvent, LlmContextCompactedEvent

# Primitive annotation strings permitted on the compaction events. event.py uses
# ``from __future__ import annotations``, so dataclass field types are strings.
_PRIMITIVE_ANNOTATIONS = {"str", "int", "str | None", "int | None"}


class TestLlmContextCompactedEvent:
    """LlmContextCompactedEvent shape — AC 5, 7."""

    def test_fields_in_order(self):
        """Fields appear in the exact AC 5 order."""
        names = [f.name for f in dataclasses.fields(LlmContextCompactedEvent)]
        assert names == [
            "run_id",
            "strategy_id",
            "summary",
            "replaced_message_count",
            "summarizer_prompt_version",
            "tokens_before",
            "tokens_after",
        ]

    def test_constructs_with_documented_fields(self):
        """Constructs with the documented fields; run_id/token fields accept None."""
        event = LlmContextCompactedEvent(
            run_id="run-1",
            strategy_id="summarize",
            summary="folded history",
            replaced_message_count=3,
            summarizer_prompt_version="v1",
            tokens_before=1000,
            tokens_after=200,
        )
        assert event.run_id == "run-1"
        assert event.replaced_message_count == 3
        nulled = LlmContextCompactedEvent(
            run_id=None,
            strategy_id="summarize",
            summary="",
            replaced_message_count=0,
            summarizer_prompt_version="v1",
            tokens_before=None,
            tokens_after=None,
        )
        assert nulled.run_id is None
        assert nulled.tokens_before is None
        assert nulled.tokens_after is None

    def test_is_frozen(self):
        """Assigning to any field raises FrozenInstanceError."""
        event = LlmContextCompactedEvent(
            run_id="run-1",
            strategy_id="summarize",
            summary="folded history",
            replaced_message_count=3,
            summarizer_prompt_version="v1",
            tokens_before=1000,
            tokens_after=200,
        )
        with pytest.raises(dataclasses.FrozenInstanceError):
            event.summary = "mutated"  # type: ignore[misc]

    def test_annotations_are_primitive_only(self):
        """Every field annotation is a primitive — no ModelMessage / sibling type."""
        for field in dataclasses.fields(LlmContextCompactedEvent):
            assert "ModelMessage" not in str(field.type)
            assert field.type in _PRIMITIVE_ANNOTATIONS


class TestLlmContextClearedEvent:
    """LlmContextClearedEvent shape — AC 6, 7."""

    def test_fields_in_order(self):
        """Fields appear in the exact AC 6 order."""
        names = [f.name for f in dataclasses.fields(LlmContextClearedEvent)]
        assert names == ["run_id", "cleared_message_count"]

    def test_constructs_with_documented_fields(self):
        """Constructs with the documented fields; run_id accepts None."""
        event = LlmContextClearedEvent(run_id="run-1", cleared_message_count=5)
        assert event.run_id == "run-1"
        assert event.cleared_message_count == 5
        assert LlmContextClearedEvent(run_id=None, cleared_message_count=0).run_id is None

    def test_is_frozen(self):
        """Assigning to any field raises FrozenInstanceError."""
        event = LlmContextClearedEvent(run_id="run-1", cleared_message_count=5)
        with pytest.raises(dataclasses.FrozenInstanceError):
            event.cleared_message_count = 9  # type: ignore[misc]

    def test_annotations_are_primitive_only(self):
        """Every field annotation is a primitive — no ModelMessage / sibling type."""
        for field in dataclasses.fields(LlmContextClearedEvent):
            assert "ModelMessage" not in str(field.type)
            assert field.type in _PRIMITIVE_ANNOTATIONS
