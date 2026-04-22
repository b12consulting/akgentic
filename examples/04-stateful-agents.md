# 04 — Stateful Agents

> A counter agent maintains typed state across messages, notifies an Orchestrator on every
> change, and exposes the full audit trail — introducing custom config, state management, and telemetry.

---

## Concepts introduced

### `BaseConfig` — typed agent configuration

Every `Akgent` receives a configuration object on creation. By default it is a plain
`BaseConfig` with three built-in fields — `name`, `role`, and `squad_id`. Subclass it to add
domain-specific parameters that Pydantic validates for you:

```python
from akgentic.core import BaseConfig

class CounterConfig(BaseConfig):
    max_increment: int = 10   # clamp per-message increments
    label_prefix: str = ""    # prefix every audit-trail entry
```

Pass an instance as `config=` when creating the agent through the orchestrator proxy:

```python
counter_addr = orch_proxy.createActor(
    CounterAgent,
    config=CounterConfig(
        name="counter",
        role="Counter",
        max_increment=5,
        label_prefix="DEMO",
    ),
)
```

The framework assigns `self.config` **before** calling `on_start()`, so custom fields are
available immediately at initialisation time:

```python
def on_start(self) -> None:
    self.state = CounterState()
    if self.config.label_prefix:
        self.state.last_operation = f"[{self.config.label_prefix}] Agent ready"
    else:
        self.state.last_operation = "Agent ready"
    self.state.observer(self)
```

`self.config` is equally accessible inside any message handler — read it whenever agent-level
parameters need to influence message processing:

```python
# In receiveMsg_IncrementMessage:
effective = min(message.amount, self.config.max_increment)
```

---

### `BaseState` — typed agent state

Every `Akgent` carries a `state` field. By default it is a plain `BaseState`, but you can
subclass it to add your own fields:

```python
from akgentic.core import BaseState

class CounterState(BaseState):
    count: int = 0
    history: list[str] = []
    last_operation: str = ""
```

The type parameters on `Akgent[Config, StateType]` tell the framework which config and state
types this agent uses:

```python
class CounterAgent(Akgent[CounterConfig, CounterState]):
    ...
```

State lives **inside** the agent and is never shared directly with other agents.

---

### Observer pattern — `state.observer()` and `notify_state_change()`

State changes are invisible to the outside world by default. The observer pattern lets you
broadcast them. In `on_start()`, attach the agent itself as an observer of its own state:

```python
def on_start(self) -> None:
    self.state = CounterState()
    self.state.observer(self)   # subscribe to state change events
```

After mutating state in a handler, call `notify_state_change()` to fire the event:

```python
def receiveMsg_IncrementMessage(self, message: IncrementMessage, sender: ActorAddress) -> None:
    effective = min(message.amount, self.config.max_increment)
    self.state.count += effective
    self.state.notify_state_change()   # broadcast the change
```

The observer pattern decouples the agent from anything that wants to track its state — the agent
simply announces "I changed"; it doesn't need to know who is listening.

---

### Orchestrator — telemetry hub

The `Orchestrator` is a built-in agent that monitors message exchanges (see example [05 — Multi-Agent](05-multi-agent.md)) and state changes from its child agents (the agent team). It acts as a centralized telemetry hub, enabling observability without coupling agents to each other:

```python
from akgentic.core import Orchestrator

orchestrator_addr = actor_system.createActor(
    Orchestrator,
    config=BaseConfig(name="orchestrator", role="Orchestrator"),
)
```

When creating an orchestrator from the `actor_system`, a `team_id` is automatically assigned to it. All child agents of the orchestrator automatically belong to the same team (the `team_id` and orchestrator address propagate to them automatically).

```python
orch_proxy = actor_system.proxy_ask(orchestrator_addr, Orchestrator)
counter_addr = orch_proxy.createActor(
    CounterAgent,
    config=CounterConfig(name="counter", role="Counter", max_increment=5, label_prefix="DEMO"),
)
```

In the example, the `orch_proxy` is reused for both `createActor()` and the later telemetry query:

```python
states = orch_proxy.get_states()   # { agent_id: StateSnapshot }
```

Note that the team membership (`orch_proxy.get_team()`) is tracked by the Orchestrator through `StartMessage` events. Every agent created this way announces itself on startup, and the Orchestrator records it.

---

## Message flow

