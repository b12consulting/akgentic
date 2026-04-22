"""
Request-Response - Proxy Tell vs Proxy Ask Patterns
====================================================

This example demonstrates two ways to call agent methods from outside the actor system:

- **proxy_tell (fire-and-forget):** Calls an agent method without waiting for a return
  value. The method sends a message to another agent, which replies asynchronously.
- **proxy_ask (blocking):** Calls an agent method and blocks until the method returns.
  The caller receives the return value directly — no inter-agent message is sent.

Key types used:
- CalculationRequest / CalculationResult: request-response message pair (tell path)
- proxy_tell / proxy_ask: two proxy modes on ActorSystem

Run with:  python examples/02_request_response.py
Or with:  uv run python examples/02_request_response.py
"""

from __future__ import annotations

import time
import uuid

from akgentic.core import ActorAddress, ActorSystem, Akgent, BaseConfig, BaseState
from akgentic.core.messages import Message

# =============================================================================
# STEP 1: Define request and response message types
# =============================================================================
# Messages are the way agents communicate. Every message extends Message,
# which provides automatic serialization, unique IDs, and sender tracking.


class CalculationRequest(Message):
    """A calculation request message sent to the calculator agent.

    Attributes:
        a: First operand (integer).
        b: Second operand (integer).
        operation: The operation to perform ('+', '-', '*', '/').
    """

    a: int
    b: int
    operation: str


class CalculationResult(Message):
    """A calculation result message sent back from the calculator agent.

    Attributes:
        result: The computed result of the calculation.
        request_id: The ID of the original request message.
    """

    result: float
    request_id: uuid.UUID


# =============================================================================
# STEP 2: Define the calculator agent that processes requests
# =============================================================================
# This agent handles incoming CalculationRequest messages and sends back
# CalculationResult messages with the computed values.


class CalculatorAgent(Akgent[BaseConfig, BaseState]):
    """An agent that processes calculation requests and sends results.

    This agent demonstrates:
    - Receiving request messages (CalculationRequest)
    - Processing the request
    - Sending response messages back to the requester
    - The receiveMsg_<Type> pattern for automatic message dispatch
    """

    def receiveMsg_CalculationRequest(
        self, message: CalculationRequest, sender: ActorAddress
    ) -> None:
        """Handle incoming calculation request by computing and sending result.

        Args:
            message: The CalculationRequest containing operands and operation.
            sender: The ActorAddress of the sender (ClientAgent in this case).
        """
        # Extract values from request
        a = message.a
        b = message.b
        operation = message.operation

        # Compute result based on operation
        print(f"[CalculatorAgent] Processing request: {a} {operation} {b}")
        result: float
        if operation == "+":
            result = float(a + b)
        elif operation == "-":
            result = float(a - b)
        elif operation == "*":
            result = float(a * b)
        elif operation == "/":
            result = a / b if b != 0 else float("nan")
        else:
            result = float("nan")

        # Send result back to the sender
        self.send(
            sender,
            CalculationResult(result=result, request_id=message.id),
        )


# =============================================================================
# STEP 3: Define the client agent that sends requests
# =============================================================================
# This agent demonstrates both fire-and-forget (tell) and blocking (ask) patterns.


