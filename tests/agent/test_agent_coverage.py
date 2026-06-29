"""Tests for BaseAgent methods not covered by test_agent.py or test_simple_team.py.

Covers: process_message, receiveMsg_AgentMessage (incl. slash-command dispatch),
notify_human, on_hire, on_fire, hire_member, and the on_start discovery event.
All tests use _make_minimal_agent pattern — no live actors or LLM calls. The
command surface is exercised through a mocked CommandRegistry.
"""

import uuid
from typing import Any, Callable
from unittest.mock import MagicMock, patch

import pytest
from akgentic.agent.agent import BaseAgent
from akgentic.agent.config import AgentConfig
from akgentic.agent.messages import AgentMessage
from akgentic.agent.output_models import Request, StructuredOutput
from akgentic.core import ActorAddress
from akgentic.tool.errors import CommandNotRecognized
from pydantic_ai import ModelRetry

# =============================================================================
# HELPERS (same pattern as test_agent.py)
# =============================================================================


def _make_mock_sender(name: str = "@Human") -> MagicMock:
    """Return a mock ActorAddress that passes isinstance checks."""
    addr = MagicMock(spec=ActorAddress)
    addr.name = name
    return addr


def _make_mock_message(sender_name: str = "@Human") -> MagicMock:
    """Return a mock AgentMessage with sender set."""
    msg = MagicMock(spec=AgentMessage)
    msg.sender = _make_mock_sender(sender_name)
    msg.type = "request"
    return msg


def _make_registry(callables: dict[str, Callable] | None = None) -> MagicMock:
    """Return a mock CommandRegistry backed by ``callables``.

    - ``has(name)`` → name in callables
    - ``callable(name)`` → callables[name], else raises CommandNotRecognized
    - ``dispatch`` / ``descriptors`` are plain MagicMocks the caller configures.
    """
    table = callables or {}
    registry = MagicMock()
    registry.has.side_effect = lambda name: name in table

    def _callable(name: str) -> Callable:
        try:
            return table[name]
        except KeyError:
            raise CommandNotRecognized(name) from None

    registry.callable.side_effect = _callable
    return registry


def _make_minimal_agent(
    name: str = "@TestAgent", callables: dict[str, Callable] | None = None
) -> BaseAgent:
    """Construct a BaseAgent without Pykka actor system."""
    agent: BaseAgent = object.__new__(BaseAgent)

    agent._command_registry = _make_registry(callables)  # type: ignore[attr-defined]
    agent._react_agent = MagicMock()  # type: ignore[attr-defined]
    agent._current_message = _make_mock_message()  # type: ignore[attr-defined]
    agent.team_id = uuid.uuid4()  # normally injected by Akgent.__init__ / createActor

    mock_config = MagicMock(spec=AgentConfig)
    mock_config.name = name
    agent.config = mock_config  # type: ignore[attr-defined]

    agent.get_team = MagicMock(return_value=[])  # type: ignore[method-assign]
    agent.get_available_roles = MagicMock(return_value=[])  # type: ignore[method-assign]
    agent.send = MagicMock()  # type: ignore[method-assign]
    agent.get_team_member = MagicMock(return_value=None)  # type: ignore[method-assign]

    return agent


# =============================================================================
# process_message
# =============================================================================


