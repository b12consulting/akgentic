"""Configuration models for LLM provider settings.

This module provides Pydantic models for configuring LLM providers,
usage limits, and agent execution settings.

Examples:
    Basic model configuration:

    >>> from akgentic.llm import ModelConfig
    >>> config = ModelConfig(
    ...     provider="openai",
    ...     model="gpt-4o",
    ...     temperature=0.7
    ... )

    Configuration with usage limits:

    >>> from akgentic.llm import ModelConfig, UsageLimits, ReactAgentConfig
    >>> config = ReactAgentConfig(
    ...     model=ModelConfig(provider="openai", model="gpt-4o"),
    ...     usage_limits=UsageLimits(request_limit=10, total_tokens_limit=5000)
    ... )
"""

from typing import Literal

from pydantic import BaseModel, Field


class ModelConfig(BaseModel):
    """Configuration for LLM model settings.

    Supports multiple providers with provider-agnostic configuration.
    Provider-specific authentication is handled via environment variables:
    - OpenAI: OPENAI_API_KEY
    - Azure: AZURE_OPENAI_API_KEY, AZURE_OPENAI_ENDPOINT
    - Anthropic: ANTHROPIC_API_KEY
    - Google: GOOGLE_API_KEY or GOOGLE_APPLICATION_CREDENTIALS
    - Mistral: MISTRAL_API_KEY
    - NVIDIA: NVIDIA_API_KEY

    Attributes:
        provider: LLM provider name
        model: Model identifier (provider-specific naming)
        temperature: Sampling temperature (0.0 = deterministic, 2.0 = maximum creativity)
        seed: Random seed for reproducible outputs (not supported by all providers)
        max_tokens: Maximum tokens in model response (None = provider default/maximum)
        reasoning_effort: Reasoning effort for o1/o3-style models ('low', 'medium', 'high')

    Example:
        >>> # OpenAI GPT-4o with moderate creativity
        >>> config = ModelConfig(
        ...     provider="openai",
        ...     model="gpt-4o",
        ...     temperature=0.7,
        ...     max_tokens=1000
        ... )
        >>>
        >>> # Anthropic Claude with deterministic output
        >>> config = ModelConfig(
        ...     provider="anthropic",
        ...     model="claude-3-5-sonnet-20241022",
        ...     temperature=0.0,
        ...     seed=42
        ... )
        >>>
        >>> # OpenAI o1 with high reasoning effort
        >>> config = ModelConfig(
        ...     provider="openai",
        ...     model="o1",
        ...     reasoning_effort="high"
        ... )
    """

    provider: Literal["openai", "azure", "nvidia", "google-gla", "mistral", "anthropic"] = Field(
        default="openai", description="Model provider"
    )

    model: str = Field(
        default="gpt-5.2",
        description="Model identifier (e.g., gpt-5.2, claude-3-5-sonnet-20241022)",
    )

    temperature: float | None = Field(
        default=None, ge=0.0, le=2.0, description="Sampling temperature (0.0-2.0)"
    )

    seed: int | None = Field(default=None, description="Random seed for reproducible outputs")

    max_tokens: int | None = Field(
        default=None, gt=0, description="Maximum tokens in model response"
    )

    reasoning_effort: Literal["low", "medium", "high"] | None = Field(
        default=None, description="Reasoning effort for o1/o3 models"
    )


class UsageLimits(BaseModel):
    """Usage limits for cost control and resource management.

    All limits are optional (None = unlimited) except for request_limit = 50 by default.
    Limits are enforced cumulatively during agent execution. When exceeded,
    raises UsageLimitError with details.

    Token tracking is cumulative across all requests in a single agent run:
    - input_tokens: Sum of all prompt/input tokens
    - output_tokens: Sum of all completion/output tokens
    - total_tokens: input_tokens + output_tokens

    Attributes:
        request_limit: Maximum number of LLM API requests per agent run
        tool_calls_limit: Maximum number of tool invocations per agent run
        input_tokens_limit: Maximum cumulative input/prompt tokens across all requests
        output_tokens_limit: Maximum cumulative output/completion tokens across all requests
        total_tokens_limit: Maximum cumulative total tokens (input + output) across all requests

    Example:
        >>> # Basic limits: 10 requests, 5K total tokens
        >>> limits = UsageLimits(
        ...     request_limit=10,
        ...     total_tokens_limit=5000
        ... )
        >>>
        >>> # Strict limits for cost control
        >>> limits = UsageLimits(
        ...     request_limit=50,          # default: 50
        ...     tool_calls_limit=20,       # default: None
        ...     input_tokens_limit=10000,  # default: None
        ...     output_tokens_limit=2000.  # default: None
        ... )
        >>>
        >>> # Unlimited (default) except for request_limit (50 default)
        >>> limits = UsageLimits(request_limit=None, total_tokens_limit=None)
    """

    request_limit: int | None = Field(
        default=50, gt=0, description="Maximum number of LLM requests per run"
    )

    tool_calls_limit: int | None = Field(
        default=None, gt=0, description="Maximum number of tool calls per run"
    )

    input_tokens_limit: int | None = Field(
        default=None, gt=0, description="Maximum input/prompt tokens"
    )

    output_tokens_limit: int | None = Field(
        default=None, gt=0, description="Maximum output/completion tokens"
    )

    total_tokens_limit: int | None = Field(
        default=None, gt=0, description="Maximum total tokens (input + output)"
    )


