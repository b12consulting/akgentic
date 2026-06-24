"""Load-testing support: a token-free mock for ``ReactAgent``.

Gated behind the optional ``loadtest`` extra. Importing this package brings in
``pyyaml``; install with ``pip install akgentic-llm[loadtest]``.

See ADR-007 (akgentic-llm) for the design.
"""

from akgentic.llm.loadtest.mock_agent import MockProviderReachedError, MockReactAgent
from akgentic.llm.loadtest.scenario import (
    SCENARIO_ENV_VAR,
    AgentScript,
    MatchSpec,
    ResponseRequest,
    ResponseSpec,
    ScenarioConfig,
    ScenarioState,
    ToolStub,
    load_scenario,
)

__all__ = [
    "MockReactAgent",
    "MockProviderReachedError",
    "ScenarioConfig",
    "AgentScript",
    "ScenarioState",
    "MatchSpec",
    "ToolStub",
    "ResponseSpec",
    "ResponseRequest",
    "load_scenario",
    "SCENARIO_ENV_VAR",
]