class TestProcessMessage:
    """Test process_message routing logic."""

    def test_routes_to_existing_member_via_at_prefix(self) -> None:
        """recipient starting with '@' → get_team_member; content is raw (no prefix)."""
        agent = _make_minimal_agent()

        member_addr = _make_mock_sender("@Assistant")
        agent.get_team_member = MagicMock(return_value=member_addr)  # type: ignore[method-assign]

        output = StructuredOutput(
            messages=[
                Request(
                    recipient="@Assistant",
                    message="do this",
                    message_type="request",
                )
            ]
        )
        agent._react_agent.run_sync.return_value = output  # type: ignore[attr-defined]

        sender = _make_mock_sender("@Human")
        agent.process_message("test content", sender)

        agent.get_team_member.assert_called_once_with("@Assistant")
        agent.send.assert_called_once()
        sent_msg = agent.send.call_args[0][1]
        assert isinstance(sent_msg, AgentMessage)
        assert sent_msg.type == "request"
        assert sent_msg.content == "do this"

    def test_routes_to_hire_member_without_at_prefix(self) -> None:
        """recipient without '@' → hire via registry callable; content is raw."""
        hired_addr = _make_mock_sender("@Developer")
        hire_cmd = MagicMock(return_value=hired_addr)
        agent = _make_minimal_agent(callables={"hire_member": hire_cmd})

        output = StructuredOutput(
            messages=[
                Request(
                    recipient="Developer",
                    message="build this",
                    message_type="instruction",
                )
            ]
        )
        agent._react_agent.run_sync.return_value = output  # type: ignore[attr-defined]

        sender = _make_mock_sender("@Human")
        agent.process_message("test", sender)

        hire_cmd.assert_called_once_with("Developer")
        agent.send.assert_called_once()
        sent_msg = agent.send.call_args[0][1]
        assert sent_msg.type == "instruction"
        assert sent_msg.content == "build this"

    def test_no_send_when_member_not_found(self) -> None:
        """When get_team_member returns None, no message is sent."""
        agent = _make_minimal_agent()
        agent.get_team_member = MagicMock(return_value=None)  # type: ignore[method-assign]

        output = StructuredOutput(
            messages=[
                Request(
                    recipient="@Ghost",
                    message="hello",
                    message_type="notification",
                )
            ]
        )
        agent._react_agent.run_sync.return_value = output  # type: ignore[attr-defined]

        agent.process_message("test", _make_mock_sender())
        agent.send.assert_not_called()

    def test_empty_messages_no_routing(self) -> None:
        """No requests → no routing happens."""
        agent = _make_minimal_agent()
        output = StructuredOutput(messages=[])
        agent._react_agent.run_sync.return_value = output  # type: ignore[attr-defined]

        agent.process_message("test", _make_mock_sender())
        agent.send.assert_not_called()

    def test_sends_raw_content_without_prefix(self) -> None:
        """process_message sends AgentMessage with raw content (no prefix baked in)."""
        agent = _make_minimal_agent()
        member_addr = _make_mock_sender("@Assistant")
        agent.get_team_member = MagicMock(return_value=member_addr)  # type: ignore[method-assign]

        output = StructuredOutput(
            messages=[
                Request(
                    recipient="@Human",
                    message="ack",
                    message_type="acknowledgment",
                )
            ]
        )
        agent._react_agent.run_sync.return_value = output  # type: ignore[attr-defined]

        agent.process_message("test", _make_mock_sender())
        sent_msg = agent.send.call_args[0][1]
        assert sent_msg.content == "ack"
        assert "You received" not in sent_msg.content


# =============================================================================
# receiveMsg_AgentMessage
# =============================================================================


