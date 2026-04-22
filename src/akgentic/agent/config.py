"""Configuration models for team agents.

This module provides configuration for BaseAgent instances:
- AgentConfig: Per-agent configuration (prompt, LLM settings, limits)
- AgentState: Runtime state (backstory from prompt)

Team-level configuration for hiring is managed by Orchestrator and passed
dynamically during agent creation.
"""

from pydantic import Field

from akgentic.core import BaseConfig, BaseState
from akgentic.llm.config import ModelConfig, RuntimeConfig, UsageLimits
from akgentic.llm.prompts import PromptTemplate
from akgentic.tool.core import ToolCard


class AgentConfig(BaseConfig):
    """Configuration for a team agent instance.

    Each agent gets its own configuration with:
    - prompt: Agent's backstory/system prompt
    - model_cfg: LLM model provider and settings
    - runtime_cfg: Execution parameters (temperature, max tokens, etc.)
    - usage_limits: Token and request usage constraints
    - max_help_requests: Recursion depth limit for delegation chains

    Team-level config for hiring members is provided by Orchestrator,
    not stored in individual agents.

    Attributes:
        prompt: Agent backstory or system prompt (plain string or template).
        model_cfg: LLM model configuration (provider, model name, API settings).
        runtime_cfg: Runtime execution settings (temperature, max tokens, retries).
        usage_limits: Usage constraints (max tokens, max requests, time limits).
        max_help_requests: Maximum delegation depth (default: 5).

    Example:
        >>> from akgentic.llm.config import ModelConfig, RuntimeConfig, UsageLimits
        >>> config = AgentConfig(
        ...     prompt="You are a helpful software developer.",
        ...     model_cfg=ModelConfig(provider="openai", model="gpt-4o"),
        ...     runtime_cfg=RuntimeConfig(temperature=0.7, max_tokens=2000),
        ...     usage_limits=UsageLimits(request_limit=50, total_tokens_limit=100000),
        ...     max_help_requests=5
        ... )
    """

    prompt: PromptTemplate = Field(
        default_factory=PromptTemplate,
        description="Agent backstory or system prompt defining behavior and personality",
    )
    model_cfg: ModelConfig = Field(
        default_factory=ModelConfig,
        description="LLM model configuration including provider, model name, and API settings",
    )
    runtime_cfg: RuntimeConfig = Field(
        default_factory=RuntimeConfig,
        description="Runtime execution settings such as temperature, max tokens and retry behavior",
    )
    usage_limits: UsageLimits = Field(
        default_factory=UsageLimits,
        description="Usage constraints for token and request limits to prevent runaway costs",
    )
    max_help_requests: int = Field(
        default=5,
        description="Maximum delegation depth to prevent infinite request chains",
    )
    tools: list[ToolCard] = Field(
        default_factory=list,
        description="List of tool cards defining the tools this agent can use, with parameters",
    )


class AgentState(BaseState):
    """Runtime state for team agents.

    Stores the agent's backstory (resolved from config prompt) and
    maintains state change notifications for observers.

    Attributes:
        backstory: Resolved backstory/system prompt from config
    """

    backstory: str
