"""Tests for creation-time system-prompt behavior (Epic 9, Story 9.1).

``BaseAgent.on_start`` no longer pre-seeds the pydantic-ai run buffer and no
longer emits a creation-time event: the prior ``record_initial_system_prompt``
call is retired. A never-run agent keeps an EMPTY run buffer so pydantic-ai's
dynamic ``@system_prompt`` injection (backstory, date, roster, role profiles,
mailbox) stays live on the first run. The never-run backstory is surfaced
client-side from ``AgentState.backstory`` instead of a backend event.

These tests assert the observable behavior:

- FR1/FR6: after ``on_start`` (before any run) NO ``LlmSystemPromptEvent`` (nor
  any ``LlmMessageEvent``) is emitted — there is no backend creation event, and
  nothing has been appended to the run buffer.

The operator-action buffering that protects this empty run buffer now lives in
``akgentic-llm`` (the agent merely delegates via
``context.record_operator_action``), so its buffer-vs-append behavior is
unit-tested there rather than here.

No assertion references any "ADR-NNN" string (Golden Rule #8).
"""

import time

from akgentic.core import ActorSystem, BaseConfig, EventSubscriber, Orchestrator
from akgentic.core.messages import EventMessage, Message
from akgentic.llm import LlmMessageEvent, LlmSystemPromptEvent, ModelConfig, PromptTemplate

from akgentic.agent.agent import BaseAgent
from akgentic.agent.config import AgentConfig


class TestOnStartEmitsNoCreationEvent:
    """A never-run agent emits no creation-time LLM event (empty run buffer)."""

    def test_on_start_emits_no_system_prompt_or_message_event(self) -> None:
        """FR1/FR6: on_start seeds nothing and emits no creation event.

        Exercised through the real actor system: ``on_start`` runs on
        ``createActor`` and no LLM call happens because no message is sent. A
        subscriber captures the orchestrator event stream; we assert NO
        ``LlmSystemPromptEvent`` and NO ``LlmMessageEvent`` for this session.
        Either would imply the run buffer was seeded at creation.
        """
        backstory_text = "You are a manager who delegates clearly."

        sys_events: list[LlmSystemPromptEvent] = []
        msg_events: list[LlmMessageEvent] = []

        class _Subscriber(EventSubscriber):
            def on_stop(self) -> None:
                pass

            def on_message(self, message: Message) -> None:
                if not isinstance(message, EventMessage):
                    return
                if isinstance(message.event, LlmSystemPromptEvent):
                    sys_events.append(message.event)
                elif isinstance(message.event, LlmMessageEvent):
                    msg_events.append(message.event)

        system = ActorSystem()
        try:
            orch_addr = system.createActor(
                Orchestrator, config=BaseConfig(name="@Orchestrator", role="Orchestrator")
            )
            orchestrator = system.proxy_ask(orch_addr, Orchestrator)
            orchestrator.subscribe(_Subscriber())

            config = AgentConfig(
                name="@Manager",
                role="Manager",
                prompt=PromptTemplate(template=backstory_text),
                model_cfg=ModelConfig(provider="openai", model="gpt-5-mini"),
            )
            orchestrator.createActor(BaseAgent, config=config)

            time.sleep(0.5)

            # FR1/FR6: no creation-time system-prompt event and no run-buffer
            # message were produced — the run buffer is empty for a never-run agent.
            assert sys_events == [], f"expected no creation event, got {len(sys_events)}"
            assert msg_events == [], f"expected no run-buffer message, got {len(msg_events)}"
        finally:
            try:
                system.shutdown(timeout=5)
            except Exception:
                pass
