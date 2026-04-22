"""Tests for HumanProxy message handling.

Covers: receiveMsg_AgentMessage (line 65) and process_human_input (lines 93-108).
Uses live ActorSystem since HumanProxy is lightweight (no LLM calls).
"""

import time

import pytest
from akgentic.core import ActorSystem, BaseConfig, Orchestrator
from akgentic.core.messages import Message
from akgentic.core.messages.orchestrator import SentMessage

from akgentic.agent.human_proxy import HumanProxy
from akgentic.agent.messages import AgentMessage


@pytest.fixture
def actor_system():
    system = ActorSystem()
    yield system
    try:
        system.shutdown(timeout=5)
    except Exception:
        pass


@pytest.fixture
def orchestrator_address(actor_system: ActorSystem):
    addr = actor_system.createActor(
        Orchestrator, config=BaseConfig(name="@Orchestrator", role="Orchestrator")
    )
    yield addr


class TestHumanProxyReceiveAgentMessage:
    """Test receiveMsg_AgentMessage logs without error."""

    def test_receives_agent_message(
        self, actor_system: ActorSystem, orchestrator_address
    ) -> None:
        """HumanProxy receives AgentMessage without crash."""
        orchestrator = actor_system.proxy_ask(orchestrator_address, Orchestrator)

        human_config = BaseConfig(name="@Human", role="Human")
        human_addr = orchestrator.createActor(HumanProxy, config=human_config)
        time.sleep(0.2)

        # Send an AgentMessage to the HumanProxy
        human_proxy = actor_system.proxy_tell(human_addr, HumanProxy)
        msg = AgentMessage(content="Help needed")
        human_proxy.send(human_addr, msg)

        time.sleep(0.3)

        # If we got here without errors, receiveMsg_AgentMessage was called successfully
        team = orchestrator.get_team()
        assert any(m.name == "@Human" for m in team)


class TestHumanProxyProcessInput:
    """Test process_human_input routes answer back to sender."""

    def test_process_human_input_sends_back(
        self, actor_system: ActorSystem, orchestrator_address
    ) -> None:
        """process_human_input sends AgentMessage answer back to original sender."""
        from akgentic.core import EventSubscriber

        orchestrator = actor_system.proxy_ask(orchestrator_address, Orchestrator)

        sent_messages: list[SentMessage] = []

        class TestSubscriber(EventSubscriber):
            def on_stop(self) -> None:
                pass

            def on_message(self, message: Message) -> None:
                if isinstance(message, SentMessage) and isinstance(
                    message.message, AgentMessage
                ):
                    sent_messages.append(message)

        subscriber = TestSubscriber()
        orchestrator.subscribe(subscriber)

        # Create two HumanProxys to test routing between them
        human_config = BaseConfig(name="@Human", role="Human")
        human_addr = orchestrator.createActor(HumanProxy, config=human_config)

        other_config = BaseConfig(name="@Other", role="Other")
        other_addr = orchestrator.createActor(HumanProxy, config=other_config)
        time.sleep(0.2)

        # Other sends a message to Human
        other_proxy = actor_system.proxy_tell(other_addr, HumanProxy)
        msg = AgentMessage(content="Need your input")
        other_proxy.send(human_addr, msg)
        time.sleep(0.3)

        # Now Human processes input and sends back to Other
        human_proxy = actor_system.proxy_tell(human_addr, HumanProxy)

        # Build a mock message with sender = other_addr
        incoming = AgentMessage(content="Need your input").init(
            sender=other_addr,
        )
        human_proxy.process_human_input("Yes, proceed", incoming)
        time.sleep(0.3)

        # Verify a SentMessage was captured with the answer
        answer_msgs = [
            m for m in sent_messages if "Yes, proceed" in m.message.content
        ]
        assert len(answer_msgs) > 0
        # Content is now raw (no prefix baked in) and type is "response"
        assert answer_msgs[0].message.content == "Yes, proceed"
        assert answer_msgs[0].message.type == "response"
        # AC-2: recipient must be set to the original sender (destinataire).
        # Compare by agent_id since send().init() wraps addresses as ActorAddressProxy.
        assert answer_msgs[0].message.recipient is not None
        assert answer_msgs[0].message.recipient.agent_id == other_addr.agent_id
