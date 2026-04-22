"""Tests for BaseAgent.get_usage_summary() (Story 7-1).

Verifies that get_usage_summary() queries the orchestrator for LlmUsageEvent
events, extracts payloads, and delegates to aggregate_usage().
"""

import uuid
from unittest.mock import MagicMock, patch

from akgentic.agent.agent import BaseAgent
from akgentic.agent.config import AgentConfig


# =============================================================================
# HELPERS
# =============================================================================


def _make_minimal_agent(name: str = "@TestAgent") -> BaseAgent:
    """Construct a BaseAgent-like object without the Pykka actor system.

    Creates a bare BaseAgent instance, bypassing the actor __init__, and
    sets up the minimal attributes needed for get_usage_summary() tests:
    - agent_id: fixed UUID for deterministic assertions
    - orchestrator_proxy_ask: MagicMock with chained .get_events().get()
    """
    agent: BaseAgent = object.__new__(BaseAgent)

    # Fixed UUID for deterministic assertions
    agent.agent_id = uuid.UUID("12345678-1234-5678-1234-567812345678")  # type: ignore[attr-defined]

    # Mock orchestrator proxy
    agent.orchestrator_proxy_ask = MagicMock()  # type: ignore[attr-defined]

    # Minimal config
    mock_config = MagicMock(spec=AgentConfig)
    mock_config.name = name
    agent.config = mock_config  # type: ignore[attr-defined]

    return agent


def _make_event_message(event: object) -> MagicMock:
    """Create a mock EventMessage with .event attribute."""
    msg = MagicMock()
    msg.event = event
    return msg


# =============================================================================
# AC-1: get_usage_summary() calls orchestrator with correct agent_id and
#        event_class, then calls aggregate_usage with extracted events
# =============================================================================


class TestGetUsageSummaryOrchestratorCall:
    """AC-1: Orchestrator queried with correct agent_id and event_class."""

    @patch("akgentic.agent.agent.aggregate_usage")
    def test_calls_orchestrator_with_correct_params(
        self, mock_aggregate: MagicMock
    ) -> None:
        """AC-1: get_events called with agent_id=str(self.agent_id) and event_class=LlmUsageEvent."""
        from akgentic.llm import LlmUsageEvent

        agent = _make_minimal_agent()
        agent.orchestrator_proxy_ask.get_events.return_value.get.return_value = []
        mock_aggregate.return_value = MagicMock()

        agent.get_usage_summary()

        agent.orchestrator_proxy_ask.get_events.assert_called_once_with(
            agent_id="12345678-1234-5678-1234-567812345678",
            event_class=LlmUsageEvent,
        )
        agent.orchestrator_proxy_ask.get_events.return_value.get.assert_called_once()


# =============================================================================
# AC-1 (cont): Extracts .event from each EventMessage
# =============================================================================


class TestGetUsageSummaryEventExtraction:
    """AC-1/AC-2: Extracts .event from each EventMessage and passes to aggregate_usage."""

    @patch("akgentic.agent.agent.aggregate_usage")
    def test_extracts_event_from_event_messages(
        self, mock_aggregate: MagicMock
    ) -> None:
        """AC-1: [e.event for e in events] passed to aggregate_usage."""
        agent = _make_minimal_agent()

        event1 = MagicMock(name="LlmUsageEvent1")
        event2 = MagicMock(name="LlmUsageEvent2")
        event_msgs = [_make_event_message(event1), _make_event_message(event2)]

        agent.orchestrator_proxy_ask.get_events.return_value.get.return_value = event_msgs
        mock_aggregate.return_value = MagicMock()

        agent.get_usage_summary()

        mock_aggregate.assert_called_once_with([event1, event2], by_run=False)


# =============================================================================
# AC-2: by_run=True forwarded to aggregate_usage
# =============================================================================


