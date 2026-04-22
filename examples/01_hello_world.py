"""
Hello World - Basic Two-Agent Message Exchange
==============================================

This example demonstrates the core akgentic pattern:
- Custom message types (HelloMessage extends Message)
- Agent message handlers (receiveMsg_<Type> pattern)
- ActorAddress for agent references
- Zero-dependency local actor system (no Redis, no HTTP, just Python)

Run with: python examples/01_hello_world.py
Or with:  uv run python examples/01_hello_world.py
"""

from __future__ import annotations

import time

from akgentic.core import ActorAddress, ActorSystem, Akgent, BaseConfig, BaseState
from akgentic.core.messages import Message

# =============================================================================
# STEP 1: Define your message type
# =============================================================================
# Messages are the way agents communicate. Every message extends Message,
# which provides automatic serialization, unique IDs, and sender tracking.


class HelloMessage(Message):
    """A greeting message sent between agents.

    Attributes:
        greeting: The greeting text to send.
    """

    greeting: str


# =============================================================================
# STEP 2: Define an agent that receives greetings
# =============================================================================
# Agents extend Agent[ConfigType, StateType]. The receiveMsg_<Type> pattern
# provides automatic message dispatch - when a HelloMessage arrives, the
# framework calls receiveMsg_HelloMessage.


class ReceiverAgent(Akgent[BaseConfig, BaseState]):
    """An agent that receives and prints greeting messages.

    This agent demonstrates the receiveMsg_<Type> pattern:
    - Define a method named receiveMsg_HelloMessage
    - The framework automatically calls it when HelloMessage arrives
    - The sender parameter tells you who sent the message
    """

    def receiveMsg_HelloMessage(self, message: HelloMessage, sender: ActorAddress) -> None:
        """Handle incoming HelloMessage by printing the greeting.

        Args:
            message: The HelloMessage containing the greeting text.
            sender: The ActorAddress of the sender (GreeterAgent in this case).
        """
        print(f"[ReceiverAgent] Received greeting: {message.greeting}")


# =============================================================================
# STEP 3: Define an agent that sends greetings
# =============================================================================
# Agents can send messages to other agents using self.send().
# The myAddress property provides this agent's address for replies.


class GreeterAgent(Akgent[BaseConfig, BaseState]):
    """An agent that can send greeting messages to other agents.

    This agent demonstrates:
    - Using self.send() to send messages to other agents
    - Using self.myAddress to identify yourself as the sender
    - Message handlers can receive and respond to messages
    """

    def receiveMsg_SendGreetingCommand(
        self, message: SendGreetingCommand, sender: ActorAddress
    ) -> None:
        """Handle command to send a greeting to a target agent.

        Args:
            message: Command containing target address and greeting text.
            sender: The ActorAddress of who sent this command.
        """
        # Send a HelloMessage to the target
        self.send(
            message.target,
            HelloMessage(greeting=message.greeting),
        )


class SendGreetingCommand(Message):
    """Command message telling GreeterAgent to send a greeting.

    Attributes:
        target: The ActorAddress to send the greeting to.
        greeting: The greeting text to send.
    """

    target: ActorAddress
    greeting: str


# =============================================================================
# STEP 4: Main execution - create system, agents, and run
# =============================================================================


def main() -> None:
    """Run the Hello World example demonstrating two-agent message exchange."""
    print("[Hello World] Starting two-agent message exchange...")

    # Create the actor system - this is the runtime that manages all agents
    # ActorSystem provides zero-dependency local execution (no Redis, etc.)
    actor_system = ActorSystem()

    try:
        # Create the ReceiverAgent first - it needs to exist before GreeterAgent
        # can send a message to it
        receiver_addr = actor_system.createActor(
            ReceiverAgent,
            config=BaseConfig(name="receiver", role="Receiver"),
        )

        # Create the GreeterAgent
        greeter_addr = actor_system.createActor(
            GreeterAgent,
            config=BaseConfig(name="greeter", role="Greeter"),
        )

        # Send a command to the greeter to send a greeting to the receiver
        # This demonstrates how the system orchestrates agent communication
        actor_system.tell(
            greeter_addr,
            SendGreetingCommand(
                target=receiver_addr,
                greeting="Hello from GreeterAgent!",
            ),
        )

        # Wait briefly for async message processing
        time.sleep(0.5)

        print("[Hello World] Exchange complete. Shutting down.")

    finally:
        # Always clean up the actor system
        actor_system.shutdown(timeout=5)


if __name__ == "__main__":
    main()
