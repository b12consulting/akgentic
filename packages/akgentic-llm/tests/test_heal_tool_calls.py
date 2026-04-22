"""Tests for ReactAgent._heal_unprocessed_tool_calls().

Covers Story 5.1 / ADR-003: When the REACT loop fails mid-execution, any
``ModelResponse`` whose ``ToolCallPart`` entries never received results is
healed by appending a ``ModelRequest`` with matching ``ToolReturnPart``
entries. This prevents the 'unprocessed tool calls' error on the next
``run()``.

Tests invoke ``_heal_unprocessed_tool_calls()`` directly on a constructed
``ReactAgent`` so the healing logic can be exercised without a real LLM
round-trip.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime
from unittest.mock import patch

import pytest
from pydantic_ai.exceptions import UsageLimitExceeded
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)

from akgentic.llm import (
    LlmMessageEvent,
    ModelConfig,
    ReactAgent,
    ReactAgentConfig,
    ToolReturnEvent,
    UsageLimitError,
)

RUN_ID = uuid.UUID("cf92c35f-4ee9-4cff-8361-b8ce3827e021")


class _EventCapture:
    """Capture all domain events emitted on a ContextManager."""

    def __init__(self) -> None:
        self.events: list[object] = []

    def notify_event(self, event: object) -> None:
        self.events.append(event)


def _make_agent() -> ReactAgent:
    """Construct a ReactAgent with a minimal offline config.

    The underlying pydantic-ai Agent is never invoked by these tests — we
    call ``_heal_unprocessed_tool_calls()`` directly on a manually populated
    context.
    """
    config = ReactAgentConfig(model_cfg=ModelConfig(provider="openai", model="gpt-4o"))
    return ReactAgent(config=config)


def _response_with_tool_calls(*call_specs: tuple[str, str]) -> ModelResponse:
    """Build a ModelResponse with the given (tool_name, tool_call_id) tuples."""
    return ModelResponse(
        parts=[
            ToolCallPart(tool_name=name, tool_call_id=call_id, args="{}")
            for name, call_id in call_specs
        ],
        timestamp=datetime.now(),
        run_id=RUN_ID,
    )


# ---------------------------------------------------------------------------
# AC-1 / AC-2: healing appends ModelRequest with ToolReturnParts
# ---------------------------------------------------------------------------


class TestHealingAppendsToolReturns:
    """AC-1 / AC-2: Last message is ModelResponse with tool calls → heal."""

    def test_appends_model_request_with_one_tool_return_per_call(self) -> None:
        """Single tool call is healed with a single ToolReturnPart."""
        agent = _make_agent()
        agent._context.add_message(_response_with_tool_calls(("my_tool", "call_xyz")))

        before = len(agent._context.messages)
        agent._heal_unprocessed_tool_calls("test error")

        messages = agent._context.messages
        assert len(messages) == before + 1
        healed = messages[-1]
        assert isinstance(healed, ModelRequest)
        tool_returns = [p for p in healed.parts if isinstance(p, ToolReturnPart)]
        assert len(tool_returns) == 1

    def test_tool_return_matches_tool_call_fields(self) -> None:
        """AC-1/AC-2: Each ToolReturnPart mirrors the ToolCallPart's name and id."""
        agent = _make_agent()
        agent._context.add_message(_response_with_tool_calls(("search", "call_001")))

        agent._heal_unprocessed_tool_calls("boom")

        healed = agent._context.messages[-1]
        assert isinstance(healed, ModelRequest)
        part = healed.parts[0]
        assert isinstance(part, ToolReturnPart)
        assert part.tool_name == "search"
        assert part.tool_call_id == "call_001"

    def test_tool_return_content_includes_error_detail(self) -> None:
        """AC-1/AC-2: ToolReturnPart.content carries the error detail string."""
        agent = _make_agent()
        agent._context.add_message(_response_with_tool_calls(("search", "call_001")))

        agent._heal_unprocessed_tool_calls("RuntimeError: deep trace")

        healed = agent._context.messages[-1]
        assert isinstance(healed, ModelRequest)
        part = healed.parts[0]
        assert isinstance(part, ToolReturnPart)
        assert "RuntimeError: deep trace" in str(part.content)