class TestReceiveAgentMessage:
    """Test receiveMsg_AgentMessage handler."""

    @patch("akgentic.agent.agent.sleep")
    def test_calls_process_message_with_reconstructed_prefix(self, mock_sleep: MagicMock) -> None:
        """Normal path: calls process_message with reconstructed prefix from metadata."""
        agent = _make_minimal_agent()
        agent.process_message = MagicMock()  # type: ignore[method-assign]

        msg_sender = _make_mock_sender("@Alice")
        message = AgentMessage(content="hello world", type="request")
        message.sender = msg_sender

        sender = _make_mock_sender("@Alice")

        agent.receiveMsg_AgentMessage(message, sender)

        expected = "You received a request from @Alice:\n\nhello world"
        agent.process_message.assert_called_once_with(expected, sender)

    @patch("akgentic.agent.agent.sleep")
    def test_usage_limit_error_notifies_human(self, mock_sleep: MagicMock) -> None:
        """LLMUsageLimitError → notify_human + WarningError raised."""
        from akgentic.core.agent import WarningError
        from akgentic.llm import UsageLimitError as LLMUsageLimitError

        agent = _make_minimal_agent()
        agent.process_message = MagicMock(  # type: ignore[method-assign]
            side_effect=LLMUsageLimitError("token limit")
        )
        agent.notify_human = MagicMock()  # type: ignore[method-assign]

        message = AgentMessage(content="do something")
        sender = _make_mock_sender("@Human")

        with pytest.raises(WarningError, match="LLM usage limit exceeded"):
            agent.receiveMsg_AgentMessage(message, sender)

        agent.notify_human.assert_called_once()
        assert "exceeded" in agent.notify_human.call_args[0][0]

    @patch("akgentic.agent.agent.sleep")
    def test_reconstructs_prefix_with_a_for_consonant_type(self, mock_sleep: MagicMock) -> None:
        """type='request' (consonant) → prefix uses 'a request'."""
        agent = _make_minimal_agent()
        agent.process_message = MagicMock()  # type: ignore[method-assign]

        message = AgentMessage(content="do this", type="request")
        message.sender = _make_mock_sender("@Bob")

        sender = _make_mock_sender("@Bob")
        agent.receiveMsg_AgentMessage(message, sender)

        expected = "You received a request from @Bob:\n\ndo this"
        agent.process_message.assert_called_once_with(expected, sender)

    @patch("akgentic.agent.agent.sleep")
    def test_reconstructs_prefix_with_an_for_acknowledgment(self, mock_sleep: MagicMock) -> None:
        """type='acknowledgment' (vowel) → prefix uses 'an acknowledgment'."""
        agent = _make_minimal_agent()
        agent.process_message = MagicMock()  # type: ignore[method-assign]

        message = AgentMessage(content="ack", type="acknowledgment")
        message.sender = _make_mock_sender("@Carol")

        agent.receiveMsg_AgentMessage(message, _make_mock_sender("@Carol"))

        called_content = agent.process_message.call_args[0][0]
        assert called_content.startswith("You received an acknowledgment from @Carol:")
        assert called_content.endswith("ack")

    @patch("akgentic.agent.agent.sleep")
    def test_reconstructs_prefix_with_an_for_instruction(self, mock_sleep: MagicMock) -> None:
        """type='instruction' (vowel) → prefix uses 'an instruction'."""
        agent = _make_minimal_agent()
        agent.process_message = MagicMock()  # type: ignore[method-assign]

        message = AgentMessage(content="step 1", type="instruction")
        message.sender = _make_mock_sender("@Manager")

        agent.receiveMsg_AgentMessage(message, _make_mock_sender("@Manager"))

        called_content = agent.process_message.call_args[0][0]
        assert called_content.startswith("You received an instruction from @Manager:")
        assert called_content.endswith("step 1")

    @patch("akgentic.agent.agent.sleep")
    def test_reconstructs_prefix_with_unknown_when_sender_is_none(
        self, mock_sleep: MagicMock
    ) -> None:
        """message.sender is None → fallback to 'unknown'."""
        agent = _make_minimal_agent()
        agent.process_message = MagicMock()  # type: ignore[method-assign]

        message = AgentMessage(content="hello", type="request")
        message.sender = None

        agent.receiveMsg_AgentMessage(message, _make_mock_sender("@Somewhere"))

        called_content = agent.process_message.call_args[0][0]
        assert "from unknown:" in called_content
        assert called_content.endswith("hello")


# =============================================================================
# notify_human
# =============================================================================


class TestNotifyHuman:
    """Test notify_human sends notification to first Human agent."""

    def test_sends_notification_to_human(self) -> None:
        agent = _make_minimal_agent()
        human_member = MagicMock(spec=ActorAddress)
        human_member.name = "@Human"
        human_member.role = "human"
        agent.get_team = MagicMock(return_value=[human_member])  # type: ignore[method-assign]

        agent.notify_human("usage limit exceeded")

        agent.send.assert_called_once()
        sent_msg = agent.send.call_args[0][1]
        assert isinstance(sent_msg, AgentMessage)
        assert sent_msg.type == "notification"
        assert "usage limit exceeded" in sent_msg.content


# =============================================================================
# on_hire / on_fire
# =============================================================================


class TestOnHireOnFire:
    """Test on_hire/on_fire callbacks (no-op but must be callable)."""

    def test_on_hire_is_noop(self) -> None:
        agent = _make_minimal_agent()
        agent.on_hire(MagicMock())  # should not raise

    def test_on_fire_is_noop(self) -> None:
        agent = _make_minimal_agent()
        agent.on_fire(MagicMock())  # should not raise


# =============================================================================
# hire_member
# =============================================================================


