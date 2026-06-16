"""REACT-based LLM agent with context management and iteration support."""

import asyncio
import logging
import traceback
from typing import Any, cast

from pydantic_ai import Agent, BinaryContent, UsageLimitExceeded
from pydantic_ai import UsageLimits as PydanticUsageLimits
from pydantic_ai.messages import ModelRequest, ModelResponse, ToolReturnPart

from .config import ReactAgentConfig, UsageLimits
from .context import ContextManager, ContextSnapshot
from .event import ContextObserver, LlmMessageEvent, LlmSystemPromptEvent
from .providers import create_http_client, create_model, get_output_type

logger = logging.getLogger(__name__)

UserPrompt = str | list[str | BinaryContent]


class UsageLimitError(Exception):
    """Raised when usage limits are exceeded during agent execution."""

    pass


class ReactAgent:
    """REACT-based LLM agent.

    Features:
    - REACT pattern support (via pydantic-ai)
    - Dynamic system prompts with registry
    - Context management with observer pattern
    - Checkpoint/rewind for error recovery
    - Iterative execution with context updates
    - Tool integration
    - Usage limit enforcement
    - HTTP retry logic

    Example:
        >>> config = ReactAgentConfig(
        ...     model=ModelConfig(provider="openai", model="gpt-4o")
        ... )
        >>> # Option 1: Pass observer at initialization
        >>> agent = ReactAgent(
        ...     config=config,
        ...     deps_type=MyDeps,
        ...     tools=[my_tool_func],
        ...     observer=my_observer
        ... )
        >>> # Option 2: Subscribe observer later
        >>> agent = ReactAgent(config=config)
        >>> agent.subscribe_context(my_observer)
        >>> result = await agent.run("User query")
    """

    def __init__(
        self,
        config: ReactAgentConfig,
        deps_type: type[Any] | None = None,
        tools: list[Any] | None = None,
        toolsets: list[Any] | None = None,
        result_type: type[Any] = str,
        observer: ContextObserver | None = None,
        event_loop: asyncio.AbstractEventLoop | None = None,
    ) -> None:
        """Initialize REACT agent.

        Args:
            config: Complete agent configuration
            deps_type: Type for dependency injection (optional)
            tools: List of tool functions (optional)
            toolsets: List of toolsets (optional, e.g., MCP servers)
            result_type: Type for agent result validation (default: str)
            observer: Context observer to register automatically (optional)
            event_loop: Asyncio event loop to use (optional, defaults to current loop)
        """
        self._config = config
        self._deps_type = deps_type
        self._result_type = result_type
        self._event_loop = event_loop

        # Create context manager (no max_messages by default)
        self._context = ContextManager()

        # Register observer if provided
        if observer:
            self._context.subscribe(observer)

        # Create HTTP client
        http_client = create_http_client(
            timeout_s=config.runtime_cfg.http_client_config.timeout,
            max_attempts=config.runtime_cfg.http_client_config.max_retries,
            exp_multiplier=config.runtime_cfg.http_client_config.backoff_multiplier,
            exp_max_s=config.runtime_cfg.http_client_config.backoff_max,
        )

        # Create model from config
        self._model = create_model(config.model_cfg, http_client)

        # Wrap result_type with provider-aware output strategy for structured output
        wrapped_result_type: Any = get_output_type(config.model_cfg, result_type)

        # Create pydantic-ai Agent.
        # pydantic-ai's Agent() @overload stubs are narrower than the runtime
        # __init__: they reject `history_processors` / `instrument` and a
        # `type[Any] | None` `deps_type`, all of which the runtime accepts.
        self._pydantic_agent = Agent(  # type: ignore[call-overload]
            model=self._model,
            tools=tools or [],
            toolsets=toolsets or [],
            retries=config.runtime_cfg.retries,
            deps_type=deps_type,
            end_strategy=config.runtime_cfg.end_strategy,
            output_type=wrapped_result_type,
            history_processors=[],  # Empty for MVP (story 2-1-6b deferred)
            instrument=True,
        )

    async def run(
        self, user_prompt: UserPrompt, deps: Any = None, output_type: type[Any] | None = None
    ) -> Any:
        """Execute agent with REACT pattern.

        Runs pydantic-ai agent iteratively, updating context after each
        iteration step.

        Args:
            user_prompt: User message to process
            deps: Optional dependency object (must match deps_type)
            output_type: Optional per-call output type override. When provided,
                wraps with get_output_type() for provider-aware structured output
                (NativeOutput for OpenAI/Anthropic, raw type for others).
                When None, uses result_type set at construction (default: str).

        Returns:
            Agent result output (type matches output_type if given, else result_type)

        Raises:
            UsageLimitError: If usage limits exceeded
        """
        user_prompt = self._fold_pending_operator_actions(user_prompt)
        pydantic_limits = self._to_pydantic_limits(self._config.usage_limits)

        try:
            # Track messages added in THIS run to prevent duplicates
            # (new_messages() can return same messages across iterations)
            added_message_ids: set[int] = set()

            async with self._pydantic_agent.iter(
                user_prompt=user_prompt,
                deps=deps,
                usage_limits=pydantic_limits,
                message_history=self._context.messages,
                output_type=get_output_type(self._config.model_cfg, output_type),
            ) as run:
                async for _ in run:
                    # new_messages() may return previously emitted messages
                    # during tool call iterations - only add each once
                    for message in run.new_messages():
                        msg_id = id(message)
                        if msg_id not in added_message_ids:
                            added_message_ids.add(msg_id)
                            self._context.add_message(message)

                # Record this run's effective system prompt rendering exactly
                # once, after pydantic-ai's in-place dynamic re-evaluation and
                # the new_messages() drain, before returning (ADR-004 §2).
                self._record_run_system_prompt(run)

                return run.result.output if run.result else None

        except UsageLimitExceeded as e:
            self._heal_unprocessed_tool_calls(traceback.format_exc())
            raise UsageLimitError(str(e)) from e
        except Exception:
            self._heal_unprocessed_tool_calls(traceback.format_exc())
            raise

    def _fold_pending_operator_actions(self, user_prompt: UserPrompt) -> UserPrompt:
        """Prepend any buffered pre-first-run operator actions to the run prompt.

        Drains ``ContextManager._pending_operator_actions`` and folds the entries
        into ``user_prompt`` so they reach the model **as prompt content** rather
        than as a system-less ``ModelRequest`` in ``message_history`` — which
        would make pydantic-ai's history non-empty and suppress system-prompt
        injection on the first run. ``message_history`` is therefore left
        untouched (empty before the first run), preserving injection.

        Folding is per prompt shape:

        - ``str`` prompt → ``f"{preamble}\\n\\n{user_prompt}"``;
        - multimodal ``list`` prompt → the joined ``preamble`` inserted as the
          leading element.

        When nothing is buffered the prompt is returned unchanged.

        Args:
            user_prompt: The caller's prompt for this run (str or multimodal list).

        Returns:
            The prompt with buffered operator actions prepended, or the original
            prompt when the buffer is empty.
        """
        pending = self._context.drain_pending_operator_actions()
        if not pending:
            return user_prompt
        preamble = "\n\n".join(pending)
        if isinstance(user_prompt, str):
            return f"{preamble}\n\n{user_prompt}"
        return [preamble, *user_prompt]

    def _record_run_system_prompt(self, run: Any) -> None:
        """Record the completed run's effective system prompt rendering once.

        Derives the run's ``run_id`` from its own messages — the same value
        ``_emit_tool_events`` reads via ``message.run_id`` — so the emitted
        ``LlmSystemPromptEvent.run_id`` correlates with that run's
        ``LlmMessageEvent``/``ToolCallEvent``/``LlmUsageEvent`` values. When the
        run produced no new messages (no ``run_id`` available), the recording
        call is skipped — there is nothing to record (ADR-004 §2).

        Args:
            run: The completed pydantic-ai run object, exposing
                ``new_messages()``.
        """
        new_messages = run.new_messages()
        if not new_messages:
            return
        run_id = getattr(new_messages[-1], "run_id", None)
        if run_id is None:
            return
        self._context.record_system_prompt(str(run_id))

    def run_sync(
        self, user_prompt: UserPrompt, deps: Any = None, output_type: type[Any] | None = None
    ) -> Any:
        """Execute agent synchronously.

        Convenience wrapper around run() for synchronous contexts.

        Args:
            user_prompt: User message to process
            deps: Optional dependency object
            output_type: Optional per-call output type override (see run()).

        Returns:
            Agent result output

        Raises:
            UsageLimitError: If usage limits exceeded
        """
        if self._event_loop and self._event_loop.is_running():
            self._event_loop.run_until_complete(self.run(user_prompt, deps, output_type))

        return asyncio.run(self.run(user_prompt, deps, output_type))

    def _to_pydantic_limits(self, limits: UsageLimits | None) -> PydanticUsageLimits | None:
        """Convert config UsageLimits to pydantic-ai UsageLimits.

        Args:
            limits: Config usage limits or None

        Returns:
            Pydantic-ai usage limits or None
        """
        if limits is None:
            return None

        return PydanticUsageLimits(
            request_limit=limits.request_limit,
            tool_calls_limit=limits.tool_calls_limit,
            input_tokens_limit=limits.input_tokens_limit,
            output_tokens_limit=limits.output_tokens_limit,
            total_tokens_limit=limits.total_tokens_limit,
        )

    def _heal_unprocessed_tool_calls(self, error_detail: str) -> None:
        """Complete any pending tool calls in context with error responses.

        When the REACT loop fails mid-execution, the last message may be a
        ModelResponse with tool calls that never received results. This
        appends a ModelRequest with ToolReturnPart for each pending call,
        preventing the 'unprocessed tool calls' error on the next run().

        Args:
            error_detail: Error detail string (typically a traceback) embedded
                verbatim into each healing ``ToolReturnPart.content`` so the LLM
                has visibility into the failure on the next turn.
        """
        messages = self._context.messages
        if not messages:
            return

        last = messages[-1]
        if not isinstance(last, ModelResponse) or not last.tool_calls:
            return

        # list[Any] rather than list[ToolReturnPart] to satisfy mypy strict
        # mode: ModelRequest.parts is a union type and narrowing to the
        # concrete part type triggers an assignment-variance error.
        error_parts: list[Any] = [
            ToolReturnPart(
                tool_name=call.tool_name,
                content=f"Error: tool call aborted due to failure: {error_detail}",
                tool_call_id=call.tool_call_id,
            )
            for call in last.tool_calls
        ]

        logger.warning(
            "Healing %d unprocessed tool call(s) after error", len(error_parts)
        )
        self._context.add_message(ModelRequest(parts=error_parts))

    # API wrapper methods

    @property
    def context(self) -> ContextManager:
        """Get context manager for message history access.

        Returns:
            Context manager instance
        """
        return self._context

    def subscribe_context(self, observer: ContextObserver) -> None:
        """Subscribe to context change notifications.

        Args:
            observer: Observer implementing ContextObserver protocol
        """
        self._context.subscribe(observer)

    def checkpoint(self, checkpoint_id: str | None = None) -> ContextSnapshot:
        """Create a checkpoint of current context.

        Args:
            checkpoint_id: Optional checkpoint ID (auto-generated if None)

        Returns:
            Created snapshot
        """
        return self._context.checkpoint(checkpoint_id)

    def rewind(self, checkpoint_id: str) -> None:
        """Restore context to a checkpoint.

        Args:
            checkpoint_id: Checkpoint to restore

        Raises:
            KeyError: If checkpoint not found
        """
        self._context.rewind(checkpoint_id)

    def restore_context(self, events: list[Any]) -> None:
        """Restore LLM conversation context from persisted events.

        Filters ``events`` for objects whose ``.event`` attribute is an
        ``LlmMessageEvent``, extracts the ``ModelMessage`` from each, and
        bulk-restores them into the ``ContextManager``.  Non-matching events
        (e.g. ``ToolCallEvent``, arbitrary objects) are silently ignored.

        Args:
            events: List of event-like objects (typically ``EventMessage``
                instances from ``akgentic-core``). Each object is expected
                to carry a ``.event`` payload; only those where
                ``isinstance(e.event, LlmMessageEvent)`` contribute a
                message.
        """
        messages = [
            e.event.message
            for e in events
            if hasattr(e, "event") and isinstance(e.event, LlmMessageEvent)
        ]
        self._context.restore(messages)
        self._seed_system_prompt_from_events(events)

    def _seed_system_prompt_from_events(self, events: list[Any]) -> None:
        """Seed the dedup hash from the latest persisted ``LlmSystemPromptEvent``.

        Scans ``events`` for the **latest** ``LlmSystemPromptEvent`` (the last in
        append/persist order) and seeds ``ContextManager._last_system_prompt_hash``
        from its ``content_hash`` via ``seed_system_prompt_hash`` — without firing
        observers — so a restored agent does not re-emit an unchanged rendering.
        When no such event is present (e.g. an older team), the dedup state is left
        at its current ``None`` so the next run emits the ``None → hash`` transition
        (ADR-004 §3). Additive to the message-restore scan; the same guard style is
        reused.

        Args:
            events: The same event-like list passed to ``restore_context``.
        """
        latest = next(
            (
                e.event
                for e in reversed(events)
                if hasattr(e, "event") and isinstance(e.event, LlmSystemPromptEvent)
            ),
            None,
        )
        if latest is not None:
            self._context.seed_system_prompt_hash(latest.content_hash)

    def system_prompt(self, func: Any) -> Any:
        """Register a custom dynamic system prompt.

        Convenience wrapper around pydantic-ai's @agent.system_prompt(dynamic=True).

        Example:
            >>> @agent.system_prompt
            >>> def my_prompt(ctx):
            ...     return f"Context: {ctx.deps.get_context()}"

        Args:
            func: System prompt function

        Returns:
            Decorated function
        """
        return self._pydantic_agent.system_prompt(dynamic=True)(func)

    def tool(self, func: Any) -> Any:
        """Register a tool function.

        Convenience wrapper around pydantic-ai's @agent.tool().

        Example:
            >>> @agent.tool
            >>> def search(query: str) -> list[str]:
            ...     return search_database(query)

        Args:
            func: Tool function

        Returns:
            Decorated function
        """
        return self._pydantic_agent.tool(func)

    @property
    def pydantic_agent(self) -> Agent[Any, Any]:
        """Access underlying pydantic-ai Agent for advanced usage.

        Use for features not wrapped by ReactAgent:
        - result_validator()
        - on_error()
        - Direct decorator access

        Returns:
            Pydantic-ai Agent instance
        """
        # `_pydantic_agent` is Any-typed (Agent() call uses a typed-ignore);
        # the runtime value genuinely is an Agent, so cast to recover the type.
        return cast(Agent[Any, Any], self._pydantic_agent)
