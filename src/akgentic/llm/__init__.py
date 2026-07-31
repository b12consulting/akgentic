"""akgentic-llm: LLM integration layer for agent systems.

Clean abstraction for LLM providers with REACT pattern support,
context management, and comprehensive configuration.

Quick Start:
    >>> from akgentic.llm import ModelConfig, ReactAgentConfig, ReactAgent
    >>> config = ReactAgentConfig(
    ...     model=ModelConfig(provider="openai", model="gpt-4o"),
    ... )
    >>> agent = ReactAgent(config=config)
    >>> result = await agent.run("Hello!")

Key Concepts:
    - REACT pattern: Iterative agent execution with tool calls
    - UsageLimits: Token/request budgets to control costs
    - ContextManager: Message history tracking
    - PromptTemplate: Template-based prompts with parameter substitution
"""

from .agent import ReactAgent, UsageLimitError, UserPrompt
from .compaction import (
    COMPACTION_STRATEGIES,
    SUMMARY_INSTRUCTIONS,
    CompactionResult,
    CompactionStrategy,
    create_compaction,
)
from .config import (
    CompactionConfig,
    HttpClientConfig,
    ModelConfig,
    ReactAgentConfig,
    RuntimeConfig,
    UsageLimits,
)
from .context import ContextManager
from .event import (
    ContextObserver,
    LlmContextClearedEvent,
    LlmContextCompactedEvent,
    LlmMessageEvent,
    LlmSystemPromptEvent,
    LlmUsageEvent,
    SystemPromptPartSnapshot,
    ToolCallEvent,
    ToolReturnEvent,
)
from .pricing import (
    AgentUsageSummary,
    ModelUsage,
    RunUsageSummary,
    aggregate_usage,
)
from .prompts import (
    PromptTemplate,
    current_datetime_prompt,
    json_output_reminder_prompt,
)
from .providers import create_http_client, create_model, create_model_settings, get_output_type

__all__ = [
    # Configuration
    "ModelConfig",
    "UsageLimits",
    "RuntimeConfig",
    "HttpClientConfig",
    "ReactAgentConfig",
    "CompactionConfig",
    # Agent
    "ReactAgent",
    "UsageLimitError",
    "UserPrompt",
    # Context
    "ContextManager",
    "ContextObserver",
    "LlmMessageEvent",
    "LlmUsageEvent",
    "LlmSystemPromptEvent",
    "SystemPromptPartSnapshot",
    "ToolCallEvent",
    "ToolReturnEvent",
    # Compaction
    "LlmContextCompactedEvent",
    "LlmContextClearedEvent",
    "CompactionStrategy",
    "CompactionResult",
    "COMPACTION_STRATEGIES",
    "SUMMARY_INSTRUCTIONS",
    "create_compaction",
    # Prompts
    "PromptTemplate",
    "current_datetime_prompt",
    "json_output_reminder_prompt",
    # Pricing & Aggregation
    "AgentUsageSummary",
    "ModelUsage",
    "RunUsageSummary",
    "aggregate_usage",
    # Providers
    "create_model",
    "create_http_client",
    "create_model_settings",
    "get_output_type",
]

__version__ = "0.1.0"