# ---------------------------------------------------------------------------
# AC-3: no-op conditions
# ---------------------------------------------------------------------------


class TestNoOpConditions:
    """AC-3: Healing is a no-op when there is nothing to heal."""

    def test_noop_when_context_empty(self) -> None:
        """Empty context → no message appended."""
        agent = _make_agent()
        assert agent._context.messages == []

        agent._heal_unprocessed_tool_calls("error")

        assert agent._context.messages == []

    def test_noop_when_last_message_is_model_request(self) -> None:
        """Last message is ModelRequest → no heal."""
        agent = _make_agent()
        agent._context.add_message(ModelRequest(parts=[UserPromptPart(content="hi")]))

        before = len(agent._context.messages)
        agent._heal_unprocessed_tool_calls("error")

        assert len(agent._context.messages) == before

    def test_noop_when_last_response_has_no_tool_calls(self) -> None:
        """Last ModelResponse is text-only → no heal."""
        agent = _make_agent()
        agent._context.add_message(
            ModelResponse(
                parts=[TextPart(content="just text")],
                timestamp=datetime.now(),
                run_id=RUN_ID,
            )
        )

        before = len(agent._context.messages)
        agent._heal_unprocessed_tool_calls("error")

        assert len(agent._context.messages) == before


# ---------------------------------------------------------------------------
# AC-4: warning log
# ---------------------------------------------------------------------------


class TestWarningLog:
    """AC-4: A WARNING is logged with the count of healed tool calls."""

    def test_warning_logged_with_count(self, caplog: pytest.LogCaptureFixture) -> None:
        """Emits WARNING-level log 'Healing %d unprocessed tool call(s) after error'."""
        agent = _make_agent()
        agent._context.add_message(
            _response_with_tool_calls(("t1", "c1"), ("t2", "c2"), ("t3", "c3"))
        )

        with caplog.at_level(logging.WARNING, logger="akgentic.llm.agent"):
            agent._heal_unprocessed_tool_calls("error")

        warning_records = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert any(
            "Healing 3 unprocessed tool call(s) after error" in r.getMessage()
            for r in warning_records
        )

    def test_no_warning_when_noop(self, caplog: pytest.LogCaptureFixture) -> None:
        """No warning emitted when there is nothing to heal."""
        agent = _make_agent()

        with caplog.at_level(logging.WARNING, logger="akgentic.llm.agent"):
            agent._heal_unprocessed_tool_calls("error")

        assert not any(
            "Healing" in r.getMessage()
            for r in caplog.records
            if r.levelno == logging.WARNING
        )


# ---------------------------------------------------------------------------
# AC-1: parallel tool calls — all healed
# ---------------------------------------------------------------------------


class TestParallelToolCalls:
    """AC-1: Multiple pending tool calls are all healed in one ModelRequest."""

    def test_multiple_calls_all_healed(self) -> None:
        """Parallel tool-use: one ToolReturnPart per pending ToolCallPart."""
        agent = _make_agent()
        agent._context.add_message(
            _response_with_tool_calls(
                ("alpha", "call_a"),
                ("beta", "call_b"),
                ("gamma", "call_c"),
            )
        )

        agent._heal_unprocessed_tool_calls("error")

        healed = agent._context.messages[-1]
        assert isinstance(healed, ModelRequest)
        tool_returns = [p for p in healed.parts if isinstance(p, ToolReturnPart)]
        assert len(tool_returns) == 3
        healed_ids = {p.tool_call_id for p in tool_returns}
        assert healed_ids == {"call_a", "call_b", "call_c"}
        healed_names = {p.tool_name for p in tool_returns}
        assert healed_names == {"alpha", "beta", "gamma"}