class TestHireMember:
    """Test hire_member resolves and invokes the registry callable (AC-8)."""

    def test_delegates_to_registry_callable(self) -> None:
        mock_addr = MagicMock()
        hire_cmd = MagicMock(return_value=mock_addr)
        agent = _make_minimal_agent(callables={"hire_member": hire_cmd})

        result = agent.hire_member("Developer")

        hire_cmd.assert_called_once_with("Developer")
        assert result == mock_addr

    def test_raises_when_command_not_registered(self) -> None:
        agent = _make_minimal_agent()  # registry has no hire_member

        with pytest.raises(RuntimeError, match="hire_member command not available"):
            agent.hire_member("Developer")

    def test_propagates_model_retry(self) -> None:
        """ModelRetry from the typed callable is propagated (not swallowed)."""
        hire_cmd = MagicMock(side_effect=ModelRetry("role not found"))
        agent = _make_minimal_agent(callables={"hire_member": hire_cmd})

        with pytest.raises(ModelRetry, match="role not found"):
            agent.hire_member("Unknown")


# =============================================================================
# Slash-command dispatch (receiveMsg_AgentMessage → _dispatch_command)
# =============================================================================


class TestSlashCommandDispatch:
    """AC-3/4/5: /-prefixed interception, fallback, and post-id failure."""

    @patch("akgentic.agent.agent.sleep")
    def test_dispatch_success_sends_result_no_llm(self, mock_sleep: MagicMock) -> None:
        """AC-3: known /command → dispatch result sent to sender, LLM not called."""
        agent = _make_minimal_agent()
        agent._command_registry.dispatch.return_value = "Member @Dev123 hired."  # type: ignore[attr-defined]
        agent.process_message = MagicMock()  # type: ignore[method-assign]

        message = AgentMessage(content="/hire Developer", type="request")
        message.sender = _make_mock_sender("@Human")
        sender = _make_mock_sender("@Human")

        agent.receiveMsg_AgentMessage(message, sender)

        agent._command_registry.dispatch.assert_called_once_with("/hire Developer")  # type: ignore[attr-defined]
        agent.process_message.assert_not_called()
        agent.send.assert_called_once()
        sent_to, sent_msg = agent.send.call_args[0]
        assert sent_to is sender
        assert isinstance(sent_msg, AgentMessage)
        assert sent_msg.type == "notification"
        assert sent_msg.content == "Member @Dev123 hired."

    @patch("akgentic.agent.agent.sleep")
    def test_command_not_recognized_falls_back_to_llm(self, mock_sleep: MagicMock) -> None:
        """AC-4: CommandNotRecognized → original content goes to the LLM path."""
        agent = _make_minimal_agent()
        agent._command_registry.dispatch.side_effect = CommandNotRecognized("etc")  # type: ignore[attr-defined]
        agent.process_message = MagicMock()  # type: ignore[method-assign]

        message = AgentMessage(content="/etc/passwd", type="request")
        message.sender = _make_mock_sender("@Human")
        sender = _make_mock_sender("@Human")

        agent.receiveMsg_AgentMessage(message, sender)

        # No result string sent for the dispatch attempt
        agent.send.assert_not_called()
        # Fell back to the normal path with the prefixed ORIGINAL content
        agent.process_message.assert_called_once()
        prefixed = agent.process_message.call_args[0][0]
        assert prefixed.endswith("/etc/passwd")
        assert "You received a request from @Human:" in prefixed

    @patch("akgentic.agent.agent.sleep")
    def test_post_identification_failure_returns_string_no_llm(self, mock_sleep: MagicMock) -> None:
        """AC-5: known command with bad args → dispatch returns a string, no LLM."""
        agent = _make_minimal_agent()
        agent._command_registry.dispatch.return_value = (  # type: ignore[attr-defined]
            "Command 'hire_member' failed: requires at least 1 argument(s), got 0"
        )
        agent.process_message = MagicMock()  # type: ignore[method-assign]

        message = AgentMessage(content="/hire_member", type="request")
        message.sender = _make_mock_sender("@Human")
        sender = _make_mock_sender("@Human")

        agent.receiveMsg_AgentMessage(message, sender)

        agent.process_message.assert_not_called()
        agent.send.assert_called_once()
        sent_msg = agent.send.call_args[0][1]
        assert sent_msg.type == "notification"
        assert "failed" in sent_msg.content

    @patch("akgentic.agent.agent.sleep")
    def test_non_slash_content_never_dispatches(self, mock_sleep: MagicMock) -> None:
        """Plain (non-/) content bypasses dispatch entirely and goes to the LLM."""
        agent = _make_minimal_agent()
        agent.process_message = MagicMock()  # type: ignore[method-assign]

        message = AgentMessage(content="hello there", type="request")
        message.sender = _make_mock_sender("@Human")

        agent.receiveMsg_AgentMessage(message, _make_mock_sender("@Human"))

        agent._command_registry.dispatch.assert_not_called()  # type: ignore[attr-defined]
        agent.process_message.assert_called_once()


