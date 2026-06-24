"""Tests for LlmSystemPromptEvent emission, dedup, and seed from ContextManager.

Covers Story 6-1 ACs: first-run emission, dedup on unchanged rendering,
emission on changed rendering, no-system-parts suppression, stable content hash,
the seed seam, and public-API export.

Note: Uses the same importlib loading pattern as test_tool_events.py /
test_context.py to avoid pulling in the providers transitive dependency.
The event dataclasses are imported directly from akgentic.llm.event because
event.py has no providers dependency.
"""

import importlib.util
from pathlib import Path

from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    SystemPromptPart,
    TextPart,
    UserPromptPart,
)

# Load context module via importlib to avoid providers transitive dependency
_CONTEXT_PATH = Path(__file__).parent.parent / "src" / "akgentic" / "llm" / "context.py"
_spec = importlib.util.spec_from_file_location("context", _CONTEXT_PATH)
_context_module = importlib.util.module_from_spec(_spec)  # type: ignore[arg-type]
_spec.loader.exec_module(_context_module)  # type: ignore[union-attr]

ContextManager = _context_module.ContextManager

# Import event classes directly — no providers dep in event.py
from akgentic.llm.event import (  # noqa: E402, I001
    LlmSystemPromptEvent,
    SystemPromptPartSnapshot,
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


def _make_manager_with_capture() -> tuple["ContextManager", EventCapture]:
    """Return a ContextManager wired to an EventCapture observer."""
    manager = ContextManager()
    capture = EventCapture()
    manager.subscribe(capture)
    return manager, capture


def _system_request(
    *parts: tuple[str | None, str],
    user: str | None = "Hello",
) -> ModelRequest:
    """Build a ModelRequest with the given (dynamic_ref, content) system parts.

    Optionally appends a UserPromptPart to mirror a realistic first request.
    """
    request_parts: list[object] = [
        SystemPromptPart(content=content, dynamic_ref=dynamic_ref)
        for dynamic_ref, content in parts
    ]
    if user is not None:
        request_parts.append(UserPromptPart(content=user))
    return ModelRequest(parts=request_parts)  # type: ignore[arg-type]


def _system_events(capture: EventCapture) -> list[LlmSystemPromptEvent]:
    """Filter captured events down to LlmSystemPromptEvent instances."""
    return [e for e in capture.events if isinstance(e, LlmSystemPromptEvent)]


def _first_hash(capture: EventCapture) -> str:
    """Return the content_hash of the first captured LlmSystemPromptEvent."""
    return _system_events(capture)[0].content_hash


# ---------------------------------------------------------------------------
# AC-2: first-run emission
# ---------------------------------------------------------------------------


class TestFirstRunEmission:
    """AC-2: first record_system_prompt emits one event mirroring system parts."""

    def test_first_run_emits_single_event(self) -> None:
        """Exactly one LlmSystemPromptEvent emitted on the first call."""
        manager, capture = _make_manager_with_capture()
        manager.add_message(
            _system_request(
                ("agent_backstory", "You are a helpful agent."),
                ("current_date", "Today is 2026-06-13."),
                ("GetTeamRoster", "#GetTeamRoster: Alice, Bob"),
            )
        )
        capture.events.clear()  # drop the add_message LlmMessageEvent

        manager.record_system_prompt("run-1")

        events = _system_events(capture)
        assert len(events) == 1

    def test_first_run_event_run_id(self) -> None:
        """The emitted event carries the supplied run_id."""
        manager, capture = _make_manager_with_capture()
        manager.add_message(_system_request(("agent_backstory", "Backstory.")))

        manager.record_system_prompt("run-1")

        events = _system_events(capture)
        assert events[0].run_id == "run-1"

    def test_first_run_parts_mirror_model_order(self) -> None:
        """parts mirror the system parts in model order with matching fields."""
        manager, capture = _make_manager_with_capture()
        manager.add_message(
            _system_request(
                ("agent_backstory", "Helpful agent."),
                ("current_date", "Today is 2026-06-13."),
                (None, "Static notice."),
            )
        )

        manager.record_system_prompt("run-1")

        event = _system_events(capture)[0]
        assert event.parts == (
            SystemPromptPartSnapshot(dynamic_ref="agent_backstory", content="Helpful agent."),
            SystemPromptPartSnapshot(dynamic_ref="current_date", content="Today is 2026-06-13."),
            SystemPromptPartSnapshot(dynamic_ref=None, content="Static notice."),
        )

    def test_first_run_sets_last_hash(self) -> None:
        """_last_system_prompt_hash is updated to a non-empty hash after emission."""
        manager, capture = _make_manager_with_capture()
        manager.add_message(_system_request(("agent_backstory", "Backstory.")))

        manager.record_system_prompt("run-1")

        event = _system_events(capture)[0]
        assert event.content_hash
        assert manager._last_system_prompt_hash == event.content_hash


# ---------------------------------------------------------------------------
# AC-3: dedup on unchanged rendering
# ---------------------------------------------------------------------------


class TestDedupUnchanged:
    """AC-3: unchanged rendering on a subsequent run emits nothing."""

    def test_unchanged_rendering_emits_nothing(self) -> None:
        """Second record on identical parts emits no new event."""
        manager, capture = _make_manager_with_capture()
        manager.add_message(
            _system_request(
                ("agent_backstory", "Backstory."),
                ("current_date", "Today is 2026-06-13."),
            )
        )
        manager.record_system_prompt("run-1")
        first_count = len(_system_events(capture))

        manager.record_system_prompt("run-2")

        assert len(_system_events(capture)) == first_count == 1


# ---------------------------------------------------------------------------
# AC-4: emission on changed rendering
# ---------------------------------------------------------------------------


class TestEmissionOnChange:
    """AC-4: a changed rendering emits one new event with a different hash."""

    def test_changed_part_emits_new_event_with_new_hash(self) -> None:
        """Mutating a part's content in place emits exactly one new event."""
        manager, capture = _make_manager_with_capture()
        first_request = _system_request(
            ("agent_backstory", "Backstory."),
            ("GetTeamRoster", "#GetTeamRoster: Alice"),
        )
        manager.add_message(first_request)
        manager.record_system_prompt("run-1")
        first_hash = _system_events(capture)[0].content_hash

        # Simulate pydantic-ai's in-place re-evaluation of the roster block.
        first_request.parts[1].content = "#GetTeamRoster: Alice, Bob"  # type: ignore[union-attr]
        manager.record_system_prompt("run-2")

        events = _system_events(capture)
        assert len(events) == 2
        assert events[1].run_id == "run-2"
        assert events[1].content_hash != first_hash
        assert manager._last_system_prompt_hash == events[1].content_hash


# ---------------------------------------------------------------------------
# AC-5: no system parts ⇒ no event
# ---------------------------------------------------------------------------


class TestNoSystemParts:
    """AC-5: no system parts (or empty context) emits nothing."""

    def test_empty_context_emits_nothing(self) -> None:
        """record_system_prompt on an empty context emits no event."""
        manager, capture = _make_manager_with_capture()

        manager.record_system_prompt("run-1")

        assert _system_events(capture) == []

    def test_first_request_without_system_parts_emits_nothing(self) -> None:
        """A first ModelRequest with only a UserPromptPart emits no event."""
        manager, capture = _make_manager_with_capture()
        manager.add_message(ModelRequest(parts=[UserPromptPart(content="Hi")]))

        manager.record_system_prompt("run-1")

        assert _system_events(capture) == []

    def test_response_only_context_emits_nothing(self) -> None:
        """A context whose first (and only) message is a ModelResponse emits nothing."""
        manager, capture = _make_manager_with_capture()
        manager.add_message(ModelResponse(parts=[TextPart(content="Hi")]))

        manager.record_system_prompt("run-1")

        assert _system_events(capture) == []


# ---------------------------------------------------------------------------
# AC-6: stable content hash
# ---------------------------------------------------------------------------


class TestStableContentHash:
    """AC-6: content_hash is a stable, order-sensitive sha256 hex digest."""

    def test_same_parts_same_hash(self) -> None:
        """Identical part sequences across managers yield identical hashes."""
        manager_a, capture_a = _make_manager_with_capture()
        manager_b, capture_b = _make_manager_with_capture()
        manager_a.add_message(_system_request(("a", "one"), ("b", "two")))
        manager_b.add_message(_system_request(("a", "one"), ("b", "two")))

        manager_a.record_system_prompt("run-1")
        manager_b.record_system_prompt("run-1")

        assert _first_hash(capture_a) == _first_hash(capture_b)

    def test_hash_is_sha256_hex(self) -> None:
        """content_hash is a 64-char lowercase hex digest (sha256)."""
        manager, capture = _make_manager_with_capture()
        manager.add_message(_system_request(("a", "one")))

        manager.record_system_prompt("run-1")

        digest = _system_events(capture)[0].content_hash
        assert len(digest) == 64
        assert all(c in "0123456789abcdef" for c in digest)

    def test_reordering_changes_hash(self) -> None:
        """Reordering the parts yields a different hash."""
        manager_a, capture_a = _make_manager_with_capture()
        manager_b, capture_b = _make_manager_with_capture()
        manager_a.add_message(_system_request(("a", "one"), ("b", "two")))
        manager_b.add_message(_system_request(("b", "two"), ("a", "one")))

        manager_a.record_system_prompt("run-1")
        manager_b.record_system_prompt("run-1")

        assert _first_hash(capture_a) != _first_hash(capture_b)

    def test_changing_dynamic_ref_changes_hash(self) -> None:
        """Changing only dynamic_ref (content equal) yields a different hash."""
        manager_a, capture_a = _make_manager_with_capture()
        manager_b, capture_b = _make_manager_with_capture()
        manager_a.add_message(_system_request(("ref_x", "same")))
        manager_b.add_message(_system_request(("ref_y", "same")))

        manager_a.record_system_prompt("run-1")
        manager_b.record_system_prompt("run-1")

        assert _first_hash(capture_a) != _first_hash(capture_b)

    def test_none_ref_distinct_from_empty_ref(self) -> None:
        """dynamic_ref=None and dynamic_ref='' produce different hashes."""
        manager_a, capture_a = _make_manager_with_capture()
        manager_b, capture_b = _make_manager_with_capture()
        manager_a.add_message(_system_request((None, "same")))
        manager_b.add_message(_system_request(("", "same")))

        manager_a.record_system_prompt("run-1")
        manager_b.record_system_prompt("run-1")

        assert _first_hash(capture_a) != _first_hash(capture_b)

    def test_part_boundary_unambiguous(self) -> None:
        """Concatenation ambiguity across parts cannot collide on the same hash."""
        manager_a, capture_a = _make_manager_with_capture()
        manager_b, capture_b = _make_manager_with_capture()
        manager_a.add_message(_system_request((None, "ab"), (None, "c")))
        manager_b.add_message(_system_request((None, "a"), (None, "bc")))

        manager_a.record_system_prompt("run-1")
        manager_b.record_system_prompt("run-1")

        assert _first_hash(capture_a) != _first_hash(capture_b)


# ---------------------------------------------------------------------------
# AC-7: seed seam suppresses re-emission
# ---------------------------------------------------------------------------


class TestSeedSeam:
    """AC-7: seed_system_prompt_hash seeds dedup state without notifying."""

    def test_seed_emits_nothing(self) -> None:
        """The seed call itself fires no observer event."""
        manager, capture = _make_manager_with_capture()

        manager.seed_system_prompt_hash("deadbeef")

        assert capture.events == []
        assert manager._last_system_prompt_hash == "deadbeef"

    def test_seed_suppresses_unchanged_reemission(self) -> None:
        """Seeding the current rendering's hash suppresses re-emission."""
        manager, capture = _make_manager_with_capture()
        manager.add_message(
            _system_request(
                ("agent_backstory", "Backstory."),
                ("current_date", "Today is 2026-06-13."),
            )
        )
        # Compute the rendering's hash via a throwaway emission, then reset.
        manager.record_system_prompt("warm-up")
        current_hash = _system_events(capture)[0].content_hash
        capture.events.clear()

        manager.seed_system_prompt_hash(current_hash)
        manager.record_system_prompt("run-1")

        assert _system_events(capture) == []

    def test_seed_none_allows_emission(self) -> None:
        """Seeding None resets dedup state so the next record emits."""
        manager, capture = _make_manager_with_capture()
        manager.add_message(_system_request(("agent_backstory", "Backstory.")))
        manager.record_system_prompt("run-1")
        capture.events.clear()

        manager.seed_system_prompt_hash(None)
        manager.record_system_prompt("run-2")

        assert len(_system_events(capture)) == 1


# ---------------------------------------------------------------------------
# AC-8: public API export
# ---------------------------------------------------------------------------


class TestPublicApiExport:
    """AC-8: both new types importable from akgentic.llm and in __all__."""

    def test_event_importable_from_akgentic_llm(self) -> None:
        """LlmSystemPromptEvent importable from the top-level package."""
        from akgentic.llm import LlmSystemPromptEvent as ImportedEvent

        assert ImportedEvent is LlmSystemPromptEvent

    def test_snapshot_importable_from_akgentic_llm(self) -> None:
        """SystemPromptPartSnapshot importable from the top-level package."""
        from akgentic.llm import SystemPromptPartSnapshot as ImportedSnapshot

        assert ImportedSnapshot is SystemPromptPartSnapshot

    def test_both_in_all(self) -> None:
        """Both names appear in akgentic.llm.__all__."""
        import akgentic.llm as llm

        assert "LlmSystemPromptEvent" in llm.__all__
        assert "SystemPromptPartSnapshot" in llm.__all__


# ---------------------------------------------------------------------------
# Dataclass properties: frozen=True
# ---------------------------------------------------------------------------


class TestEventDataclassProperties:
    """Verify the new event dataclasses are frozen (immutable)."""

    def test_event_is_frozen(self) -> None:
        """LlmSystemPromptEvent must be immutable (frozen=True)."""
        import dataclasses

        import pytest

        event = LlmSystemPromptEvent(run_id="r", parts=(), content_hash="h")
        with pytest.raises(dataclasses.FrozenInstanceError):
            event.run_id = "other"  # type: ignore[misc]

    def test_snapshot_is_frozen(self) -> None:
        """SystemPromptPartSnapshot must be immutable (frozen=True)."""
        import dataclasses

        import pytest

        snap = SystemPromptPartSnapshot(dynamic_ref="ref", content="body")
        with pytest.raises(dataclasses.FrozenInstanceError):
            snap.content = "other"  # type: ignore[misc]
