"""Tests for LocalTeamHandle — community-tier TeamHandle adapter."""

from __future__ import annotations

from unittest.mock import MagicMock

from akgentic.infra.adapters.community.local_team_handle import LocalTeamHandle
from akgentic.infra.protocols.team_handle import TeamHandle


class TestLocalTeamHandleProtocolConformance:
    """AC1: LocalTeamHandle satisfies the TeamHandle protocol."""

    def test_isinstance_check(self) -> None:
        """isinstance(LocalTeamHandle(...), TeamHandle) returns True."""
        runtime = MagicMock()
        handle = LocalTeamHandle(runtime)
        assert isinstance(handle, TeamHandle)


class TestLocalTeamHandleSend:
    """AC1: send() delegates to TeamRuntime.send()."""

    def test_send_delegates_to_runtime(self) -> None:
        """send(content) calls self._runtime.send(content)."""
        runtime = MagicMock()
        handle = LocalTeamHandle(runtime)
        handle.send("hello")
        runtime.send.assert_called_once_with("hello")

    def test_send_passes_content_exactly(self) -> None:
        """Content string is forwarded unchanged."""
        runtime = MagicMock()
        handle = LocalTeamHandle(runtime)
        handle.send("multi\nline\ncontent")
        runtime.send.assert_called_once_with("multi\nline\ncontent")

    def test_send_forwards_message_instance_untouched(self) -> None:
        """A pre-formed Message is passed through to runtime.send() untouched."""
        runtime = MagicMock()
        message = MagicMock()
        handle = LocalTeamHandle(runtime)
        handle.send(message)
        runtime.send.assert_called_once_with(message)
        assert runtime.send.call_args.args[0] is message


class TestLocalTeamHandleSendTo:
    """AC1: send_to() delegates to TeamRuntime.send_to()."""

    def test_send_to_delegates_to_runtime(self) -> None:
        """send_to(agent_name, content) calls runtime.send_to(agent_name, content)."""
        runtime = MagicMock()
        handle = LocalTeamHandle(runtime)
        handle.send_to("analyst", "do analysis")
        runtime.send_to.assert_called_once_with("analyst", "do analysis")

    def test_send_to_forwards_message_instance_untouched(self) -> None:
        """A pre-formed Message is passed through to runtime.send_to() untouched."""
        runtime = MagicMock()
        message = MagicMock()
        handle = LocalTeamHandle(runtime)
        handle.send_to("analyst", message)
        runtime.send_to.assert_called_once_with("analyst", message)
        assert runtime.send_to.call_args.args[1] is message


class TestLocalTeamHandleSendFromTo:
    """AC2: send_from_to() delegates to TeamRuntime.send_from_to()."""

    def test_send_from_to_delegates_to_runtime(self) -> None:
        """send_from_to(sender, recipient, content) calls runtime.send_from_to()."""
        runtime = MagicMock()
        handle = LocalTeamHandle(runtime)
        handle.send_from_to("@Developer", "@Manager", "hello")
        runtime.send_from_to.assert_called_once_with("@Developer", "@Manager", "hello")

    def test_send_from_to_forwards_message_instance_untouched(self) -> None:
        """A pre-formed Message is passed through to runtime.send_from_to() untouched."""
        runtime = MagicMock()
        message = MagicMock()
        handle = LocalTeamHandle(runtime)
        handle.send_from_to("@Developer", "@Manager", message)
        runtime.send_from_to.assert_called_once_with("@Developer", "@Manager", message)
        assert runtime.send_from_to.call_args.args[2] is message


class TestLocalTeamHandleEmitMessage:
    """emitMessage() delegates to TeamRuntime.emitMessage() (ADR-22)."""

    def test_emit_message_delegates_to_runtime(self) -> None:
        """emitMessage(message) calls runtime.emitMessage(message) once, same instance."""
        runtime = MagicMock()
        message = MagicMock()
        handle = LocalTeamHandle(runtime)
        handle.emitMessage(message)
        runtime.emitMessage.assert_called_once_with(message)
        assert runtime.emitMessage.call_args.args[0] is message


class TestLocalTeamHandleProcessHumanInput:
    """AC1: process_human_input() delegates to TeamRuntime."""

    def test_process_human_input_delegates_to_runtime(self) -> None:
        """process_human_input(content, message) calls runtime.process_human_input()."""
        runtime = MagicMock()
        message = MagicMock()
        handle = LocalTeamHandle(runtime)
        handle.process_human_input("user input", message)
        runtime.process_human_input.assert_called_once_with("user input", message)


class TestLocalTeamHandleSubscribe:
    """AC2: subscribe() delegates via runtime.orchestrator_proxy."""

    def test_subscribe_delegates_via_orchestrator_proxy(self) -> None:
        """subscribe(subscriber) calls runtime.orchestrator_proxy.subscribe()."""
        runtime = MagicMock()
        subscriber = MagicMock()
        handle = LocalTeamHandle(runtime)
        handle.subscribe(subscriber)
        runtime.orchestrator_proxy.subscribe.assert_called_once_with(subscriber)


class TestLocalTeamHandleUnsubscribe:
    """AC3: unsubscribe() delegates via runtime.orchestrator_proxy."""

    def test_unsubscribe_delegates_via_orchestrator_proxy(self) -> None:
        """unsubscribe(subscriber) calls runtime.orchestrator_proxy.unsubscribe()."""
        runtime = MagicMock()
        subscriber = MagicMock()
        handle = LocalTeamHandle(runtime)
        handle.unsubscribe(subscriber)
        runtime.orchestrator_proxy.unsubscribe.assert_called_once_with(subscriber)