```
main()            ActorSystem        Orchestrator         CounterAgent
  |                   |                   |                     |
  |--createActor(O)-->|                   |                     |
  |                   |--spawn----------->|                     |
  |                   |                   |                     |
  |--orch_proxy.createActor(Counter,      |                     |
  |    CounterConfig(max_increment=5,     |                     |
  |     label_prefix="DEMO"))------------>|--spawn------------->|
  |                   |                   |  (team_id, orch,    |
  |                   |                   |   parent: auto)     |
  |                   |                   |                     |
  |--tell(Increment +5)-------------------|-------------------->|
  |                   |                   |  receiveMsg_IncrementMessage
  |                   |                   |<--notify_state_change()
  |                   |                   |  (StateChangedMsg)  |
  |                   |                   |                     |
  |--tell(Increment +3)-------------------|-------------------->|
  |                   |                   |  receiveMsg_IncrementMessage
  |                   |                   |<--notify_state_change()
  |                   |                   |                     |
  |--tell(Increment +10 →clamped to +5)---|-------------------->|
  |                   |                   |  receiveMsg_IncrementMessage
  |                   |                   |<--notify_state_change()
  |                   |                   |                     |
  |--tell(Reset)--------------------------|-------------------->|
  |                   |                   |  receiveMsg_IncrementMessage
  |                   |                   |<--notify_state_change()
  |                   |                   |                     |
  |--tell(Increment +10 →clamped to +5)---|-------------------->|
  |                   |                   |  receiveMsg_IncrementMessage
  |                   |                   |<--notify_state_change()
  |                   |                   |                     |
  |--orch_proxy.get_states()------------->|                     |
  |<--{ counter_id: CounterState }--------|                     |
  |                   |                   |                     |
  |--shutdown()------>|                   |                     |
```

---

## Walkthrough

The counter agent maintains a `CounterState` with three fields: a running `count`, a `history`
list (one entry per operation), and `last_operation`. After every mutation it fires
`notify_state_change()`, which the Orchestrator intercepts and records.

The agent is configured with `CounterConfig(max_increment=5, label_prefix="DEMO")`:

- **`max_increment=5`**: any `IncrementMessage` with `amount > 5` is silently clamped to 5,
  demonstrating that configuration can enforce invariants without changing the message protocol
- \*\*`label_prefix="DEMO"`: every history entry is prefixed with `[DEMO]`, tagging the audit
  trail — the same `CounterAgent` class could be deployed with different prefixes for different
  environments

`self.config` is read in `on_start()` to set the initial `last_operation`, and again in every
message handler. This shows that **configuration is a first-class, uniform source of behaviour**
across the agent's entire lifecycle.

At the end, you can retrieve a snapshot of the final state:

```python
states = orch_proxy.get_states()
if states:
    final = next(iter(states.values()))
    print(f"count={final.count}, history={final.history}")
```

The Orchestrator never needs to know about `CounterState` or `CounterConfig` specifically — it
stores whatever `BaseState` subclass the agent reports. This makes it reusable across any agent type.

---

## Running it

```bash
uv run python examples/04_stateful_agents.py
```

Expected output:

```
[Stateful Agents] Demonstrating state management with Orchestrator tracking...
[CounterAgent] Increment requested=5 → effective=5 → count: 5 (label: "[DEMO] first increment")
[CounterAgent] Increment requested=3 → effective=3 → count: 8 (label: "[DEMO] second increment")
[CounterAgent] Increment requested=10 → effective=5 → count: 13 (label: "[DEMO] over-limit increment")
[CounterAgent] Reset → count: 0 (reason: "starting new sequence")
[CounterAgent] Increment requested=10 → effective=5 → count: 5 (label: "[DEMO] after reset")
[Orchestrator] Tracked 5 state changes for CounterAgent
[Orchestrator] Final state: count=5, history=['[DEMO] first increment', '[DEMO] second increment', '[DEMO] over-limit increment', '[DEMO] starting new sequence', '[DEMO] after reset']
[Stateful Agents] State management demo complete.
```

Notice:

- `amount=10` increments are clamped to `effective=5` by `max_increment=5`
- Every history label carries the `[DEMO]` prefix from `label_prefix`
- The reset reason also appears in history with the prefix — `self.config` is accessible in all handlers, not only `on_start()`

---

## What's next

→ [05 — Multi-Agent](05-multi-agent.md): full multi-agent workflow with a human-in-the-loop and
event subscribers.
