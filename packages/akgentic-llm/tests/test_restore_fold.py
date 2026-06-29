"""Replay-parity tests for the ordered-fold restore (Story 12-3, AC 8 & 9).

Drives a live ``ContextManager`` through messages + ``compact()`` / ``clear_context()``,
captures the emitted event stream with a recording observer, then folds that stream
through a fresh ``ReactAgent.restore_context`` and asserts the restored context matches
the live one. Live and replay share the one ``ContextManager.fold_compaction`` helper, so
parity holds by construction.

The synthetic ``[Conversation summary] …`` message carries an auto ``timestamp`` that
differs between the live fold and the replay fold, so full-context parity is asserted on a
timestamp-independent content projection; every retained (non-summary) message is the same
object instance in both paths and is checked by identity.

No ``akgentic.core`` import — the persisted-event wrapper is a stdlib ``SimpleNamespace``.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from pydantic_ai.messages import ModelMessage, ModelRequest, UserPromptPart

from akgentic.llm import ContextManager, ModelConfig, ReactAgent, ReactAgentConfig
from akgentic.llm.event import LlmContextCompactedEvent


class _Recorder:
    """Observer that records every emitted domain event in order."""

    def __init__(self) -> None:
        self.events: list[Any] = []

    def notify_event(self, event: object) -> None:
        self.events.append(event)


def _user(text: str) -> ModelRequest:
    return ModelRequest(parts=[UserPromptPart(content=text)])


def _compacted(summary: str, replaced: int) -> LlmContextCompactedEvent:
    # Count-based fold belongs to ``sliding_window`` after 12-7; ``summarize`` full-folds.
    return LlmContextCompactedEvent(
        run_id=None,
        strategy_id="sliding_window",
        summary=summary,
        replaced_message_count=replaced,
        summarizer_prompt_version="v1",
        tokens_before=None,
        tokens_after=None,
    )


def _stream(recorder: _Recorder) -> list[SimpleNamespace]:
    """Wrap recorded events as persisted-event stand-ins exposing ``.event``."""
    return [SimpleNamespace(event=e) for e in recorder.events]


def _shape(messages: list[ModelMessage]) -> list[tuple[str, tuple[tuple[str, Any], ...]]]:
    """Timestamp-independent projection: (message type, ((part type, content), ...))."""
    return [
        (
            type(m).__name__,
            tuple((type(p).__name__, getattr(p, "content", None)) for p in m.parts),
        )
        for m in messages
    ]


def _fresh_agent() -> ReactAgent:
    return ReactAgent(
        config=ReactAgentConfig(model_cfg=ModelConfig(provider="openai", model="gpt-4o"))
    )


def test_compact_then_continue_replays_identically() -> None:
    """AC 8: a live session that compacts then continues replays to an identical context."""
    live = ContextManager()
    recorder = _Recorder()
    live.subscribe(recorder)

    m1, m2, m3, m4 = _user("m1"), _user("m2"), _user("m3"), _user("m4")
    for m in (m1, m2, m3, m4):
        live.add_message(m)
    live.compact(_compacted("SUMMARY-A", replaced=2))  # folds m1+m2
    m5 = _user("m5")
    live.add_message(m5)

    agent = _fresh_agent()
    agent.restore_context(_stream(recorder))

    restored = agent.context.messages
    assert _shape(restored) == _shape(live.messages)
    # The synthetic summary leads; retained originals are the very same objects.
    assert restored[0].parts[0].content == "[Conversation summary] SUMMARY-A"
    assert restored[1] is m3
    assert restored[2] is m4
    assert restored[3] is m5


def test_two_sequential_compactions_compose_no_double_fold() -> None:
    """AC 8: a later compaction consumes the earlier summary; prior rows untouched, fold once."""
    live = ContextManager()
    recorder = _Recorder()
    live.subscribe(recorder)

    m1, m2, m3, m4 = _user("m1"), _user("m2"), _user("m3"), _user("m4")
    for m in (m1, m2, m3, m4):
        live.add_message(m)
    live.compact(_compacted("SUMMARY-A", replaced=2))  # -> [sumA, m3, m4]
    m5, m6 = _user("m5"), _user("m6")
    live.add_message(m5)
    live.add_message(m6)
    live.compact(_compacted("SUMMARY-B", replaced=2))  # folds sumA + m3 -> [sumB, m4, m5, m6]

    agent = _fresh_agent()
    agent.restore_context(_stream(recorder))

    restored = agent.context.messages
    assert _shape(restored) == _shape(live.messages)
    assert len(restored) == 4
    assert restored[0].parts[0].content == "[Conversation summary] SUMMARY-B"
    # Rows after the fold point are the untouched originals (fold applied exactly once).
    assert restored[1] is m4
    assert restored[2] is m5
    assert restored[3] is m6


def test_cleared_event_rebuilds_identical_post_clear_context() -> None:
    """AC 9: an LlmContextClearedEvent in the stream rebuilds an identical post-clear context."""
    live = ContextManager()
    recorder = _Recorder()
    live.subscribe(recorder)

    live.add_message(_user("m1"))
    live.add_message(_user("m2"))
    live.clear_context()  # wipes to empty
    m3 = _user("fresh after clear")
    live.add_message(m3)

    agent = _fresh_agent()
    agent.restore_context(_stream(recorder))

    restored = agent.context.messages
    # Only the post-clear message survives in both paths (same object instance).
    assert restored == [m3]
    assert _shape(restored) == _shape(live.messages)


def test_clear_event_resets_then_message_after_is_kept() -> None:
    """AC 9: the clear watermark resets the accumulator; only later messages remain."""
    recorder = _Recorder()
    live = ContextManager()
    live.subscribe(recorder)
    live.add_message(_user("a"))
    live.clear_context()

    agent = _fresh_agent()
    agent.restore_context(_stream(recorder))

    assert agent.context.messages == []
    assert live.messages == []
