"""Tests for the ``/compact`` + ``/clear`` built-ins and ``compaction_cfg`` threading.

This is the akgentic-agent slice of ADR-010: ``BaseAgent`` supplies two thin
delegating callables (``compact`` -> ``ReactAgent.compact``; ``clear`` ->
``ReactAgent.clear_context``) and threads one config object into the
``ReactAgentConfig`` it builds. All compaction/clear mechanics live in
akgentic-llm; here we assert only the agent-layer wiring and delegation.

Specs stub ``ReactAgent`` — no live LLM backend, no network.
"""

from unittest.mock import MagicMock

from akgentic.core import ActorAddress
from akgentic.llm import CompactionConfig
from akgentic.tool.core import ToolFactory

from akgentic.agent.agent import BaseAgent
from akgentic.agent.config import AgentConfig
from akgentic.agent.messages import AgentMessage


def _bare_agent_with_stubbed_llm() -> BaseAgent:
    """Bare BaseAgent (no Pykka actor system) with a stubbed ReactAgent."""
    agent: BaseAgent = object.__new__(BaseAgent)
    agent._react_agent = MagicMock()  # type: ignore[attr-defined]
    return agent


def _make_sender(name: str = "@Human") -> MagicMock:
    """Return a mock ActorAddress that passes isinstance checks."""
    addr = MagicMock(spec=ActorAddress)
    addr.name = name
    return addr


# =============================================================================
# FR1 — AgentConfig.compaction_cfg field (AC #1)
# =============================================================================


class TestCompactionCfgField:
    """AC #1: AgentConfig gains a compaction_cfg field defaulting to CompactionConfig."""

    def test_default_compaction_cfg_is_compactionconfig_instance(self) -> None:
        """AgentConfig() with no argument yields a CompactionConfig (default factory)."""
        cfg = AgentConfig()
        assert isinstance(cfg.compaction_cfg, CompactionConfig)

    def test_default_is_opt_in_off(self) -> None:
        """The default leaves compaction effectively off (no context_length on the model)."""
        cfg = AgentConfig()
        assert cfg.model_cfg.context_length is None

    def test_explicit_compaction_cfg_roundtrips_unchanged(self) -> None:
        """Passing an explicit CompactionConfig round-trips by value."""
        custom = CompactionConfig(
            strategy="none", auto_trigger=False, keep_recent_messages=9
        )
        cfg = AgentConfig(compaction_cfg=custom)
        assert cfg.compaction_cfg == custom
        assert cfg.compaction_cfg.strategy == "none"
        assert cfg.compaction_cfg.auto_trigger is False
        assert cfg.compaction_cfg.keep_recent_messages == 9


# =============================================================================
# FR2 / FR3 — compact() and clear() delegate (AC #3, #4, #8)
# =============================================================================


class TestCompactClearDelegation:
    """AC #3/#4/#8: the built-ins only delegate to the akgentic-llm bridge."""

    def test_method_names_map_to_slash_commands(self) -> None:
        """__name__ drives the command name: compact -> /compact, clear -> /clear."""
        assert BaseAgent.compact.__name__ == "compact"
        assert BaseAgent.clear.__name__ == "clear"

    def test_compact_delegates_once_and_returns_bridge_status(self) -> None:
        """AC #3: compact() calls _react_agent.compact() once and echoes its value."""
        agent = _bare_agent_with_stubbed_llm()
        agent._react_agent.compact.return_value = (  # type: ignore[attr-defined]
            "Compacted: replaced 3 earlier message(s) with a summary."
        )

        result = agent.compact()

        agent._react_agent.compact.assert_called_once_with()  # type: ignore[attr-defined]
        assert result == "Compacted: replaced 3 earlier message(s) with a summary."
        # Pure delegation — clear path untouched.
        agent._react_agent.clear_context.assert_not_called()  # type: ignore[attr-defined]

    def test_clear_delegates_once_and_returns_bridge_status(self) -> None:
        """AC #4: clear() calls _react_agent.clear_context() once and echoes its value."""
        agent = _bare_agent_with_stubbed_llm()
        agent._react_agent.clear_context.return_value = (  # type: ignore[attr-defined]
            "Cleared 5 message(s); system prompt regenerates on the next run."
        )

        result = agent.clear()

        agent._react_agent.clear_context.assert_called_once_with()  # type: ignore[attr-defined]
        assert result == "Cleared 5 message(s); system prompt regenerates on the next run."
        # Pure delegation — compact path untouched.
        agent._react_agent.compact.assert_not_called()  # type: ignore[attr-defined]

    def test_clear_docstring_states_regeneration_not_preservation(self) -> None:
        """AC #4: clear()'s docstring says the system prompt regenerates, never 'preserves'."""
        doc = (BaseAgent.clear.__doc__ or "").lower()
        assert "regenerat" in doc
        assert "preserv" not in doc


