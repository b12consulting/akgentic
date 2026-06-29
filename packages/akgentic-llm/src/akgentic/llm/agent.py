"""REACT-based LLM agent with context management and iteration support."""

import asyncio
import logging
import traceback
from typing import Any, cast

from pydantic_ai import Agent, BinaryContent, UsageLimitExceeded
from pydantic_ai import UsageLimits as PydanticUsageLimits
from pydantic_ai.messages import ModelMessage, ModelRequest, ModelResponse, ToolReturnPart

from .compaction import CompactionResult, CompactionStrategy, create_compaction
from .config import ReactAgentConfig, UsageLimits
from .context import ContextManager
from .event import (
    ContextObserver,
    LlmContextClearedEvent,
    LlmContextCompactedEvent,
    LlmMessageEvent,
    LlmSystemPromptEvent,
)
from .providers import create_http_client, create_model, get_output_type

logger = logging.getLogger(__name__)

UserPrompt = str | list[str | BinaryContent]


def _evict_anyio_run_vars(loop: asyncio.AbstractEventLoop) -> None:
    """Remove anyio's per-loop run-vars entry so the closed loop can be GC'd.

    anyio keeps per-loop state in a module-global ``WeakKeyDictionary``
    (``anyio.lowlevel._run_vars``, keyed by the loop); its value retains the
    finished run ``Task``, which strong-references the loop, so the weak key
    never clears and the loop leaks. Evicting our own loop's entry breaks that
    ``_root_task → loop`` anchor. Best-effort and version-guarded: anyio
    internals are private, and a missing/absent ``_run_vars`` (or no anyio) must
    not break teardown. Local copy of ``akgentic.core.agent._evict_anyio_run_vars``
    (NOT imported — ``akgentic-llm`` must not depend on a sibling package).
    """
    try:
        from anyio.lowlevel import _run_vars  # noqa: PLC0415

        _run_vars.pop(loop, None)
    except Exception:
        pass


class UsageLimitError(Exception):
    """Raised when usage limits are exceeded during agent execution."""

    pass


