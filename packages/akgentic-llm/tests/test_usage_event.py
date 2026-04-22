"""Tests for LlmUsageEvent data model and emission from ContextManager.

Story 4-1: LlmUsageEvent Data Model and Emission.
Covers AC-1 through AC-7.

Uses the same importlib loading pattern as test_tool_events.py to avoid
pulling in the providers dependency.
"""

import dataclasses
import importlib.util
from datetime import datetime
from pathlib import Path

import pytest
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    UserPromptPart,
)
from pydantic_ai.usage import RequestUsage

# Load context module via importlib to avoid providers transitive dependency
_CONTEXT_PATH = Path(__file__).parent.parent / "src" / "akgentic" / "llm" / "context.py"
_spec = importlib.util.spec_from_file_location("context", _CONTEXT_PATH)
_context_module = importlib.util.module_from_spec(_spec)  # type: ignore[arg-type]
_spec.loader.exec_module(_context_module)  # type: ignore[union-attr]

ContextManager = _context_module.ContextManager

# Import event classes directly — no providers dep in event.py
from akgentic.llm.event import (  # noqa: E402, I001
    LlmMessageEvent,
    LlmUsageEvent,
    ToolCallEvent,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class EventCapture:
    """Captures all domain events in emission order."""

    def __init__(self) -> None:
        self.events: list[object] = []

    def notify_event(self, event: object) -> None:
        """Append each received event for ordered assertion."""
        self.events.append(event)


def _make_manager_with_capture() -> tuple[ContextManager, EventCapture]:
    """Return a ContextManager wired to an EventCapture observer."""
    manager = ContextManager()
    capture = EventCapture()
    manager.subscribe(capture)
    return manager, capture


def _response_with_usage(
    model_name: str | None = "claude-sonnet-4-20250514",
    provider_name: str | None = "anthropic",
    run_id: str | None = "test-run-123",
    input_tokens: int = 100,
    output_tokens: int = 50,
    cache_read_tokens: int = 10,
    cache_write_tokens: int = 20,
) -> ModelResponse:
    """Create a ModelResponse with usage data."""
    return ModelResponse(
        parts=[TextPart(content="Hello")],
        model_name=model_name,
        provider_name=provider_name,
        run_id=run_id,
        timestamp=datetime.now(),
        usage=RequestUsage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_read_tokens=cache_read_tokens,
            cache_write_tokens=cache_write_tokens,
        ),
    )


# ---------------------------------------------------------------------------
# AC-1: LlmUsageEvent is a frozen dataclass with correct fields and types
# ---------------------------------------------------------------------------


class TestLlmUsageEventDataclass:
    """AC-1: LlmUsageEvent dataclass definition."""

    def test_is_dataclass(self) -> None:
        """LlmUsageEvent must be a dataclass."""
        assert dataclasses.is_dataclass(LlmUsageEvent)

    def test_is_frozen(self) -> None:
        """LlmUsageEvent must be immutable (frozen=True)."""
        event = LlmUsageEvent(
            run_id="r",
            model_name="m",
            provider_name="p",
            input_tokens=1,
            output_tokens=2,
            cache_read_tokens=3,
            cache_write_tokens=4,
            requests=1,
        )
        with pytest.raises(dataclasses.FrozenInstanceError):
            event.run_id = "other"  # type: ignore[misc]

    def test_correct_fields(self) -> None:
        """LlmUsageEvent must have exactly the specified fields."""
        field_names = {f.name for f in dataclasses.fields(LlmUsageEvent)}
        expected = {
            "run_id",
            "model_name",
            "provider_name",
            "input_tokens",
            "output_tokens",
            "cache_read_tokens",
            "cache_write_tokens",
            "requests",
        }
        assert field_names == expected

    def test_field_types(self) -> None:
        """LlmUsageEvent fields must have correct types."""
        fields = {f.name: f.type for f in dataclasses.fields(LlmUsageEvent)}
        assert fields["run_id"] == "str"
        assert fields["model_name"] == "str"
        assert fields["provider_name"] == "str"
        assert fields["input_tokens"] == "int"
        assert fields["output_tokens"] == "int"
        assert fields["cache_read_tokens"] == "int"
        assert fields["cache_write_tokens"] == "int"
        assert fields["requests"] == "int"


# ---------------------------------------------------------------------------
# AC-2: ModelResponse with usage emits LlmUsageEvent with correct fields
# ---------------------------------------------------------------------------


class TestUsageEventEmission:
    """AC-2: Emission on ModelResponse with usage."""

    def test_model_response_with_usage_emits_llm_usage_event(self) -> None:
        """ModelResponse with usage must emit LlmUsageEvent."""
        manager, capture = _make_manager_with_capture()
        msg = _response_with_usage()
        manager.add_message(msg)

        usage_events = [e for e in capture.events if isinstance(e, LlmUsageEvent)]
        assert len(usage_events) == 1

    def test_llm_usage_event_field_values(self) -> None:
        """LlmUsageEvent fields must match ModelResponse metadata and usage."""
        manager, capture = _make_manager_with_capture()
        msg = _response_with_usage(
            model_name="gpt-4o",
            provider_name="openai",
            run_id="run-456",
            input_tokens=200,
            output_tokens=100,
            cache_read_tokens=30,
            cache_write_tokens=40,
        )
        manager.add_message(msg)

        usage_events = [e for e in capture.events if isinstance(e, LlmUsageEvent)]
        assert len(usage_events) == 1
        event = usage_events[0]
        assert event.run_id == "run-456"
        assert event.model_name == "gpt-4o"
        assert event.provider_name == "openai"
        assert event.input_tokens == 200
        assert event.output_tokens == 100
        assert event.cache_read_tokens == 30
        assert event.cache_write_tokens == 40
        assert event.requests == 1  # RequestUsage.requests is always 1