# =============================================================================
# FR4 / FR5 / FR6 — real CommandRegistry dispatch + discovery (AC #5, #6, #7)
# =============================================================================


class TestRealRegistryDispatch:
    """AC #5/#6: both built-ins register and dispatch through a real CommandRegistry."""

    def test_both_appear_in_descriptors_and_dispatch_verbatim(self) -> None:
        """get_command_registry(extra_commands=[...]) exposes /compact and /clear."""
        agent = _bare_agent_with_stubbed_llm()
        agent._react_agent.compact.return_value = "compacted-status"  # type: ignore[attr-defined]
        agent._react_agent.clear_context.return_value = "cleared-status"  # type: ignore[attr-defined]

        registry = ToolFactory(tool_cards=[]).get_command_registry(
            extra_commands=[agent.compact, agent.clear]
        )

        names = {desc.name for desc in registry.descriptors()}
        assert {"compact", "clear"} <= names

        # /compact dispatches to the bridge and echoes its status verbatim.
        assert registry.dispatch("/compact") == "compacted-status"
        agent._react_agent.compact.assert_called_once_with()  # type: ignore[attr-defined]

        # /clear dispatches to clear_context and echoes its status verbatim.
        assert registry.dispatch("/clear") == "cleared-status"
        agent._react_agent.clear_context.assert_called_once_with()  # type: ignore[attr-defined]


class TestOperatorActionLogging:
    """AC #7: dispatching /compact or /clear records exactly one operator action."""

    def _agent_with_real_registry(self) -> BaseAgent:
        agent = _bare_agent_with_stubbed_llm()
        agent._react_agent.compact.return_value = "compacted-status"  # type: ignore[attr-defined]
        agent._react_agent.clear_context.return_value = "cleared-status"  # type: ignore[attr-defined]
        agent._command_registry = ToolFactory(tool_cards=[]).get_command_registry(  # type: ignore[attr-defined]
            extra_commands=[agent.compact, agent.clear]
        )
        agent.send = MagicMock()  # type: ignore[method-assign]
        return agent

    def test_compact_dispatch_records_one_operator_action(self) -> None:
        """/compact rides the unchanged slash path and records one operator action."""
        agent = self._agent_with_real_registry()

        message = AgentMessage(content="/compact", type="request")
        message.sender = _make_sender("@Human")

        handled = agent._dispatch_command(message, _make_sender("@Human"))

        assert handled is True
        record = agent._react_agent.context.record_operator_action  # type: ignore[attr-defined]
        record.assert_called_once()
        assert '"/compact"' in record.call_args[0][0]
        agent.send.assert_called_once()  # type: ignore[attr-defined]

    def test_clear_dispatch_records_one_operator_action(self) -> None:
        """/clear rides the unchanged slash path and records one operator action."""
        agent = self._agent_with_real_registry()

        message = AgentMessage(content="/clear", type="request")
        message.sender = _make_sender("@Human")

        handled = agent._dispatch_command(message, _make_sender("@Human"))

        assert handled is True
        record = agent._react_agent.context.record_operator_action  # type: ignore[attr-defined]
        record.assert_called_once()
        assert '"/clear"' in record.call_args[0][0]
        agent.send.assert_called_once()  # type: ignore[attr-defined]


