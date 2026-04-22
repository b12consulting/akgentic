"""
Stateful Agents - Demonstrating State Management with Observer Pattern
=======================================================================

This example demonstrates how agents maintain and share state using BaseState
with the observer pattern and Orchestrator tracking, and how to extend BaseConfig
to pass typed, injectable parameters that drive agent behaviour:

- Custom config type (CounterConfig) extending BaseConfig with domain fields
- Custom state type (CounterState) with multiple fields
- State initialization driven by self.config inside on_start()
- Explicit state mutation with notification, clamped via self.config
- Orchestrator tracking of all state changes
- Querying final state via Orchestrator.get_states()

Run with: python examples/04_stateful_agents.py
Or with:  uv run python examples/04_stateful_agents.py
"""

from __future__ import annotations

import time

from akgentic.core import (
    ActorAddress,
    ActorSystem,
    Akgent,
    BaseConfig,
    BaseState,
    Orchestrator,
)
from akgentic.core.messages import Message

# =============================================================================
# STEP 0: Define a custom configuration (extends BaseConfig)
# =============================================================================
# CounterConfig adds two fields that drive the agent's behaviour:
# - max_increment: clamps how much a single IncrementMessage can add
# - label_prefix:  tags every history entry for easy identification
# Both fields are available as self.config.* inside on_start() and all handlers.


class CounterConfig(BaseConfig):
    """Configuration for CounterAgent with rate-limiting and audit labelling.

    Attributes:
        max_increment: Maximum amount a single IncrementMessage may apply.
                       Requests above this value are clamped silently.
        label_prefix:  String prepended to every history entry.
                       Useful for distinguishing agents in multi-agent scenarios.
    """

    max_increment: int = 10
    label_prefix: str = ""


# =============================================================================
# STEP 1: Define the agent state
# =============================================================================
# CounterState extends BaseState to provide observer pattern integration.
# It tracks a count, history of operations, and the last operation.
# The observer pattern allows the agent to notify the Orchestrator of changes.


class CounterState(BaseState):
    """State for a counter agent tracking count and operation history.

    Attributes:
        count: Current counter value (starts at 0).
        history: List of operation labels for audit trail.
        last_operation: Description of the most recent operation.
    """

    count: int = 0
    history: list[str] = []
    last_operation: str = ""


# =============================================================================
# STEP 2: Define message types for state mutations
# =============================================================================
# Messages tell the counter agent to perform operations that mutate state.


class IncrementMessage(Message):
    """Message to increment the counter by a specified amount.

    Attributes:
        amount: Amount to increment by (default 1).
        label: Label describing this increment operation.
    """

    amount: int = 1
    label: str = ""


class ResetMessage(Message):
    """Message to reset the counter to zero.

    Attributes:
        reason: Reason for resetting the counter.
    """

    reason: str = ""


# =============================================================================
# STEP 3: Define the counter agent with state management
# =============================================================================
# CounterAgent demonstrates:
# - Initializing state with observer attachment, driven by self.config
# - Mutating state and notifying observers
# - Clamping increments using self.config.max_increment
# - Prefixing history labels using self.config.label_prefix
# - Orchestrator tracking state changes


