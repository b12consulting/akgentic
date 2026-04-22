"""Tests for BaseAgent methods not covered by test_agent.py or test_simple_team.py.

Covers: process_message, receiveMsg_AgentMessage, notify_human,
on_hire, on_fire, hire_member, cmd_* commands.
All tests use _make_minimal_agent pattern — no live actors or LLM calls.
"""

from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from pydantic_ai import ModelRetry

from akgentic.agent.agent import BaseAgent
from akgentic.agent.config import AgentConfig
from akgentic.agent.messages import AgentMessage
from akgentic.agent.output_models import Request, StructuredOutput
from akgentic.core import ActorAddress


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


def _make_minimal_agent(name: str = "@TestAgent") -> BaseAgent:
    """Construct a BaseAgent without Pykka actor system."""
    agent: BaseAgent = object.__new__(BaseAgent)

    agent._expand_media_refs_command = None  # type: ignore[attr-defined]
    agent._react_agent = MagicMock()  # type: ignore[attr-defined]
    agent._current_message = _make_mock_message()  # type: ignore[attr-defined]
    agent._hire_member_command = None  # type: ignore[attr-defined]
    agent._fire_member_command = None  # type: ignore[attr-defined]
    agent._get_planning_command = None  # type: ignore[attr-defined]
    agent._get_planning_task_command = None  # type: ignore[attr-defined]
    agent._get_team_roster_command = None  # type: ignore[attr-defined]
    agent._get_role_profiles_command = None  # type: ignore[attr-defined]

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
        """recipient without '@' → hire_member; content is raw (no prefix)."""
        agent = _make_minimal_agent()

        hired_addr = _make_mock_sender("@Developer")
        agent._hire_member_command = MagicMock(return_value=hired_addr)  # type: ignore[attr-defined]

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

        agent._hire_member_command.assert_called_once_with("Developer")  # type: ignore[attr-defined]
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
    """Test hire_member command delegation."""

    def test_delegates_to_command(self) -> None:
        agent = _make_minimal_agent()
        mock_addr = MagicMock()
        agent._hire_member_command = MagicMock(return_value=mock_addr)  # type: ignore[attr-defined]

        result = agent.hire_member("Developer")

        agent._hire_member_command.assert_called_once_with("Developer")  # type: ignore[attr-defined]
        assert result == mock_addr

    def test_raises_when_command_not_available(self) -> None:
        agent = _make_minimal_agent()
        agent._hire_member_command = None  # type: ignore[attr-defined]

        with pytest.raises(RuntimeError, match="hire_member command not available"):
            agent.hire_member("Developer")


# =============================================================================
# cmd_hire_member
# =============================================================================


class TestCmdHireMember:
    """Test cmd_hire_member (catches ModelRetry)."""

    def test_success_returns_address(self) -> None:
        agent = _make_minimal_agent()
        mock_addr = MagicMock()
        agent._hire_member_command = MagicMock(return_value=mock_addr)  # type: ignore[attr-defined]

        result = agent.cmd_hire_member("Developer")
        assert result == mock_addr

    def test_model_retry_returns_error_string(self) -> None:
        agent = _make_minimal_agent()
        agent._hire_member_command = MagicMock(  # type: ignore[attr-defined]
            side_effect=ModelRetry("role not found")
        )

        result = agent.cmd_hire_member("Unknown")
        assert isinstance(result, str)
        assert "role not found" in result


# =============================================================================
# cmd_fire_member
# =============================================================================


class TestCmdFireMember:
    """Test cmd_fire_member."""

    def test_success_returns_confirmation(self) -> None:
        agent = _make_minimal_agent()
        agent._fire_member_command = MagicMock(return_value="Fired @Dev")  # type: ignore[attr-defined]

        result = agent.cmd_fire_member("@Dev")
        assert result == "Fired @Dev"

    def test_raises_when_command_not_available(self) -> None:
        agent = _make_minimal_agent()
        agent._fire_member_command = None  # type: ignore[attr-defined]

        with pytest.raises(RuntimeError, match="fire_member command not available"):
            agent.cmd_fire_member("@Dev")

    def test_model_retry_returns_error_string(self) -> None:
        agent = _make_minimal_agent()
        agent._fire_member_command = MagicMock(  # type: ignore[attr-defined]
            side_effect=ModelRetry("cannot fire")
        )

        result = agent.cmd_fire_member("@Dev")
        assert isinstance(result, str)
        assert "cannot fire" in result


# =============================================================================
# cmd_get_planning
# =============================================================================


class TestCmdGetPlanning:
    """Test cmd_get_planning."""

    def test_success(self) -> None:
        agent = _make_minimal_agent()
        agent._get_planning_command = MagicMock(return_value="Sprint 1 tasks")  # type: ignore[attr-defined]

        result = agent.cmd_get_planning()
        assert result == "Sprint 1 tasks"

    def test_raises_when_not_available(self) -> None:
        agent = _make_minimal_agent()
        with pytest.raises(RuntimeError, match="get_planning command not available"):
            agent.cmd_get_planning()


# =============================================================================
# cmd_get_team_roster
# =============================================================================


class TestCmdGetTeamRoster:
    """Test cmd_get_team_roster."""

    def test_success(self) -> None:
        agent = _make_minimal_agent()
        agent._get_team_roster_command = MagicMock(return_value="@Manager, @Dev")  # type: ignore[attr-defined]

        result = agent.cmd_get_team_roster()
        assert result == "@Manager, @Dev"

    def test_raises_when_not_available(self) -> None:
        agent = _make_minimal_agent()
        with pytest.raises(RuntimeError, match="get_team_roster command not available"):
            agent.cmd_get_team_roster()


# =============================================================================
# cmd_get_role_profiles
# =============================================================================


class TestCmdGetRoleProfiles:
    """Test cmd_get_role_profiles."""

    def test_success(self) -> None:
        agent = _make_minimal_agent()
        agent._get_role_profiles_command = MagicMock(return_value="Developer: builds")  # type: ignore[attr-defined]

        result = agent.cmd_get_role_profiles()
        assert result == "Developer: builds"

    def test_raises_when_not_available(self) -> None:
        agent = _make_minimal_agent()
        with pytest.raises(RuntimeError, match="get_role_profiles command not available"):
            agent.cmd_get_role_profiles()


# =============================================================================
# cmd_get_planning_task
# =============================================================================


class TestCmdGetPlanningTask:
    """Test cmd_get_planning_task."""

    def test_success(self) -> None:
        agent = _make_minimal_agent()
        mock_task = MagicMock()
        agent._get_planning_task_command = MagicMock(return_value=mock_task)  # type: ignore[attr-defined]

        result = agent.cmd_get_planning_task(42)
        agent._get_planning_task_command.assert_called_once_with(42)  # type: ignore[attr-defined]
        assert result == mock_task

    def test_raises_when_not_available(self) -> None:
        agent = _make_minimal_agent()
        with pytest.raises(RuntimeError, match="get_planning_task command not available"):
            agent.cmd_get_planning_task(1)


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
