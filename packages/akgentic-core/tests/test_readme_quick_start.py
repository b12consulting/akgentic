"""Tests for the README Quick Start snippet.

Verifies the minimal example shown in akgentic-core/README.md works exactly
as documented:
- Custom message type (EchoMessage)
- Agent with receiveMsg_<Type> handler (EchoAgent)
- ActorSystem.createActor() / tell() / shutdown() lifecycle
"""

import time

from akgentic.core import ActorAddress, ActorSystem, Akgent
from akgentic.core.messages import Message

# ---------------------------------------------------------------------------
# Replicate the README snippet as local classes so tests are self-contained.
# ---------------------------------------------------------------------------


class EchoMessage(Message):
    content: str


class EchoAgent(Akgent):
    def receiveMsg_EchoMessage(self, message: EchoMessage, sender: ActorAddress) -> None:
        print(f"EchoAgent received: {message.content}")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestEchoMessage:
    """EchoMessage behaves as documented."""

    def test_is_message_subclass(self) -> None:
        assert issubclass(EchoMessage, Message)

    def test_content_field(self) -> None:
        msg = EchoMessage(content="Hello, Akgentic!")
        assert msg.content == "Hello, Akgentic!"


class TestEchoAgent:
    """EchoAgent behaves as documented."""

    def test_is_akgent_subclass(self) -> None:
        assert issubclass(EchoAgent, Akgent)

    def test_handler_prints_content(self, capsys) -> None:  # noqa: ANN001
        system = ActorSystem()
        try:
            agent = system.createActor(EchoAgent)
            system.tell(agent, EchoMessage(content="Hello, Akgentic!"))
            time.sleep(0.2)
            captured = capsys.readouterr()
            assert "Hello, Akgentic!" in captured.out
        finally:
            system.shutdown(timeout=5)