class ClientAgent(Akgent[BaseConfig, BaseState]):
    """An agent that sends calculation requests and receives results.

    This agent demonstrates:
    - Using tell() for fire-and-forget message sending
    - Using ask() for blocking/synchronous communication
    - Receiving response messages (CalculationResult)
    """

    def send_request_tell(
        self, calculator_addr: ActorAddress, a: int, b: int, operation: str
    ) -> None:
        """Send a calculation request using tell (fire-and-forget).

        tell() sends the message but does NOT wait for a response.
        This is useful for asynchronous, decoupled communication where you
        don't need immediate feedback from the recipient.

        Args:
            calculator_addr: Address of the calculator agent.
            a: First operand.
            b: Second operand.
            operation: Operation to perform.
        """
        print(f"[ClientAgent] Sending calculation request: {a} {operation} {b}")
        request = CalculationRequest(a=a, b=b, operation=operation)
        # send() internally uses tell - message is sent but we don't wait
        self.send(calculator_addr, request)

    def send_request_ask(
        self, _calculator_addr: ActorAddress, a: int, b: int, operation: str
    ) -> dict[str, float | uuid.UUID]:
        """Compute a calculation locally and return the result (ask pattern).

        Unlike send_request_tell, this method does NOT send a message to the
        CalculatorAgent. It computes the result directly and returns it.
        When called via proxy_ask, the caller blocks until this method returns
        and receives the return value — demonstrating the blocking proxy pattern.

        The arithmetic logic is intentionally duplicated from CalculatorAgent
        to keep each path self-contained for demonstration purposes.

        Args:
            _calculator_addr: Unused — kept for API symmetry with
                send_request_tell.
            a: First operand.
            b: Second operand.
            operation: Operation to perform.

        Returns:
            Dictionary with the computed result and a standalone request ID
            (not correlated to any message, since no message is sent).
        """
        print(f"[ClientAgent] Computing calculation locally (ask): {a} {operation} {b}")
        # No message is sent to another agent — the computation happens right
        # here inside the ClientAgent. The proxy_ask caller will block until
        # this method returns, then receive the return value directly.
        # (Arithmetic is duplicated from CalculatorAgent intentionally.)
        result: float
        if operation == "+":
            result = float(a + b)
        elif operation == "-":
            result = float(a - b)
        elif operation == "*":
            result = float(a * b)
        elif operation == "/":
            result = a / b if b != 0 else float("nan")
        else:
            result = float("nan")

        # request_id is an arbitrary UUID here (no correlated request message).
        return {"result": result, "request_id": uuid.uuid4()}

    def receiveMsg_CalculationResult(
        self, message: CalculationResult, sender: ActorAddress
    ) -> None:
        """Handle incoming calculation result.

        Args:
            message: The CalculationResult containing the computed value.
            sender: The ActorAddress of the sender (CalculatorAgent).
        """
        print(f"[ClientAgent] Received result: {message.result}")


# =============================================================================
# STEP 4: Main execution - create system, agents, and run
# =============================================================================


def main() -> None:
    """Run the Request-Response example demonstrating proxy_tell and proxy_ask."""
    print("[Request-Response] Demonstrating proxy_tell and proxy_ask patterns...")

    # Create the actor system - this is the runtime that manages all agents
    # ActorSystem provides zero-dependency local execution (no Redis, etc.)
    actor_system = ActorSystem()

    try:
        # Create the CalculatorAgent first - it needs to exist before ClientAgent
        # can send requests to it
        calculator_addr = actor_system.createActor(
            CalculatorAgent,
            config=BaseConfig(name="calculator", role="Calculator"),
        )

        # Create the ClientAgent
        client_addr = actor_system.createActor(
            ClientAgent,
            config=BaseConfig(name="client", role="Client"),
        )

        # Get a proxy to the client agent using tell pattern
        # This allows us to call methods on the client without waiting for responses
        client_tell = actor_system.proxy_tell(client_addr, ClientAgent)

        # Demonstrate tell pattern (fire-and-forget)
        # The client sends a request but doesn't wait for the response
        client_tell.send_request_tell(calculator_addr, 10, 5, "+")

        # Wait for the async message to be processed
        time.sleep(0.2)

        # Get a proxy to the client agent using ask pattern.
        # proxy_ask blocks until the called method returns and gives back its
        # return value — no inter-agent message is involved in this path.
        client_ask = actor_system.proxy_ask(client_addr, ClientAgent)

        # Demonstrate ask pattern (blocking).
        # send_request_ask computes locally inside ClientAgent and returns the
        # result. proxy_ask blocks here until the method finishes, then we get
        # the return value directly — unlike tell, no CalculatorAgent is used.
        result = client_ask.send_request_ask(calculator_addr, 20, 3, "*")
        print(f"[Request-Response] Ask result: {result}")

        print("[Request-Response] All calculations complete. Shutting down.")

    finally:
        # Always clean up the actor system
        actor_system.shutdown(timeout=5)


if __name__ == "__main__":
    main()
