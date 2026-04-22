# akgentic-core Examples

`akgentic-core` is a pure-Python actor framework for building concurrent, message-driven agents.
No Redis, no HTTP, no external services — just Python.

---

## The Actor Model

The actor model is a concurrency pattern where computation is organized around **actors** (agents):
each actor encapsulates its own state and behavior, and interacts with the world exclusively by
sending and receiving **messages**. Actors never share memory directly; they only communicate
through their mailboxes.

```
  ┌────────────────────────────────────────────────┐
  │                  ActorSystem                   │
  │                                                │
  │   ┌──────────┐   message   ┌──────────────┐    │
  │   │  AgentA  │ ──────────► │   AgentB     │    │
  │   │          │             │              │    │
  │   │  state   │ ◄────────── │   state      │    │
  │   └──────────┘   message   └──────────────┘    │
  │                                                │
  └────────────────────────────────────────────────┘
```

Three building blocks are all you need to get started:

| Block       | Class                   | Role                                                  |
| ----------- | ----------------------- | ----------------------------------------------------- |
| **Message** | `Message`               | Typed data packet exchanged between agents            |
| **Agent**   | `Akgent[Config, State]` | Isolated unit of state + behavior                     |
| **Runtime** | `ActorSystem`           | Manages agents, delivers messages, controls lifecycle |

---

## Setup

```bash
# Install with uv (recommended)
uv add akgentic-core

# Or with pip
pip install akgentic-core
```

Run any example from the package root:

```bash
uv run python examples/01_hello_world.py
```

---

## Learning Path

Work through the examples in order — each one introduces a small set of new concepts and builds
on the previous.

| #   | File                     | What you'll learn                                                        |
| --- | ------------------------ | ------------------------------------------------------------------------ |
| 01  | `01_hello_world.py`      | Messages, agents, the actor system, fire-and-forget messaging            |
| 02  | `02_request_response.py` | Bidirectional communication, blocking calls, proxy wrappers              |
| 03  | `03_dynamic_agents.py`   | Spawning child agents at runtime, parent-child hierarchy                 |
| 04  | `04_stateful_agents.py`  | Custom config, typed state, the observer pattern, Orchestrator telemetry |
| 05  | `05_multi_agent.py`      | Multi-agent workflows, human-in-the-loop, event subscribers              |

---

## Concept Index

Find the example where a specific concept first appears:

| Concept                                            | First seen in                                   |
| -------------------------------------------------- | ----------------------------------------------- |
| `Message`                                          | [01 — Hello World](01-hello-world.md)           |
| `Akgent[Config, State]`                            | [01 — Hello World](01-hello-world.md)           |
| `ActorSystem`                                      | [01 — Hello World](01-hello-world.md)           |
| `receiveMsg_<Type>` dispatch                       | [01 — Hello World](01-hello-world.md)           |
| `self.send()`                                      | [01 — Hello World](01-hello-world.md)           |
| `actor_system.tell()`                              | [01 — Hello World](01-hello-world.md)           |
| `ActorAddress`                                     | [01 — Hello World](01-hello-world.md)           |
| Bidirectional messaging (reply via `sender`)       | [02 — Request-Response](02-request-response.md) |
| `proxy_tell()` / `proxy_ask()`                     | [02 — Request-Response](02-request-response.md) |
| `tell` vs `ask` (fire-and-forget vs blocking)      | [02 — Request-Response](02-request-response.md) |
| `self.createActor()` (spawn child from agent)      | [03 — Dynamic Agents](03-dynamic-agents.md)     |
| `self._parent`                                     | [03 — Dynamic Agents](03-dynamic-agents.md)     |
| `on_start()` hook                                  | [03 — Dynamic Agents](03-dynamic-agents.md)     |
| `BaseConfig` subclass (custom typed config)        | [04 — Stateful Agents](04-stateful-agents.md)   |
| `self.config` in `on_start()` and message handlers | [04 — Stateful Agents](04-stateful-agents.md)   |
| `BaseState` subclass                               | [04 — Stateful Agents](04-stateful-agents.md)   |
| `state.observer()` / `notify_state_change()`       | [04 — Stateful Agents](04-stateful-agents.md)   |
| `Orchestrator`                                     | [04 — Stateful Agents](04-stateful-agents.md)   |
| `UserProxy` (human-in-the-loop)                    | [05 — Multi-Agent](05-multi-agent.md)           |
| `EventSubscriber`                                  | [05 — Multi-Agent](05-multi-agent.md)           |
| `get_team()` / `get_messages()` / `get_states()`   | [05 — Multi-Agent](05-multi-agent.md)           |