# =============================================================================
# on_start discovery event (CommandsAnnouncedEvent) — actor-system level
# =============================================================================


class TestCommandsAnnouncedEventEmission:
    """AC-2: on_start emits exactly one CommandsAnnouncedEvent for the agent."""

    def test_on_start_emits_single_discovery_event(self) -> None:
        """Creating a BaseAgent emits one CommandsAnnouncedEvent(agent==myAddress).

        Exercised through the real actor system (on_start runs on createActor);
        no LLM call happens because no message is sent. A subscriber captures the
        orchestrator event stream and we assert exactly one CommandsAnnouncedEvent
        is announced for this agent, carrying the registry descriptors.
        """
        import time

        from akgentic.core import ActorSystem, BaseConfig, EventSubscriber, Orchestrator
        from akgentic.core.messages import EventMessage, Message
        from akgentic.llm import ModelConfig, PromptTemplate
        from akgentic.tool.event import CommandsAnnouncedEvent

        announced: list[CommandsAnnouncedEvent] = []

        class _Subscriber(EventSubscriber):
            def on_stop(self) -> None:
                pass

            def on_message(self, message: Message) -> None:
                if isinstance(message, EventMessage) and isinstance(
                    message.event, CommandsAnnouncedEvent
                ):
                    announced.append(message.event)

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
                prompt=PromptTemplate(template="You are a manager."),
                model_cfg=ModelConfig(provider="openai", model="gpt-5-mini"),
            )
            manager_addr = orchestrator.createActor(BaseAgent, config=config)

            time.sleep(0.5)

            for_agent = [e for e in announced if e.agent.name == manager_addr.name]
            assert len(for_agent) == 1, f"expected exactly one event, got {len(for_agent)}"
            event = for_agent[0]
            # TeamTool is auto-injected, so the registry exposes hire/fire commands.
            command_names = {desc.name for desc in event.commands}
            assert "hire_member" in command_names
            assert "fire_member" in command_names
        finally:
            try:
                system.shutdown(timeout=5)
            except Exception:
                pass


# =============================================================================
# _dispatch_command helper (unit level)
# =============================================================================


class TestDispatchCommandHelper:
    """Unit coverage of the _dispatch_command helper return contract."""

    def test_dispatch_command_helper_emits_nothing_extra(self) -> None:
        """_dispatch_command returns False on CommandNotRecognized (fallback signal)."""
        agent = _make_minimal_agent()
        agent._command_registry.dispatch.side_effect = CommandNotRecognized("x")  # type: ignore[attr-defined]

        message = AgentMessage(content="/x", type="request")
        message.sender = _make_mock_sender("@Human")

        handled = agent._dispatch_command(message, _make_mock_sender("@Human"))

        assert handled is False
        agent.send.assert_not_called()

    def test_dispatch_command_helper_sends_and_returns_true(self) -> None:
        """_dispatch_command sends the result and returns True on success."""
        agent = _make_minimal_agent()
        agent._command_registry.dispatch.return_value = "ok"  # type: ignore[attr-defined]

        message = AgentMessage(content="/roster", type="request")
        message.sender = _make_mock_sender("@Human")
        sender = _make_mock_sender("@Human")

        handled = agent._dispatch_command(message, sender)

        assert handled is True
        agent.send.assert_called_once()
        sent_msg = agent.send.call_args[0][1]
        assert sent_msg.content == "ok"
        assert sent_msg.type == "notification"


# =============================================================================
# Operator-action LLM-context delegation
# =============================================================================


