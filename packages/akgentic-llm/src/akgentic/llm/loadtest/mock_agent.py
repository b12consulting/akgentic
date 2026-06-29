"""Drop-in mock for ``ReactAgent`` driven by a YAML state machine.

Implements ADR-007 §2–§5. ``MockReactAgent`` reproduces a recorded scenario's
routing and LLM event stream at zero token cost: it owns a real
``ContextManager`` and feeds synthetic pydantic-ai messages through it, so a
subscribed observer sees the same event sequence a real run would emit — but no
model or provider is ever built and no real tool is invoked.
"""

import asyncio
import logging
import re
import uuid
from typing import Any

from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.usage import RequestUsage

from akgentic.llm.context import ContextManager
from akgentic.llm.event import ContextObserver, LlmMessageEvent
from akgentic.llm.loadtest.scenario import (
    AgentScript,
    ScenarioState,
    ToolStub,
    _resolve_scenario_ref,
    load_scenario,
)

logger = logging.getLogger(__name__)


class MockProviderReachedError(RuntimeError):
    """Raised if a provider factory is reached during a mock run (zero-egress guard)."""


class MockReactAgent:
    """Transparent ``ReactAgent`` replacement backed by a scenario state machine."""

    def __init__(
        self,
        config: Any,
        deps_type: type[Any] | None = None,
        tools: list[Any] | None = None,
        toolsets: list[Any] | None = None,
        result_type: type[Any] = str,
        observer: ContextObserver | None = None,
        event_loop: asyncio.AbstractEventLoop | None = None,
    ) -> None:
        """Mirror ``ReactAgent.__init__`` without building a model or provider.

        ``_model``/``_http_client`` stay ``None`` (zero-token guarantee). The
        scenario is resolved from ``config`` and loaded eagerly.

        Args:
            event_loop: Deprecated — accepted and ignored. The mock creates and
                owns its own loop (``self._loop``) for drop-in parity with
                ``ReactAgent``; the passed loop is neither adopted nor used by
                ``run_sync``.
        """
        # The mock owns its loop too (drop-in parity over the ReactAgent close
        # surface): build no client/model, so loop creation can go first. The
        # deprecated `event_loop=` argument is ignored.
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._closed = False

        self._config = config
        self._deps_type = deps_type
        self._result_type = result_type
        self._model: Any = None
        self._http_client: Any = None

        self._context = ContextManager()
        if observer:
            self._context.subscribe(observer)

        self._scenario = load_scenario(_resolve_scenario_ref(config))
        self._consumed: set[int] = set()
        self._run_id: str = str(uuid.uuid4())

    # --- model/provider guard ------------------------------------------------

    def _build_model(self) -> Any:
        """Guard: a mock run must never reach a provider factory."""
        raise MockProviderReachedError(
            "MockReactAgent must never build a model or contact a provider"
        )

    # --- run -----------------------------------------------------------------

    async def run(
        self, user_prompt: Any, deps: Any = None, output_type: type[Any] | None = None
    ) -> Any:
        """Replay the matching state's event stream and return its output."""
        self._run_id = str(uuid.uuid4())
        name = self._agent_name(deps)
        script = self._scenario.agents[name]
        state = self._select_state(script, user_prompt)
        self._emit_request(user_prompt)
        for stub in state.tools:
            self._emit_tool_call(stub)
            self._emit_tool_return(stub)
        self._emit_final_response(state)
        await self._sleep(self._latency_ms(state))
        return self._build_output(state, output_type)

    def run_sync(
        self, user_prompt: Any, deps: Any = None, output_type: type[Any] | None = None
    ) -> Any:
        """Synchronous wrapper around :meth:`run` (mirrors ``ReactAgent.run_sync``).

        Always runs on the mock's own loop; there is no ``asyncio.run()``
        fallback. Raises once the agent has been closed.

        Raises:
            RuntimeError: If the agent has been closed.
        """
        if self._closed or self._loop.is_closed():
            raise RuntimeError("MockReactAgent is closed")
        return self._loop.run_until_complete(self.run(user_prompt, deps, output_type))

    async def aclose(self) -> None:
        """No-op teardown (mirrors ``ReactAgent.aclose``; the mock holds no client)."""
        return None

    def close(self) -> None:
        """Synchronously tear the mock down; idempotent (mirrors ``ReactAgent.close``).

        Drives the no-op ``aclose()`` on the still-open loop, cancels stragglers,
        drains async generators and the default executor, then closes the loop in
        ``finally``. A second call is a harmless no-op; teardown failures are
        logged, never raised.
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
            logger.warning("MockReactAgent.close() teardown failed", exc_info=True)
        finally:
            loop.close()  # 4. close the loop

    @staticmethod
    def _cancel_pending(loop: asyncio.AbstractEventLoop) -> None:
        """Cancel and await any tasks still pending on ``loop`` before close.

        Local copy of ``ReactAgent._cancel_pending`` — kept self-contained so the
        mock imports nothing from a sibling package (NFR1).
        """
        pending = [t for t in asyncio.all_tasks(loop) if not t.done()]
        if not pending:
            return
        for task in pending:
            task.cancel()
        loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))

    # --- state selection -----------------------------------------------------

    @staticmethod
    def _agent_name(deps: Any) -> str:
        """Duck-type the agent name from ``deps.config.name`` (no agent import)."""
        return str(getattr(deps.config, "name"))

    def _select_state(self, script: AgentScript, user_prompt: Any) -> ScenarioState:
        """Return the first matching unconsumed state, else the agent's default."""
        prompt = self._prompt_text(user_prompt)
        for state in script.states:
            key = id(state)
            if key in self._consumed:
                continue
            if self._matches(state, prompt):
                self._consumed.add(key)
                return state
        return script.default

    @staticmethod
    def _prompt_text(user_prompt: Any) -> str:
        """Flatten a str or multimodal-list prompt to text for matching."""
        if isinstance(user_prompt, str):
            return user_prompt
        if isinstance(user_prompt, list):
            return " ".join(p for p in user_prompt if isinstance(p, str))
        return str(user_prompt)

    @staticmethod
    def _matches(state: ScenarioState, prompt: str) -> bool:
        """Evaluate a state's optional ``when`` matcher against ``prompt``."""
        spec = state.when
        if spec is None:
            return True
        if spec.contains is not None and spec.contains.lower() not in prompt.lower():
            return False
        if spec.regex is not None and re.search(spec.regex, prompt) is None:
            return False
        if spec.from_sender is not None and spec.from_sender not in prompt:
            return False
        return True

    # --- event emission (synthetic pydantic-ai messages) ---------------------

    def _emit_request(self, user_prompt: Any) -> None:
        """Push the inbound prompt as a ``ModelRequest(UserPromptPart)``."""
        content = self._prompt_text(user_prompt)
        self._context.add_message(
            ModelRequest(parts=[UserPromptPart(content=content)], run_id=self._run_id)
        )

    def _emit_tool_call(self, stub: ToolStub) -> None:
        """Push a ``ModelResponse(ToolCallPart)`` for a simulated tool call."""
        self._context.add_message(
            ModelResponse(
                parts=[
                    ToolCallPart(
                        tool_name=stub.name,
                        args=stub.args,
                        tool_call_id=self._tool_call_id(stub),
                    )
                ],
                run_id=self._run_id,
            )
        )

    def _emit_tool_return(self, stub: ToolStub) -> None:
        """Push a ``ModelRequest(ToolReturnPart)`` with the canned tool result."""
        self._context.add_message(
            ModelRequest(
                parts=[
                    ToolReturnPart(
                        tool_name=stub.name,
                        content=stub.returns,
                        tool_call_id=self._tool_call_id(stub),
                    )
                ],
                run_id=self._run_id,
            )
        )

    def _emit_final_response(self, state: ScenarioState) -> None:
        """Push the final ``ModelResponse(TextPart=<json>)`` with zero-token usage."""
        self._context.add_message(
            ModelResponse(
                parts=[TextPart(content=self._response_text(state))],
                usage=RequestUsage(),
                model_name="mock",
                provider_name="mock",
                run_id=self._run_id,
            )
        )

    # --- output --------------------------------------------------------------

    def _build_output(self, state: ScenarioState, output_type: type[Any] | None) -> Any:
        """Validate a structured ``output_type`` or return the response text."""
        if output_type is not None and output_type is not str and hasattr(
            output_type, "model_validate"
        ):
            messages = [m.model_dump() for m in state.respond.messages]
            return output_type.model_validate({"messages": messages})
        return state.respond.text or ""

    @staticmethod
    def _response_text(state: ScenarioState) -> str:
        """Build the JSON ``TextPart`` content mirroring a real structured output."""
        if state.respond.text is not None:
            return state.respond.text
        from pydantic import TypeAdapter  # noqa: PLC0415

        adapter: TypeAdapter[Any] = TypeAdapter(list[dict[str, str]])
        messages = [m.model_dump() for m in state.respond.messages]
        return '{"messages": ' + adapter.dump_json(messages).decode("utf-8") + "}"

    # --- latency -------------------------------------------------------------

    def _latency_ms(self, state: ScenarioState) -> int:
        """Per-state latency, falling back to the scenario default."""
        if state.latency_ms is not None:
            return state.latency_ms
        return self._scenario.default_latency_ms

    @staticmethod
    async def _sleep(latency_ms: int) -> None:
        """Inject think-time; a zero/negative value adds no measurable delay."""
        if latency_ms > 0:
            await asyncio.sleep(latency_ms / 1000.0)

    # --- ids -----------------------------------------------------------------

    @staticmethod
    def _tool_call_id(stub: ToolStub) -> str:
        """Deterministic-per-instance tool call id matching call and return."""
        return f"mock-{stub.name}-{id(stub)}"

    # --- drop-in surface delegating to ContextManager ------------------------

    @property
    def context(self) -> ContextManager:
        """Underlying context manager (drop-in parity with ``ReactAgent.context``)."""
        return self._context

    def subscribe_context(self, observer: ContextObserver) -> None:
        """Subscribe an observer to context events."""
        self._context.subscribe(observer)

    def restore_context(self, events: list[Any]) -> None:
        """Restore LLM context from persisted events (mirrors ``ReactAgent``)."""
        messages = [
            e.event.message
            for e in events
            if hasattr(e, "event") and isinstance(e.event, LlmMessageEvent)
        ]
        self._context.restore(messages)

    def system_prompt(self, func: Any) -> Any:
        """No-op decorator: returns ``func`` unchanged (no model to register on)."""
        return func

    def tool(self, func: Any) -> Any:
        """No-op decorator: returns ``func`` unchanged (tools are simulated)."""
        return func