class CounterAgent(Akgent[CounterConfig, CounterState]):
    """An agent that maintains a counter with state tracking.

    This agent demonstrates:
    - CounterConfig subclass with domain-specific fields
    - BaseState subclass with multiple fields
    - Observer pattern for state change notification
    - Orchestrator tracking of all state mutations
    - Message handlers that modify state using self.config
    """

    def on_start(self) -> None:
        """Initialize agent with counter state and attach observer.

        Called by pykka after __init__ completes. Sets up the agent's state and
        attaches this agent as an observer to receive state change notifications.
        self.config is guaranteed to be set by the framework before on_start() is called.
        """
        self.state = CounterState()
        # Read self.config to set an initial operation label — self.config is
        # guaranteed to be set by the framework before on_start() is called.
        if self.config.label_prefix:
            self.state.last_operation = f"[{self.config.label_prefix}] Agent ready"
        else:
            self.state.last_operation = "Agent ready"
        # state.observer(self) attaches self as observer and triggers initial notification
        self.state.observer(self)

    def receiveMsg_IncrementMessage(self, message: IncrementMessage, sender: ActorAddress) -> None:
        """Handle increment message by updating state.

        Clamps the requested amount using self.config.max_increment — self.config is
        also readable inside message handlers.

        Args:
            message: IncrementMessage containing amount and label.
            sender: Address of sender (unused in this example).
        """
        # Clamp the requested amount using the config
        effective = min(message.amount, self.config.max_increment)
        label = (
            f"[{self.config.label_prefix}] {message.label}"
            if self.config.label_prefix
            else message.label
        )

        # Update state fields
        self.state.count += effective
        self.state.history.append(label)
        self.state.last_operation = f"Incremented by {effective} (requested {message.amount})"

        # Print for visibility
        print(
            f"[CounterAgent] Increment requested={message.amount} → effective={effective} "
            f'→ count: {self.state.count} (label: "{label}")'
        )

        # Notify observer (Orchestrator) of state change
        self.state.notify_state_change()

    def receiveMsg_ResetMessage(self, message: ResetMessage, sender: ActorAddress) -> None:
        """Handle reset message by clearing counter.

        Resets counter to zero, records reason in history (prefixed), and notifies observer.

        Args:
            message: ResetMessage containing reason.
            sender: Address of sender (unused in this example).
        """
        label = (
            f"[{self.config.label_prefix}] {message.reason}"
            if self.config.label_prefix
            else message.reason
        )

        # Update state fields
        self.state.count = 0
        self.state.history.append(label)
        self.state.last_operation = f"Reset ({message.reason})"

        # Print for visibility
        print(f'[CounterAgent] Reset → count: {self.state.count} (reason: "{message.reason}")')

        # Notify observer (Orchestrator) of state change
        self.state.notify_state_change()


# =============================================================================
# STEP 4: Main execution - create system, agents, and demonstrate state tracking
# =============================================================================


def main() -> None:
    """Run the Stateful Agents example demonstrating state management."""
    print("[Stateful Agents] Demonstrating state management with Orchestrator tracking...")

    # Create the actor system - this is the runtime that manages all agents
    actor_system = ActorSystem()

    try:
        # Create an Orchestrator agent to track state changes
        orchestrator_addr = actor_system.createActor(
            Orchestrator,
            config=BaseConfig(name="orchestrator", role="Orchestrator"),
        )

        # Create orchestrator proxy — reused for both createActor and get_states
        orch_proxy = actor_system.proxy_ask(orchestrator_addr, Orchestrator)

        # Create CounterAgent via orchestrator with a CounterConfig instance —
        # team_id, orchestrator, parent all auto-propagated.
        # CounterConfig extends BaseConfig with max_increment and label_prefix.
        counter_addr = orch_proxy.createActor(
            CounterAgent,
            config=CounterConfig(
                name="counter",
                role="Counter",
                max_increment=5,
                label_prefix="DEMO",
            ),
        )

        # Send increment messages to mutate state
        # amount=5 → effective=5 (within max_increment=5)
        actor_system.tell(
            counter_addr,
            IncrementMessage(amount=5, label="first increment"),
        )

        # Wait for async message processing
        time.sleep(0.2)

        # amount=3 → effective=3 (within max_increment=5)
        actor_system.tell(
            counter_addr,
            IncrementMessage(amount=3, label="second increment"),
        )

        # Wait for async message processing
        time.sleep(0.2)

        # amount=10 → effective=5 (clamped by max_increment=5) — demonstrates clamping
        actor_system.tell(
            counter_addr,
            IncrementMessage(amount=10, label="over-limit increment"),
        )

        # Wait for async message processing
        time.sleep(0.2)

        actor_system.tell(
            counter_addr,
            ResetMessage(reason="starting new sequence"),
        )

        # Wait for async message processing
        time.sleep(0.2)

        # amount=10 → effective=5 (clamped again after reset)
        actor_system.tell(
            counter_addr,
            IncrementMessage(amount=10, label="after reset"),
        )

        # Wait for async message processing
        time.sleep(0.5)

        # Get all tracked state (reuse orch_proxy created above)
        states = orch_proxy.get_states()

        # Count state changes by the size of history in final state
        # (each mutation adds an entry to history)
        state_change_count = 0
        if states:
            final_state = next(iter(states.values()))
            # Type guard to handle both BaseState and dict
            if isinstance(final_state, CounterState):
                state_change_count = len(final_state.history)
                print(f"[Orchestrator] Tracked {state_change_count} state changes for CounterAgent")

                # Display final state
                print(
                    f"[Orchestrator] Final state: count={final_state.count}, "
                    f"history={final_state.history}"
                )

        print("[Stateful Agents] State management demo complete.")

    finally:
        # Always clean up the actor system
        actor_system.shutdown(timeout=5)


if __name__ == "__main__":
    main()
