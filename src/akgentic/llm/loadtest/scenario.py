"""Scenario models and loader for the YAML-driven mock agent.

Implements ADR-007 §3 (YAML config). All models are fully Pydantic — no
``dict[str, Any]`` — so a scenario round-trips through serialization cleanly.
``ToolStub.args`` is a raw JSON string, mirroring ``ToolCallPart.args``.
"""

import os
from functools import cache
from typing import Any

import yaml
from pydantic import BaseModel, Field

# Environment variable consulted when the scenario is not pinned in config.
SCENARIO_ENV_VAR = "AKGENTIC_MOCK_SCENARIO"


class MatchSpec(BaseModel):
    """Optional input matcher for a state.

    A state is selected when every set field matches the inbound prompt:

    - ``contains``: case-insensitive substring test.
    - ``regex``: ``re.search`` against the prompt.
    - ``from_sender``: substring test for a sender marker (e.g. ``@Manager``).

    An empty ``MatchSpec`` matches any prompt.
    """

    contains: str | None = None
    regex: str | None = None
    from_sender: str | None = None


class ToolStub(BaseModel):
    """A simulated tool call. The real tool is never invoked."""

    name: str
    args: str = "{}"  # raw JSON string, mirrors ToolCallPart.args
    returns: str = ""


class ResponseRequest(BaseModel):
    """One routed message in a simulated ``StructuredOutput``."""

    message_type: str
    recipient: str
    message: str


class ResponseSpec(BaseModel):
    """The structured response a state emits as the final ``TextPart``."""

    messages: list[ResponseRequest] = Field(default_factory=list)
    text: str | None = None  # used when the caller's output_type is str


class ScenarioState(BaseModel):
    """A single Mealy-machine state for one agent."""

    id: str = "default"
    when: MatchSpec | None = None
    tools: list[ToolStub] = Field(default_factory=list)
    respond: ResponseSpec = Field(default_factory=ResponseSpec)
    latency_ms: int | None = None


class AgentScript(BaseModel):
    """The state machine for one named agent."""

    states: list[ScenarioState] = Field(default_factory=list)
    default: ScenarioState = Field(default_factory=ScenarioState)


class ScenarioConfig(BaseModel):
    """A full scenario: per-agent scripts plus scenario-wide defaults."""

    name: str
    default_latency_ms: int = 0
    agents: dict[str, AgentScript] = Field(default_factory=dict)


def _resolve_scenario_ref(config: Any) -> str:
    """Resolve the scenario reference for a mock run.

    Reads ``config.model_cfg.model`` first (the scenario path is smuggled
    through the model field), falling back to the ``AKGENTIC_MOCK_SCENARIO``
    environment variable.

    Raises:
        ValueError: When no reference can be resolved.
    """
    ref = getattr(getattr(config, "model_cfg", None), "model", None)
    if isinstance(ref, str) and ref:
        return ref
    env_ref = os.environ.get(SCENARIO_ENV_VAR)
    if env_ref:
        return env_ref
    raise ValueError(
        "No mock scenario resolved from config.model_cfg.model or "
        f"${SCENARIO_ENV_VAR}"
    )


@cache
def load_scenario(ref: str) -> ScenarioConfig:
    """Load and validate a scenario YAML file, cached by resolved path.

    Args:
        ref: Filesystem path to the scenario YAML.

    Returns:
        The parsed, validated ``ScenarioConfig``.
    """
    path = os.path.abspath(ref)
    with open(path, encoding="utf-8") as fh:
        raw = yaml.safe_load(fh)
    return ScenarioConfig.model_validate(raw)