class HttpClientConfig(BaseModel):
    """HTTP client configuration for LLM API communication.

    Configures timeout and retry behavior with exponential backoff for transient failures:
    - Retries on: HTTP 429 (rate limit), 503 (service unavailable), connection errors
    - Backoff formula: min(max_delay, multiplier * (2 ** attempt))

    Attributes:
        timeout: Maximum time for single LLM request (connection + response)
        max_retries: Maximum retry attempts for transient HTTP failures
        backoff_multiplier: Base delay multiplier for exponential backoff (seconds)
        backoff_max: Cap on backoff delay to prevent excessive waiting

    Example:
        >>> # Default: 120s timeout, 5 retries with exponential backoff
        >>> client = HttpClientConfig()
        >>>
        >>> # Aggressive: fast timeout, fewer retries
        >>> client = HttpClientConfig(
        ...     timeout=30.0,
        ...     max_retries=2,
        ...     backoff_multiplier=0.2
        ... )
        >>>
        >>> # Conservative: long timeout, many retries
        >>> client = HttpClientConfig(
        ...     timeout=300.0,
        ...     max_retries=10,
        ...     backoff_multiplier=1.0,
        ...     backoff_max=120.0
        ... )
    """

    timeout: float = Field(
        default=120.0,
        gt=0,
        description="Maximum duration for single LLM request including connection and response time",  # noqa: E501
    )

    max_retries: int = Field(
        default=5,
        ge=1,
        description="Maximum HTTP retry attempts for transient failures (rate limits, service unavailable)",  # noqa: E501
    )

    backoff_multiplier: float = Field(
        default=0.5,
        gt=0,
        description="Base delay multiplier for exponential backoff: delay = multiplier * (2 ** attempt)",  # noqa: E501
    )

    backoff_max: float = Field(
        default=60.0,
        gt=0,
        description="Maximum backoff delay in seconds to prevent excessive retry waits",
    )


class RuntimeConfig(BaseModel):
    """Runtime configuration for agent behavior.

    Attributes:
        retries: Number of retry attempts for tool call failures and output validation errors
        end_strategy: Tool execution termination strategy
        parallel_tool_calls: Enable concurrent tool execution when model supports it
        http_client_config: HTTP client configuration for API communication
            (timeout and retry settings)

    Tool Execution Strategies:
        - 'early': Stops after first successful result (fast path)
        - 'exhaustive': Executes all tool calls even when result available (complete data gathering)

    Example:
        >>> # Default: resilient with standard HTTP settings
        >>> runtime = RuntimeConfig()
        >>>
        >>> # Aggressive: fast timeout, fewer retries
        >>> runtime = RuntimeConfig(
        ...     http_client_config=HttpClientConfig(
        ...         timeout=30.0, max_retries=2, backoff_multiplier=0.2
        ...     )
        ... )
        >>>
        >>> # Conservative: long timeout, many retries
        >>> runtime = RuntimeConfig(
        ...     http_client_config=HttpClientConfig(
        ...         timeout=300.0, max_retries=10, backoff_multiplier=1.0, backoff_max=120.0
        ...     )
        ... )
    """

    retries: int = Field(
        default=3,
        ge=0,
        description="Number of retry attempts for tool call failures and output validation errors",
    )

    end_strategy: Literal["early", "exhaustive"] = Field(
        default="exhaustive",
        description="Tool execution strategy: 'early' stops after first result, 'exhaustive' runs all tools",  # noqa: E501
    )

    parallel_tool_calls: bool = Field(
        default=True,
        description="Enable parallel tool execution when model supports concurrent calls",
    )

    http_client_config: HttpClientConfig = Field(
        default_factory=HttpClientConfig,
        description="HTTP client configuration for API communication",
    )


class ReactAgentConfig(BaseModel):
    """Configuration for REACT (Reasoning + Acting) pattern agent.

    Combines model settings, resource limits, and runtime behavior into a
    unified configuration for ReactAgent execution. This config is passed to
    ReactAgent during initialization and controls all aspects of LLM interaction.

    The REACT pattern alternates between:
    1. Reasoning: LLM generates thoughts and decides on actions
    2. Acting: Execute tools/functions based on LLM decisions
    3. Observing: Feed tool results back to LLM
    4. Repeat until task completion

    Attributes:
        model_cfg: LLM provider and model settings.
        runtime_cfg: Execution behavior and HTTP retry strategy.
        usage_limits: Resource limits for cost control.

    Example:
        >>> # Minimal configuration with defaults
        >>> config = ReactAgentConfig(
        ...     model_cfg=ModelConfig(provider="openai", model="gpt-4o")
        ... )
        >>>
        >>> # Full configuration with limits and custom behavior
        >>> config = ReactAgentConfig(
        ...     model_cfg=ModelConfig(
        ...         provider="anthropic",
        ...         model="claude-3-5-sonnet-20241022",
        ...         temperature=0.7
        ...     ),
        ...     usage_limits=UsageLimits(
        ...         request_limit=10,
        ...         total_tokens_limit=50000
        ...     ),
        ...     runtime_cfg=RuntimeConfig(
        ...         end_strategy="exhaustive",
        ...         http_client=HttpClientConfig(timeout=180.0)
        ...     )
        ... )
    """

    model_cfg: ModelConfig = Field(default_factory=ModelConfig, description="Model configuration")

    runtime_cfg: RuntimeConfig = Field(
        default_factory=RuntimeConfig, description="Runtime behavior configuration"
    )

    usage_limits: UsageLimits = Field(
        default_factory=UsageLimits, description="Usage limits for cost control"
    )