# =============================================================================
# FR1 threading + FR5 discovery/command-only — through the real ActorSystem (AC #2, #6)
# =============================================================================


class TestOnStartWiring:
    """AC #2/#6: on_start threads compaction_cfg and registers command-only built-ins.

    Exercised through the real actor system (on_start runs on createActor). The
    real ReactAgent is swapped for a capturing fake so no LLM is built; the fake
    records the ReactAgentConfig + tools on_start passes to it.
    """

    def test_threads_compaction_cfg_and_registers_command_only(self) -> None:
        import time

        from akgentic.core import ActorSystem, BaseConfig, EventSubscriber, Orchestrator
        from akgentic.core.messages import EventMessage, Message
        from akgentic.llm import ModelConfig, PromptTemplate, ReactAgentConfig
        from akgentic.tool.event import CommandsAnnouncedEvent

        import akgentic.agent.agent as agent_module

        captured: list[dict] = []

        class _CapturingReactAgent:
            def __init__(self, **kwargs: object) -> None:
                captured.append(kwargs)

            def system_prompt(self, fn: object) -> object:
                return fn

            def close(self) -> None:
                pass

        announced: list[CommandsAnnouncedEvent] = []

        class _Subscriber(EventSubscriber):
            def on_stop(self) -> None:
                pass

            def on_message(self, message: Message) -> None:
                if isinstance(message, EventMessage) and isinstance(
                    message.event, CommandsAnnouncedEvent
                ):
                    announced.append(message.event)

        sentinel = CompactionConfig(
            strategy="summarize", keep_recent_messages=7, trigger_ratio=0.5
        )

        system = ActorSystem()
        original = agent_module.ReactAgent
        agent_module.ReactAgent = _CapturingReactAgent  # type: ignore[misc, assignment]
        try:
            orch_addr = system.createActor(
                Orchestrator, config=BaseConfig(name="@Orchestrator", role="Orchestrator")
            )
            orchestrator = system.proxy_ask(orch_addr, Orchestrator)
            orchestrator.subscribe(_Subscriber())

            config = AgentConfig(
                name="@Manager",
                role="Manager",
                prompt=PromptTemplate(template="You are a manager."),
                model_cfg=ModelConfig(provider="openai", model="gpt-5-mini"),
                compaction_cfg=sentinel,
            )
            manager_addr = orchestrator.createActor(BaseAgent, config=config)

            time.sleep(0.5)

            # AC #2 — the agent's compaction_cfg is threaded into the built config.
            assert captured, "ReactAgent was never constructed"
            built_cfg = captured[-1]["config"]
            assert isinstance(built_cfg, ReactAgentConfig)
            assert built_cfg.compaction_cfg == sentinel
            assert built_cfg.compaction_cfg.keep_recent_messages == 7

            # AC #6 — both built-ins appear in the single CommandsAnnouncedEvent.
            for_agent = [e for e in announced if e.agent.name == manager_addr.name]
            assert len(for_agent) == 1, f"expected one event, got {len(for_agent)}"
            command_names = {desc.name for desc in for_agent[0].commands}
            assert {"compact", "clear"} <= command_names

            # AC #6 — command-only: neither name leaks onto the TOOL_CALL channel.
            tool_names = {getattr(t, "__name__", None) for t in captured[-1]["tools"]}
            assert "compact" not in tool_names
            assert "clear" not in tool_names
        finally:
            agent_module.ReactAgent = original  # type: ignore[misc]
            try:
                system.shutdown(timeout=5)
            except Exception:
                pass