class TestGetUsageSummaryByRun:
    """AC-2: by_run flag forwarded to aggregate_usage."""

    @patch("akgentic.agent.agent.aggregate_usage")
    def test_by_run_true_forwarded(self, mock_aggregate: MagicMock) -> None:
        """AC-2: by_run=True passed through to aggregate_usage."""
        agent = _make_minimal_agent()
        agent.orchestrator_proxy_ask.get_events.return_value.get.return_value = []
        mock_aggregate.return_value = MagicMock()

        agent.get_usage_summary(by_run=True)

        mock_aggregate.assert_called_once_with([], by_run=True)

    @patch("akgentic.agent.agent.aggregate_usage")
    def test_by_run_false_default(self, mock_aggregate: MagicMock) -> None:
        """AC-2: by_run defaults to False."""
        agent = _make_minimal_agent()
        agent.orchestrator_proxy_ask.get_events.return_value.get.return_value = []
        mock_aggregate.return_value = MagicMock()

        agent.get_usage_summary()

        mock_aggregate.assert_called_once_with([], by_run=False)


# =============================================================================
# AC-3: Empty events returns AgentUsageSummary with all zeros
# =============================================================================


class TestGetUsageSummaryEmptyEvents:
    """AC-3: No LLM calls returns zeroed AgentUsageSummary."""

    @patch("akgentic.agent.agent.aggregate_usage")
    def test_empty_events_calls_aggregate_with_empty_list(
        self, mock_aggregate: MagicMock
    ) -> None:
        """AC-3: Empty event list passed to aggregate_usage."""
        agent = _make_minimal_agent()
        agent.orchestrator_proxy_ask.get_events.return_value.get.return_value = []
        mock_aggregate.return_value = MagicMock()

        agent.get_usage_summary()

        mock_aggregate.assert_called_once_with([], by_run=False)

    def test_empty_events_returns_zeroed_summary(self) -> None:
        """AC-3: Actual aggregate_usage([]) returns zeroed AgentUsageSummary."""
        from akgentic.llm import AgentUsageSummary, aggregate_usage

        result = aggregate_usage([], by_run=False)
        assert isinstance(result, AgentUsageSummary)
        assert result.total_input_tokens == 0
        assert result.total_output_tokens == 0
        assert result.total_requests == 0
        assert result.total_cost_usd == 0.0


# =============================================================================
# AC-4: Method callable via Pykka proxy (signature check)
# =============================================================================


class TestGetUsageSummaryProxyCallable:
    """AC-4: get_usage_summary is callable via Pykka proxy."""

    def test_method_exists_on_baseagent(self) -> None:
        """AC-4: BaseAgent has get_usage_summary method."""
        assert hasattr(BaseAgent, "get_usage_summary")
        assert callable(getattr(BaseAgent, "get_usage_summary"))

    def test_method_signature(self) -> None:
        """AC-4: get_usage_summary(self, by_run: bool = False) -> AgentUsageSummary."""
        import inspect

        from akgentic.llm import AgentUsageSummary

        sig = inspect.signature(BaseAgent.get_usage_summary)
        params = list(sig.parameters.keys())
        assert params == ["self", "by_run"]
        assert sig.parameters["by_run"].default is False
        assert sig.parameters["by_run"].annotation is bool
        assert sig.return_annotation is AgentUsageSummary

    @patch("akgentic.agent.agent.aggregate_usage")
    def test_returns_aggregate_usage_result(self, mock_aggregate: MagicMock) -> None:
        """AC-4: Method returns the result of aggregate_usage."""
        from akgentic.llm import AgentUsageSummary

        agent = _make_minimal_agent()
        agent.orchestrator_proxy_ask.get_events.return_value.get.return_value = []

        expected = AgentUsageSummary(
            by_model={},
            runs=[],
            total_input_tokens=0,
            total_output_tokens=0,
            total_cache_read_tokens=0,
            total_cache_write_tokens=0,
            total_requests=0,
            total_cost_usd=0.0,
        )
        mock_aggregate.return_value = expected

        result = agent.get_usage_summary()
        assert result is expected
