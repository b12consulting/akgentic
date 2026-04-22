"""Tests for UserProxy agent.

Covers class structure, process_human_input routing, receiveMsg_UserMessage
handler, and integration with ActorSystem and Orchestrator.
"""

from __future__ import annotations

import time
from collections.abc import Generator
from unittest.mock import MagicMock, patch

import pykka
import pytest

from akgentic.core import ActorSystem, BaseConfig, Orchestrator, UserProxy
from akgentic.core.actor_address_impl import ActorAddressImpl
from akgentic.core.agent import Akgent
from akgentic.core.messages.message import Message, ResultMessage, UserMessage
from akgentic.core.messages.orchestrator import SentMessage


@pytest.fixture(autouse=True)
def cleanup_actors() -> Generator[None, None, None]:
    """Ensure all actors are stopped after each test."""
    yield
    pykka.ActorRegistry.stop_all()


class TestUserProxyClass:
    """AC: UserProxy inherits Akgent[BaseConfig, BaseState]."""

    def test_userproxy_inherits_akgent(self) -> None:
        assert issubclass(UserProxy, Akgent)


class TestUserProxyCreation:
    """AC: UserProxy initialization with BaseConfig."""

    def test_userproxy_creation_no_orchestrator(self) -> None:
        """UserProxy starts without an orchestrator."""
        ref = UserProxy.start(config=BaseConfig(name="human", role="UserProxy"))
        assert ref.is_alive()
        ref.stop()

    def test_userproxy_creation_via_actor_system(self) -> None:
        """UserProxy starts via ActorSystem."""
        system = ActorSystem()
        addr = system.createActor(UserProxy, config=BaseConfig(name="human", role="UserProxy"))
        assert addr.is_alive()
        system.shutdown()


class TestUserProxyOrchestratorIntegration:
    """AC: StartMessage sent automatically; handle_user_message flag."""

    def test_userproxy_sends_start_message_to_orchestrator(self) -> None:
        """UserProxy auto-registers with Orchestrator via StartMessage."""
        orch_ref = Orchestrator.start(config=BaseConfig(name="orchestrator", role="Orchestrator"))
        orch_addr = ActorAddressImpl(orch_ref)

        proxy_ref = UserProxy.start(
            config=BaseConfig(name="human", role="UserProxy"), orchestrator=orch_addr
        )
        time.sleep(0.1)

        team = orch_ref.proxy().get_team().get()
        assert len(team) >= 1

        proxy_ref.stop()
        orch_ref.stop()

    def test_userproxy_address_handle_user_message_returns_true(self) -> None:
        """ActorAddress.handle_user_message() is True for UserProxy."""
        proxy_ref = UserProxy.start(config=BaseConfig(name="human", role="UserProxy"))
        assert ActorAddressImpl(proxy_ref).handle_user_message() is True
        proxy_ref.stop()


class TestProcessHumanInput:
    """AC: process_human_input logs and routes ResultMessage to message.sender."""

    def test_process_human_input_sends_result_message(self) -> None:
        """ResultMessage with human content is sent to original message.sender."""
        orch_ref = Orchestrator.start(config=BaseConfig(name="orchestrator", role="Orchestrator"))
        orch_addr = ActorAddressImpl(orch_ref)

        worker_ref = Orchestrator.start(
            config=BaseConfig(name="worker", role="Agent"), orchestrator=orch_addr
        )
        worker_addr = worker_ref.proxy().myAddress.get()

        proxy_ref = UserProxy.start(
            config=BaseConfig(name="human", role="UserProxy"), orchestrator=orch_addr
        )
        proxy = proxy_ref.proxy()

        original_msg = UserMessage(content="Should I proceed?")
        original_msg.sender = worker_addr
        proxy.process_human_input("Yes, proceed", original_msg).get()
        time.sleep(0.1)

        result_msgs = [
            m.message
            for m in orch_ref.proxy().get_messages().get()
            if isinstance(m, SentMessage) and isinstance(m.message, ResultMessage)
        ]
        assert len(result_msgs) >= 1
        assert result_msgs[-1].content == "Yes, proceed"

        proxy_ref.stop()
        worker_ref.stop()
        orch_ref.stop()

    def test_result_message_sent_to_correct_address(self) -> None:
        """process_human_input routes to message.sender, not elsewhere."""
        sent_recipients: list = []
        sent_messages: list[Message] = []

        def capture_send(recipient: object, message: Message) -> None:
            sent_recipients.append(recipient)
            sent_messages.append(message)

        proxy_ref = UserProxy.start(config=BaseConfig(name="human", role="UserProxy"))
        proxy = proxy_ref.proxy()

        mock_addr = MagicMock()
        original_msg = UserMessage(content="route me back")
        original_msg.sender = mock_addr

        with patch.object(UserProxy, "send", side_effect=capture_send):
            proxy.process_human_input("routed response", original_msg).get()

        assert len(sent_recipients) == 1
        assert sent_recipients[0] is mock_addr
        assert isinstance(sent_messages[0], ResultMessage)
        assert sent_messages[0].content == "routed response"

        proxy_ref.stop()


class TestReceiveMsgUserMessage:
    """AC: receiveMsg_UserMessage handles incoming UserMessage; UserProxy sends messages."""

    def test_receivemsg_usermessage_can_be_received_via_tell(self) -> None:
        """UserProxy survives receiving a UserMessage without raising."""
        proxy_ref = UserProxy.start(config=BaseConfig(name="human", role="UserProxy"))
        proxy_ref.tell(UserMessage(content="Are you there?"))
        time.sleep(0.1)
        assert proxy_ref.is_alive()
        proxy_ref.stop()

    def test_userproxy_can_send_usermessage_to_other_agent(self) -> None:
        """UserProxy can send a UserMessage to another agent."""
        orch_ref = Orchestrator.start(config=BaseConfig(name="orchestrator", role="Orchestrator"))
        orch_addr = ActorAddressImpl(orch_ref)

        proxy_ref = UserProxy.start(
            config=BaseConfig(name="human", role="UserProxy"), orchestrator=orch_addr
        )
        recv_ref = Orchestrator.start(
            config=BaseConfig(name="receiver", role="Agent"), orchestrator=orch_addr
        )
        recv_addr = recv_ref.proxy().myAddress.get()

        proxy_ref.proxy().send(recv_addr, UserMessage(content="Hello from UserProxy")).get()
        time.sleep(0.1)

        user_msgs = [
            m.message
            for m in orch_ref.proxy().get_messages().get()
            if isinstance(m, SentMessage) and isinstance(m.message, UserMessage)
        ]
        assert len(user_msgs) >= 1

        proxy_ref.stop()
        recv_ref.stop()
        orch_ref.stop()
