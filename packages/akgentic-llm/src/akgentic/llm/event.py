from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from pydantic_ai.messages import ModelMessage

    from akgentic.llm.context import ContextSnapshot


@dataclass(frozen=True)
class LlmMessageEvent:
    """Event emitted when a new model message is added to context."""

    message: ModelMessage


@dataclass(frozen=True)
class LlmCheckpointCreatedEvent:
    """Event emitted when a context checkpoint is created."""

    snapshot: ContextSnapshot


@dataclass(frozen=True)
class LlmCheckpointRestoredEvent:
    """Event emitted when context is restored from a checkpoint."""

    snapshot: ContextSnapshot


@dataclass(frozen=True)
class ToolCallEvent:
    """Event emitted when the LLM invokes a tool.

    Emitted after ``LlmMessageEvent`` for every ``tool-call`` part found in
    a ``ModelResponse``. Multiple tool calls in a single response produce one
    ``ToolCallEvent`` per part, in part order.

    Attributes:
        run_id: String representation of the pydantic-ai ``run_id`` UUID stamped
            on the originating ``ModelResponse``. All ``ToolCallEvent`` instances
            from the same ``ReactAgent.run()`` invocation share the same value.
        tool_name: Name of the tool being called.
        tool_call_id: Unique identifier for this call (assigned by the model).
        arguments: Raw JSON string of the arguments passed to the tool.
            Consumers who need structured access should do ``json.loads(event.arguments)``.
    """

    run_id: str
    tool_name: str
    tool_call_id: str
    arguments: str


@dataclass(frozen=True)
class ToolReturnEvent:
    """Event emitted when a tool call completes (successfully or with an error).

    Emitted after ``LlmMessageEvent`` for:
    - ``tool-return`` parts in a ``ModelRequest`` → ``success=True``
    - ``retry-prompt`` parts with a non-None ``tool_name`` → ``success=False``

    Attributes:
        run_id: String representation of the pydantic-ai ``run_id`` UUID stamped
            on the originating ``ModelRequest``. Matches the ``run_id`` on the
            ``ToolCallEvent`` that initiated the corresponding tool call.
        tool_name: Name of the tool that was called.
        tool_call_id: Identifier matching the originating ``ToolCallEvent``.
        success: ``True`` if the tool returned normally; ``False`` if the model
            issued a retry prompt due to a tool error.
    """

    run_id: str
    tool_name: str
    tool_call_id: str
    success: bool


@dataclass(frozen=True)
class LlmUsageEvent:
    """Event emitted for each ModelResponse with token usage data.

    Emitted after LlmMessageEvent and tool events for every ModelResponse
    message added to context. Consumers can aggregate by run_id for per-call
    totals, or by agent/session at higher layers.

    Attributes:
        run_id: String representation of the pydantic-ai run_id stamped on
            the originating ModelResponse. All events from the same
            ReactAgent.run() invocation share the same value.
        model_name: Model identifier as reported by the provider
            (e.g. "claude-sonnet-4-20250514").
        provider_name: Provider identifier (e.g. "anthropic", "openai").
        input_tokens: Tokens consumed in the prompt.
        output_tokens: Tokens generated in the response.
        cache_read_tokens: Tokens read from provider cache.
        cache_write_tokens: Tokens written to provider cache.
        requests: Number of HTTP requests for this response.
    """

    run_id: str
    model_name: str
    provider_name: str
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_write_tokens: int
    requests: int


@runtime_checkable
class ContextObserver(Protocol):
    """Observer protocol for LLM context changes."""

    def notify_event(self, event: object) -> None:
        """Called when an LLM domain event is emitted.

        Args:
            event: Domain event object
        """
        ...