class TestOperatorActionInjection:
    """AC1/AC5: the agent delegates each operator action to the LLM context.

    The agent no longer owns the buffer-vs-append decision or pydantic-ai's
    first-run injection rule — it builds the canonical entry string and hands it
    to ``context.record_operator_action``. The buffering itself is owned and
    unit-tested in ``akgentic-llm``.
    """

    def test_inject_helper_delegates_canonical_entry_to_context(self) -> None:
        """AC1: _inject_operator_action builds the canonical human-attributed
        entry and delegates it to context.record_operator_action — nothing else."""
        agent = _make_minimal_agent()

        agent._inject_operator_action("/hire Developer", "hired @Developer42")

        agent._react_agent.context.record_operator_action.assert_called_once_with(  # type: ignore[attr-defined]
            '[Operator action] The human ran "/hire Developer". \nResult:\nhired @Developer42'
        )

    def test_entry_is_human_subject_never_agent_first_person(self) -> None:
        """AC1: 'The human ran' phrasing; no agent first-person framing."""
        agent = _make_minimal_agent()

        agent._inject_operator_action("/hire Developer", "hired @Dev9")

        entry = agent._react_agent.context.record_operator_action.call_args[0][0]  # type: ignore[attr-defined]
        assert "The human ran" in entry
        assert "I ran" not in entry
        assert "I hired" not in entry

    def test_dispatched_command_delegates_one_entry_and_sends_result(self) -> None:
        """AC1/AC5: a dispatched command records exactly one operator action via
        the context and sends the result back to the human (one send)."""
        agent = _make_minimal_agent()
        agent._command_registry.dispatch.return_value = "hired @Developer42"  # type: ignore[attr-defined]

        message = AgentMessage(content="/hire Developer", type="request")
        message.sender = _make_mock_sender("@Human")

        handled = agent._dispatch_command(message, _make_mock_sender("@Human"))

        assert handled is True
        agent._react_agent.context.record_operator_action.assert_called_once_with(  # type: ignore[attr-defined]
            '[Operator action] The human ran "/hire Developer". \nResult:\nhired @Developer42'
        )
        agent.send.assert_called_once()

    def test_command_not_recognized_delegates_nothing(self) -> None:
        """AC5: CommandNotRecognized → no delegation, no send, returns False."""
        agent = _make_minimal_agent()
        agent._command_registry.dispatch.side_effect = CommandNotRecognized("frob")  # type: ignore[attr-defined]

        message = AgentMessage(content="/frobnicate", type="request")
        message.sender = _make_mock_sender("@Human")

        handled = agent._dispatch_command(message, _make_mock_sender("@Human"))

        assert handled is False
        agent._react_agent.context.record_operator_action.assert_not_called()  # type: ignore[attr-defined]
        agent.send.assert_not_called()

    def test_post_identification_failure_delegates_error_result(self) -> None:
        """AC5: bad-args dispatch returns a usage string → one delegated entry
        carrying the error result, plus one send to the human."""
        agent = _make_minimal_agent()
        agent._command_registry.dispatch.return_value = "usage: /hire <role>"  # type: ignore[attr-defined]

        message = AgentMessage(content="/hire", type="request")
        message.sender = _make_mock_sender("@Human")

        handled = agent._dispatch_command(message, _make_mock_sender("@Human"))

        assert handled is True
        agent._react_agent.context.record_operator_action.assert_called_once_with(  # type: ignore[attr-defined]
            '[Operator action] The human ran "/hire". \nResult:\nusage: /hire <role>'
        )
        agent.send.assert_called_once()  # exactly one send to sender


# =============================================================================
# init_llm_context (ADR-009 Layer 3 pass-through)
# =============================================================================


class TestInitLlmContext:
    """Test init_llm_context pass-through to ReactAgent.restore_context."""

    def test_passes_events_through_to_react_agent(self) -> None:
        """AC-1/AC-2: init_llm_context delegates to _react_agent.restore_context
        with the exact same list object (no filtering, no transformation)."""
        agent = _make_minimal_agent()
        events: list[Any] = [MagicMock(), MagicMock(), MagicMock()]

        agent.init_llm_context(events)

        agent._react_agent.restore_context.assert_called_once_with(events)  # type: ignore[attr-defined]
        # Verify identity — same object, not a copy
        passed_arg = agent._react_agent.restore_context.call_args[0][0]  # type: ignore[attr-defined]
        assert passed_arg is events

    def test_passes_empty_list(self) -> None:
        """Edge case: empty event list is forwarded unchanged."""
        agent = _make_minimal_agent()
        events: list[Any] = []

        agent.init_llm_context(events)

        agent._react_agent.restore_context.assert_called_once_with(events)  # type: ignore[attr-defined]