# ---------------------------------------------------------------------------
# AC-5: observer events emitted for healing message
# ---------------------------------------------------------------------------


class TestObserverEventsOnHeal:
    """AC-5: Healing goes through add_message() so observer events fire."""

    def test_llm_message_event_and_tool_return_events_emitted(self) -> None:
        """LlmMessageEvent emitted once, one ToolReturnEvent per healed part.

        ContextManager maps every ``ToolReturnPart`` to
        ``ToolReturnEvent(success=True)`` — the flag reflects part kind, not
        tool outcome — so the test asserts on presence, counts, and identity
        fields only, not on ``success``.
        """
        agent = _make_agent()
        capture = _EventCapture()
        agent.subscribe_context(capture)

        agent._context.add_message(
            _response_with_tool_calls(("t1", "c1"), ("t2", "c2"))
        )
        # Drain events from adding the ModelResponse so we only inspect heal-time events.
        capture.events.clear()

        agent._heal_unprocessed_tool_calls("error")

        llm_events = [e for e in capture.events if isinstance(e, LlmMessageEvent)]
        assert len(llm_events) == 1
        healed_msg = llm_events[0].message
        assert isinstance(healed_msg, ModelRequest)

        tool_return_events = [e for e in capture.events if isinstance(e, ToolReturnEvent)]
        assert len(tool_return_events) == 2
        assert {e.tool_call_id for e in tool_return_events} == {"c1", "c2"}
        assert {e.tool_name for e in tool_return_events} == {"t1", "t2"}


# ---------------------------------------------------------------------------
# AC-1 / AC-2: healing is invoked by run() exception handlers
# ---------------------------------------------------------------------------


class _RaisingRun:
    """Async context manager whose __aenter__ raises the configured error."""

    def __init__(self, error: BaseException) -> None:
        self._error = error

    async def __aenter__(self) -> "_RaisingRun":
        raise self._error

    async def __aexit__(self, exc_type: object, exc_val: object, exc_tb: object) -> bool:
        return False


class TestRunInvokesHealing:
    """Run-level integration: exception handlers call the healing method."""

    @pytest.mark.asyncio
    async def test_usage_limit_exceeded_heals_pending_tool_calls(self) -> None:
        """AC-1: UsageLimitExceeded handler heals context and still raises."""
        agent = _make_agent()
        agent._context.add_message(_response_with_tool_calls(("tool_a", "call_a")))
        before = len(agent._context.messages)

        raising = _RaisingRun(UsageLimitExceeded("Request limit exceeded"))
        with patch.object(agent._pydantic_agent, "iter", return_value=raising):
            with pytest.raises(UsageLimitError):
                await agent.run("test")

        messages = agent._context.messages
        assert len(messages) == before + 1
        healed = messages[-1]
        assert isinstance(healed, ModelRequest)
        assert any(isinstance(p, ToolReturnPart) for p in healed.parts)

    @pytest.mark.asyncio
    async def test_generic_exception_heals_and_reraises(self) -> None:
        """AC-2: generic Exception handler heals context and re-raises unchanged."""
        agent = _make_agent()
        agent._context.add_message(_response_with_tool_calls(("tool_b", "call_b")))
        before = len(agent._context.messages)

        class MyBoom(Exception):
            pass

        raising = _RaisingRun(MyBoom("kaboom"))
        with patch.object(agent._pydantic_agent, "iter", return_value=raising):
            with pytest.raises(MyBoom, match="kaboom"):
                await agent.run("test")

        messages = agent._context.messages
        assert len(messages) == before + 1
        healed = messages[-1]
        assert isinstance(healed, ModelRequest)
        tool_returns = [p for p in healed.parts if isinstance(p, ToolReturnPart)]
        assert len(tool_returns) == 1
        assert tool_returns[0].tool_name == "tool_b"
        assert tool_returns[0].tool_call_id == "call_b"