# ---------------------------------------------------------------------------
# AC-3: Ordering guarantee — LlmMessageEvent -> tool events -> LlmUsageEvent
# ---------------------------------------------------------------------------


class TestUsageEventOrdering:
    """AC-3: LlmUsageEvent is emitted AFTER tool events."""

    def test_ordering_text_response(self) -> None:
        """For text response: LlmMessageEvent -> LlmUsageEvent."""
        manager, capture = _make_manager_with_capture()
        msg = _response_with_usage()
        manager.add_message(msg)

        assert len(capture.events) == 2
        assert isinstance(capture.events[0], LlmMessageEvent)
        assert isinstance(capture.events[1], LlmUsageEvent)

    def test_ordering_with_tool_calls(self) -> None:
        """For tool-call response: LlmMessageEvent -> ToolCallEvent -> LlmUsageEvent."""
        manager, capture = _make_manager_with_capture()
        msg = ModelResponse(
            parts=[ToolCallPart(tool_name="search", tool_call_id="call_1", args="{}")],
            model_name="claude-sonnet-4-20250514",
            provider_name="anthropic",
            run_id="test-run",
            timestamp=datetime.now(),
            usage=RequestUsage(input_tokens=50, output_tokens=25),
        )
        manager.add_message(msg)

        assert len(capture.events) == 3
        assert isinstance(capture.events[0], LlmMessageEvent)
        assert isinstance(capture.events[1], ToolCallEvent)
        assert isinstance(capture.events[2], LlmUsageEvent)


# ---------------------------------------------------------------------------
# AC-4: No emission for ModelRequest
# ---------------------------------------------------------------------------


class TestNoEmissionForModelRequest:
    """AC-4: ModelRequest does NOT emit LlmUsageEvent."""

    def test_model_request_does_not_emit_usage_event(self) -> None:
        """ModelRequest must not produce LlmUsageEvent."""
        manager, capture = _make_manager_with_capture()
        msg = ModelRequest(parts=[UserPromptPart(content="Hello")])
        manager.add_message(msg)

        usage_events = [e for e in capture.events if isinstance(e, LlmUsageEvent)]
        assert len(usage_events) == 0


# ---------------------------------------------------------------------------
# AC-5: No emission when usage is None — defensive guard
# (In practice, ModelResponse always has usage via default factory,
#  but the guard exists for safety)
# ---------------------------------------------------------------------------


class TestDefaultUsageStillEmits:
    """AC-5 related: ModelResponse with default RequestUsage() still emits."""

    def test_default_usage_all_zeros_still_emits(self) -> None:
        """ModelResponse with default RequestUsage() (all zeros) still emits LlmUsageEvent."""
        manager, capture = _make_manager_with_capture()
        msg = ModelResponse(
            parts=[TextPart(content="Hello")],
            timestamp=datetime.now(),
            # usage defaults to RequestUsage() — all zeros, but NOT None
        )
        manager.add_message(msg)

        usage_events = [e for e in capture.events if isinstance(e, LlmUsageEvent)]
        assert len(usage_events) == 1
        event = usage_events[0]
        assert event.input_tokens == 0
        assert event.output_tokens == 0
        assert event.cache_read_tokens == 0
        assert event.cache_write_tokens == 0
        assert event.requests == 1


# ---------------------------------------------------------------------------
# AC-2 (edge): Field mapping from ModelResponse metadata
# ---------------------------------------------------------------------------


class TestFieldMappingFromModelResponse:
    """LlmUsageEvent fields correctly map from ModelResponse metadata."""

    def test_fields_map_from_model_response(self) -> None:
        """model_name, provider_name, run_id map correctly."""
        manager, capture = _make_manager_with_capture()
        msg = _response_with_usage(
            model_name="test-model",
            provider_name="test-provider",
            run_id="test-run-id",
        )
        manager.add_message(msg)

        event = [e for e in capture.events if isinstance(e, LlmUsageEvent)][0]
        assert event.model_name == "test-model"
        assert event.provider_name == "test-provider"
        assert event.run_id == "test-run-id"

    def test_none_values_default_to_empty_string(self) -> None:
        """None values for model_name, provider_name, run_id default to empty string."""
        manager, capture = _make_manager_with_capture()
        msg = _response_with_usage(
            model_name=None,
            provider_name=None,
            run_id=None,
        )
        manager.add_message(msg)

        event = [e for e in capture.events if isinstance(e, LlmUsageEvent)][0]
        assert event.model_name == ""
        assert event.provider_name == ""
        assert event.run_id == ""


# ---------------------------------------------------------------------------
# AC-6: Public API export
# ---------------------------------------------------------------------------


class TestPublicApiExport:
    """AC-6: LlmUsageEvent importable from akgentic.llm."""

    def test_importable_from_akgentic_llm(self) -> None:
        """LlmUsageEvent must be importable from the top-level package."""
        import akgentic.llm as llm

        assert hasattr(llm, "LlmUsageEvent")

    def test_in_all(self) -> None:
        """LlmUsageEvent must appear in __all__."""
        import akgentic.llm as llm

        assert "LlmUsageEvent" in llm.__all__

    def test_importable_from_event_module(self) -> None:
        """LlmUsageEvent must be importable from akgentic.llm.event."""
        from akgentic.llm.event import LlmUsageEvent as EventLlmUsageEvent

        assert EventLlmUsageEvent is LlmUsageEvent

    def test_direct_import(self) -> None:
        """Direct named import from akgentic.llm must succeed."""
        from akgentic.llm import LlmUsageEvent as ImportedLlmUsageEvent

        assert ImportedLlmUsageEvent is LlmUsageEvent