class ReactAgent:
    """REACT-based LLM agent.

    Features:
    - REACT pattern support (via pydantic-ai)
    - Dynamic system prompts with registry
    - Context management with observer pattern
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
            event_loop: Deprecated — accepted and ignored. The agent creates and
                owns its own loop (``self._loop``); the passed loop is neither
                adopted nor used by ``run_sync``. Kept in the signature for one
                release so callers can stop passing it without a flag day.
        """
        # The agent owns its loop: create it and make it current on the
        # constructing thread BEFORE building the httpx client / model, so the
        # connection pool stays a per-agent resource bound to one stable loop
        # (ADR-008). The deprecated `event_loop=` argument is ignored.
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._closed = False

        self._config = config
        self._deps_type = deps_type
        self._result_type = result_type

        # Create context manager (no max_messages by default)
        self._context = ContextManager()

        # Register observer if provided
        if observer:
            self._context.subscribe(observer)

        # Create HTTP client. Held on the instance so its connection pool can be
        # released on stop via aclose(); otherwise the pydantic-ai Model keeps the
        # httpx.AsyncClient (open sockets/TLS buffers) alive past team teardown.
        self._http_client = create_http_client(
            timeout_s=config.runtime_cfg.http_client_config.timeout,
            max_attempts=config.runtime_cfg.http_client_config.max_retries,
            exp_multiplier=config.runtime_cfg.http_client_config.backoff_multiplier,
            exp_max_s=config.runtime_cfg.http_client_config.backoff_max,
        )

        # Resolve the compaction strategy as runtime state (never a Pydantic
        # field). Built AFTER self._http_client so the summarizer reuses the
        # agent's shared httpx client (no second pool); the summarizer model uses
        # summary_model_cfg when set, else the primary model_cfg.
        summary_cfg = config.compaction_cfg.summary_model_cfg or config.model_cfg
        self._compaction: CompactionStrategy = create_compaction(
            config.compaction_cfg, summary_cfg, self._http_client
        )

        # Create model from config
        self._model = create_model(config.model_cfg, self._http_client)

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
            instrument=False,
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
        # Auto-compact (at most once per turn) BEFORE iter() snapshots the
        # history. iter() reads message_history once at entry and the loop only
        # appends, so compacting here takes effect for the whole turn.
        await self._maybe_compact()
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

    def _compaction_threshold(self) -> int | None:
        """Token budget that arms the auto-trigger, or None when compaction is off.

        ``int(context_length * trigger_ratio)`` when ``model_cfg.context_length``
        is set; ``None`` (auto-compaction disabled) otherwise.
        """
        context_length = self._config.model_cfg.context_length
        if context_length is None:
            return None
        return int(context_length * self._config.compaction_cfg.trigger_ratio)

    async def _maybe_compact(self) -> None:
        """Auto-compact once when the last run's input_tokens exceed the threshold.

        Reads the provider-reported ``last_input_tokens`` (no ``tiktoken``). No-ops
        when auto-trigger is off, the budget is unset (``context_length is None``),
        no usage has been reported yet (``last_input_tokens is None`` — never
        mis-fires on missing data), or usage is at/below the threshold. Fires at
        most once per ``run()`` call.
        """
        cfg = self._config.compaction_cfg
        if not cfg.auto_trigger:
            return
        threshold = self._compaction_threshold()
        used = self._context.last_input_tokens
        if threshold is None or used is None or used <= threshold:
            return
        await self._compact_now()

    def _build_compaction_event(self, result: CompactionResult) -> LlmContextCompactedEvent:
        """Build the append-only event from a strategy result (auto + manual share this)."""
        cfg = self._config.compaction_cfg
        return LlmContextCompactedEvent(
            run_id=None,
            strategy_id=cfg.strategy,
            summary=result.summary,
            replaced_message_count=result.replaced_message_count,
            summarizer_prompt_version=cfg.summarizer_prompt_version,
            tokens_before=self._context.last_input_tokens,
            tokens_after=result.tokens_after,
        )

    async def _compact_now(self) -> str:
        """Run the strategy and fold its result in — the shared async core (R1 + R2).

        Awaits the strategy inside the running loop (ADR-009). A zero-replacement
        result is a clean no-op: no event, no synthetic summary. Both the auto path
        (``_maybe_compact``) and the sync ``compact()`` bridge converge here.

        Returns:
            A human-readable status string.
        """
        result = await self._compaction.compact(self._context.messages)
        if result.replaced_message_count == 0:
            return "Nothing to compact."
        self._context.compact(self._build_compaction_event(result))
        return (
            f"Compacted: replaced {result.replaced_message_count} "
            f"earlier message(s) with a summary."
        )

    def compact(self) -> str:
        """Force a compaction now, bypassing the budget gate (manual /compact, FR15).

        Synchronous bridge for the slash-command path: the agent-owned loop is idle
        at dispatch time, so ``run_until_complete`` is legal here (unlike the auto
        path, which awaits inside the running loop). Mirrors ``run_sync``'s
        closed-agent guard.

        Returns:
            A human-readable status string.

        Raises:
            RuntimeError: If the agent has been closed.
        """
        if self._closed or self._loop.is_closed():
            raise RuntimeError("ReactAgent is closed")
        return self._loop.run_until_complete(self._compact_now())

    def clear_context(self) -> str:
        """Wipe the conversation; the system prompt regenerates next run (/clear, FR15).

        Pure synchronous wrapper over ``ContextManager.clear_context`` — no
        summarizer, no ``run_until_complete``, no loop interaction.

        Returns:
            A human-readable status string.
        """
        removed = self._context.clear_context()
        return f"Cleared {removed} message(s); system prompt regenerates on the next run."

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
            RuntimeError: If the agent has been closed
            UsageLimitError: If usage limits exceeded
        """
        # Always run on the agent's own loop so the httpx connection pool stays
        # bound to ONE stable loop across calls. There is no asyncio.run()
        # fallback: a fresh loop per call would leave pooled connections attached
        # to already-closed loops, making aclose() raise on stop and leaking the
        # pool (RAM grows per team).
        if self._closed or self._loop.is_closed():
            raise RuntimeError("ReactAgent is closed")
        return self._loop.run_until_complete(self.run(user_prompt, deps, output_type))

    async def aclose(self) -> None:
        """Release async resources (the httpx connection pool); does NOT close the loop.

        Resource-only teardown driven by ``close()`` on a still-open loop. The
        pydantic-ai Model (and its provider) hold this client, so without
        ``aclose()`` its open sockets and connection pool survive team stop and
        accumulate in the worker. Guarded so a second call is harmless.
        """
        if not self._http_client.is_closed:
            await self._http_client.aclose()

    def close(self) -> None:
        """Synchronously tear the agent down; idempotent.

        Drives async resource teardown (``aclose()``) on the still-open loop,
        cancels stragglers, drains async generators and the default executor,
        closes the loop, then evicts anyio's per-loop run-vars anchor — all in
        ``finally``. The five-step order is load-bearing: async exit handlers
        must run before the loop closes, and stragglers / async generators must
        drain before ``loop.close()`` to avoid leaked transports and "Task was
        destroyed but it is pending" warnings; step 5 (the eviction) runs after
        ``loop.close()`` so the closed loop can actually be GC'd (the
        ``_root_task`` ``RunVar`` would otherwise pin it). A second call is a
        harmless no-op; teardown failures are logged, never raised.
        """
        loop = self._loop
        if self._closed or loop.is_closed():
            return
        self._closed = True
        try:
            if not loop.is_running():
                loop.run_until_complete(self.aclose())  # 1. async resource teardown
                self._cancel_pending(loop)  # 2. cancel stragglers
                loop.run_until_complete(loop.shutdown_asyncgens())  # 3. drain async gens
                loop.run_until_complete(loop.shutdown_default_executor())
        except Exception:
            logger.warning("ReactAgent.close() teardown failed", exc_info=True)
        finally:
            loop.close()  # 4. close the loop
            _evict_anyio_run_vars(loop)  # 5. break anyio's _root_task → loop anchor

    @staticmethod
    def _cancel_pending(loop: asyncio.AbstractEventLoop) -> None:
        """Cancel and await any tasks still pending on ``loop`` before close.

        Mirrors ``Akgent._drain_event_loop``'s straggler step: cancel each
        pending task, then run the loop once to let the cancellations settle.
        """
        pending = [t for t in asyncio.all_tasks(loop) if not t.done()]
        if not pending:
            return
        for task in pending:
            task.cancel()
        loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))

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

    def restore_context(self, events: list[Any]) -> None:
        """Restore LLM conversation context as an ordered fold over persisted events.

        Folds ``events`` in persisted-sequence order into an accumulator:
        ``LlmMessageEvent`` appends its message; ``LlmContextCompactedEvent``
        applies the **same** mechanical fold as the live ``compact()`` (via the
        shared ``ContextManager.fold_compaction`` helper, **without** notify);
        ``LlmContextClearedEvent`` resets the accumulator to empty (**without**
        notify). Non-matching events (e.g. ``ToolCallEvent``, arbitrary objects)
        are ignored. The final accumulator is bulk-restored once (observers never
        fire during replay), then the dedup hash is seeded from the latest
        ``LlmSystemPromptEvent``. Sharing the fold with the live path keeps live
        and replayed contexts byte-identical, and two sequential compaction events
        compose (the later fold consumes the earlier synthetic summary) with the
        fold applied exactly once per event (FR16).

        Args:
            events: List of event-like objects (typically ``EventMessage``
                instances from ``akgentic-core``). Each is expected to carry a
                ``.event`` payload.
        """
        messages: list[ModelMessage] = []
        for e in events:
            if not hasattr(e, "event"):
                continue
            payload = e.event
            if isinstance(payload, LlmMessageEvent):
                messages.append(payload.message)
            elif isinstance(payload, LlmContextCompactedEvent):
                messages = ContextManager.fold_compaction(messages, payload)
            elif isinstance(payload, LlmContextClearedEvent):
                messages = []
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
