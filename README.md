# akgentic-core

[![CI](https://github.com/b12consulting/akgentic-core/actions/workflows/ci.yml/badge.svg)](https://github.com/b12consulting/akgentic-core/actions/workflows/ci.yml)
[![Coverage](https://img.shields.io/endpoint?url=https://gist.githubusercontent.com/gpiroux/5fae2fa4f4f3cd3fc5cc08f5d2a7da44/raw/coverage.json)](https://github.com/b12consulting/akgentic-core/actions/workflows/ci.yml)

Zero-dependency actor framework for the [Akgentic](https://github.com/b12consulting/akgentic-quick-start)
multi-agent platform. Define agents, exchange typed messages, and compose
concurrent workflows — all in-memory with no external services required.

## Table of Contents

- [Overview](#overview)
- [Installation](#installation)
- [Quick Start](#quick-start)
- [Architecture](#architecture)
- [Messages](#messages)
- [Agents — Akgent](#agents--akgent)
  - [Error Handling](#error-handling)
- [ActorSystem & ActorAddress](#actorsystem--actoraddress)
- [Communication Patterns](#communication-patterns)
- [Agent Lifecycle](#agent-lifecycle)
- [State & Configuration](#state--configuration)
- [Orchestrator & Multi-Agent Coordination](#orchestrator--multi-agent-coordination)
- [AgentCard — Capability Discovery](#agentcard--capability-discovery)
- [UserProxy — Human-in-the-Loop](#userproxy--human-in-the-loop)
- [Examples](#examples)
- [Development](#development)
- [License](#license)

## Overview

`akgentic-core` provides the foundational primitives for building actor-based
agent systems with **zero infrastructure dependencies** — no Redis, no HTTP
clients, no database drivers. Everything runs in-process.

The package delivers:

- **Actor model runtime** via `Akgent` and `ActorSystem` — isolated agents
  communicating exclusively through typed messages
- **Typed message dispatch** via `receiveMsg_<Type>` convention — no manual
  routing code
- **Actor addressing** via `ActorAddress` — serializable agent references with
  rich team metadata
- **Communication primitives** — `self.send()` for actor-to-actor messaging;
  `tell` / `ask` for external callers via `ActorSystem`; typed proxy wrappers
  for method-call syntax over the message bus
- **Typed state & config** via `BaseState` / `BaseConfig` with observer pattern
  for reactive updates
- **Orchestrator** — central coordinator for telemetry, team roster, and
  pub/sub event distribution
- **Capability catalog** via `AgentCard` — declarative agent profiles for
  dynamic discovery
- **Human-in-the-loop** via `UserProxy` — bridge between humans and the agent
  system

```
  ┌──────────────────────────────────────────────┐
  │                 ActorSystem                  │
  │                                              │
  │  ┌─────────────┐  message  ┌──────────────┐  │
  │  │   AgentA    │ ────────► │    AgentB    │  │
  │  │  (Akgent)   │           │   (Akgent)   │  │
  │  │  state      │ ◄──────── │   state      │  │
  │  └──────┬──────┘  message  └────────┬─────┘  │
  │         │ telemetry       telemetry │        │
  │         └──────────┐    ┌───────────┘        │
  │                 Orchestrator                 │
  │                (team + events)               │
  └──────────────────────────────────────────────┘
```

## Installation

### Workspace Installation (Recommended)

This package is designed for use within the Akgentic monorepo workspace:

```bash
git clone git@github.com:b12consulting/akgentic-quick-start.git
cd akgentic-quick-start
git submodule update --init --recursive

uv venv
source .venv/bin/activate
uv sync --all-packages --all-extras
```

All dependencies resolve automatically via workspace configuration.

### Standalone Installation

```bash
pip install akgentic-core
# or with uv
uv add akgentic-core
```

## Quick Start

Three building blocks are all you need:

```python
from akgentic.core import ActorSystem, Akgent, ActorAddress, BaseConfig, BaseState
from akgentic.core.messages import Message


class GreetMessage(Message):
    text: str


class GreeterAgent(Akgent[BaseConfig, BaseState]):
    def receiveMsg_GreetMessage(self, msg: GreetMessage, sender: ActorAddress) -> None:
        print(f"Hello, {msg.text}!")


system = ActorSystem()

agent = system.createActor(GreeterAgent, config=BaseConfig(name="greeter", role="Greeter"))
system.tell(agent, GreetMessage(text="Akgentic"))

system.shutdown()
```

Output:

```
Hello, Akgentic!
```

## Architecture

`akgentic-core` wraps the [Pykka](https://pykka.readthedocs.io/) actor runtime
behind a framework-aware abstraction layer. **Application code must never use
Pykka directly** — all interaction goes through `Akgent`, `ActorSystem`, and
`ActorAddress`.

```
┌──────────────────────────────────────────────────────────┐
│  Application Layer: Akgent subclasses, message handlers  │
├──────────────────────────────────────────────────────────┤
│  Framework Layer: ActorSystem, Orchestrator, AgentCard   │
│                   ActorAddress, BaseState, BaseConfig    │
├──────────────────────────────────────────────────────────┤
│  Runtime Layer: Pykka (ThreadingActor, ActorRegistry)    │
└──────────────────────────────────────────────────────────┘
```

### Package Structure

```
src/akgentic/core/
    __init__.py             # Public API — flat imports
    agent.py                # Akgent base class, ProxyWrapper
    actor_system_impl.py    # ActorSystem, ExecutionContext, Statistics
    actor_address.py        # ActorAddress ABC
    actor_address_impl.py   # ActorAddressImpl, ActorAddressProxy, ActorAddressStopped
    agent_card.py           # AgentCard — capability profiles
    agent_config.py         # BaseConfig, AgentConfig alias
    agent_state.py          # BaseState with observer pattern
    orchestrator.py         # Orchestrator, EventSubscriber, Timer
    user_proxy.py           # UserProxy — human-in-the-loop bridge
    messages/
        message.py          # Message, UserMessage, ResultMessage, StopRecursively
        orchestrator.py     # Telemetry messages (SentMessage, ErrorMessage, …)
    utils/
        serializer.py       # SerializableBaseModel (internal)
        deserializer.py     # ActorAddressDict, DeserializeContext (internal)
examples/                   # 6 progressive examples with companion docs
tests/
```

### Why Pykka Is Abstracted

`Pykka` is a general-purpose actor library with no awareness of agents, teams,
or workflows. The abstraction adds what the framework needs:

| Pykka primitive | Framework equivalent | What is added |
|---|---|---|
| `ThreadingActor` | `Akgent` | Message dispatch, state, telemetry, child creation |
| `ActorRef` | `ActorAddress` | Team metadata, serialization, typed proxy access |
| `ActorRegistry` + `start()` | `ActorSystem.createActor()` | `team_id` propagation, orchestrator wiring |

## Messages

A `Message` is the only way agents interact. Define message types by
subclassing `Message`:

```python
from akgentic.core.messages import Message

class TaskMessage(Message):
    task_id: str
    payload: str
```

Every message automatically carries:

- `id` — unique UUID
- `timestamp` — creation time
- `sender` / `recipient` — `ActorAddress` references
- `team_id` — team scope
- `parent_id` — causal chain tracking

Messages are **immutable data packets**. Import business messages from
`akgentic.core.messages`:

```python
from akgentic.core.messages import (
    Message,           # Base class for all application messages
    UserMessage,       # Human input into the agent system
    ResultMessage,     # Agent response to a UserMessage
    StopRecursively,   # Signal recursive shutdown
)
```

Telemetry messages (`SentMessage`, `ReceivedMessage`, `ErrorMessage`, etc.)
flow automatically to the Orchestrator. Import them when building
`EventSubscriber` implementations or handling errors programmatically:

```python
from akgentic.core.messages.orchestrator import (
    SentMessage, ReceivedMessage, ProcessedMessage, ErrorMessage,
    StartMessage, StopMessage, StateChangedMessage, EventMessage,
)
```

## Agents — Akgent

`Akgent[ConfigType, StateType]` is the base class every agent extends. It turns
a raw Pykka actor into a framework agent:

```python
from akgentic.core import Akgent, BaseConfig, BaseState, ActorAddress

class SummaryAgent(Akgent[BaseConfig, BaseState]):

    def on_start(self) -> None:
        """Initialisation hook — runs inside the actor thread after startup."""
        self.state = BaseState()
        self.state.observer(self)

    def receiveMsg_TaskMessage(self, msg: TaskMessage, sender: ActorAddress) -> None:
        """Handler name = receiveMsg_ + message class name."""
        result = self._summarize(msg.payload)
        self.send(sender, ResultMessage(content=result))

    def _summarize(self, text: str) -> str:
        return text[:100]
```

**Key conventions:**

- **`receiveMsg_<ClassName>`** — automatic dispatch; no manual routing needed
- **`on_start()`** — always initialise state here, never in `__init__`
- **`self.send(recipient, message)`** — send from within an actor
- **`self.myAddress`** — obtain own `ActorAddress` for self-reference

**Key methods:**

| Method | Description |
|---|---|
| `on_start()` | Initialisation hook (actor thread) |
| `send(recipient, msg)` | Send message with telemetry |
| `createActor(cls, config)` | Spawn child actor with context propagation |
| `stop()` | Recursive stop (children first, then self) |
| `update_state(updates)` | Merge dict into typed state |
| `notify_event(event)` | Emit domain event via `EventMessage` |
| `proxy_tell(addr, Type)` | Typed fire-and-forget proxy call |
| `proxy_ask(addr, Type)` | Typed blocking proxy call |
| `get_team()` | Team roster via orchestrator |
| `get_agent_card(role)` | Look up capability profile |
| `find_agents_with_skill(skill)` | Discover agents by skill |

### Error Handling

When an unhandled exception occurs during message processing, `Akgent` uses
Pykka's `_handle_failure()` hook (not a try/except wrapper around dispatch):

1. **Log** the error with full context
2. **Emit `ProcessedMessage`** to the orchestrator (marks the current message as done)
3. **Check for `WarningError`** — if so, silently acknowledge and return
4. **Emit `ErrorMessage`** with `exception_type`, `exception_value`, `traceback`,
   and `current_message` to the orchestrator

The actor **does not crash** — it continues processing subsequent messages.

`WarningError` is a soft signal for non-critical failures (e.g., usage limits
exceeded). Raise it from a message handler when the error should be logged
and the current message marked as processed, but no `ErrorMessage` should be
sent to the orchestrator. Import it from `akgentic.core`:

```python
from akgentic.core import WarningError

class MyAgent(Akgent[BaseConfig, BaseState]):
    def receiveMsg_TaskMessage(self, msg: TaskMessage, sender: ActorAddress) -> None:
        if self._over_budget():
            raise WarningError("Usage limit exceeded")  # logged, no ErrorMessage
```

For proxy `ask()` calls, Pykka's reply mechanism handles errors automatically —
the exception is sent back to the caller, bypassing `_handle_failure()`.

## ActorSystem & ActorAddress

### ActorSystem

`ActorSystem` is the **sole gateway** between external code and the actor world.
From outside an actor (a web handler, a test, a CLI), all interaction goes
through `ActorSystem`:

```python
system = ActorSystem()

# Spawn an agent — returns an ActorAddress, never a direct object reference
agent = system.createActor(MyAgent, config=BaseConfig(name="agent", role="MyAgent"))

# Fire-and-forget
system.tell(agent, MyMessage(data="hello"))

# Blocking request — wait for handler's return value
result = system.ask(agent, QueryMessage(query="..."), timeout=10.0)

# Receive a reply sent back to the system context
response = system.listen(timeout=5.0)

# Typed proxy — method call syntax, still message-passing under the hood
proxy = system.proxy_ask(agent, MyAgent, timeout=5.0)
result = proxy.some_method(arg)

system.shutdown()
```

Use `system.private()` when you need an isolated context for scripted
workflows or integration tests where the caller receives replies directly:

```python
with system.private() as ctx:
    ctx.tell(agent, MyMessage())
    reply = ctx.listen(timeout=5.0)
```

### ActorAddress

`ActorAddress` is a reference to an agent — like a mailbox address. You never
hold a direct Python object reference to another agent.

```python
addr.agent_id   # UUID — unique agent identity
addr.name       # str  — e.g. "@Summarizer"
addr.role       # str  — e.g. "SummaryAgent"
addr.team_id    # UUID — always set; defines team membership
addr.is_alive() # bool — whether the actor is still running
addr.serialize()# → ActorAddressDict — survives serialization/persistence
```

Three implementations cover the full actor lifecycle:

| Class | Used when | `send()` |
|---|---|---|
| `ActorAddressImpl` | Live actor | delivers to mailbox |
| `ActorAddressProxy` | Deserialized / mock | raises `RuntimeError` |
| `ActorAddressStopped` | Post-stop tracking | raises `RuntimeError` |

## Communication Patterns

### tell vs ask

| | `tell` / `proxy_tell` | `ask` / `proxy_ask` |
|---|---|---|
| **Blocks caller** | No — fire-and-forget | Yes — until handler returns |
| **Return value** | None | Handler's return value |
| **Deadlock risk** | None | Yes if called from within the same actor |
| **Use for** | Notifications, events | Queries, request-response |

### Bidirectional Messaging (reply via `sender`)

Every `receiveMsg_<Type>` handler receives `sender: ActorAddress`. Reply by
sending a message back:

```python
class ResponderAgent(Akgent[BaseConfig, BaseState]):
    def receiveMsg_QueryMessage(self, msg: QueryMessage, sender: ActorAddress) -> None:
        result = self._compute(msg.query)
        self.send(sender, ResultMessage(content=result))
```

### Typed Proxy Wrappers

`proxy_tell` and `proxy_ask` provide method-call syntax over the message bus —
the actor model principle is preserved because every call is still converted to
a mailbox message internally:

```python
# Outside the actor system
orch_proxy = system.proxy_ask(orchestrator_addr, Orchestrator)
team = orch_proxy.get_team()           # → ask() → mailbox → handler → return

# Inside an actor (actor-to-actor)
worker_proxy = self.proxy_tell(worker_addr, WorkerAgent)
worker_proxy.process(task)             # → tell() → worker's mailbox
```

## Agent Lifecycle

### Spawning Agents

Agents are created with `createActor()` — either from `ActorSystem` (root
actors) or from within an actor (child actors):

```python
# Root actor — from outside
orchestrator = system.createActor(
    Orchestrator,
    config=BaseConfig(name="orchestrator", role="Orchestrator"),
)

# Child actor — from inside an agent
class ManagerAgent(Akgent[BaseConfig, BaseState]):
    def on_start(self) -> None:
        self._worker = self.createActor(
            WorkerAgent,
            config=WorkerConfig(name="worker-1"),
        )
        # team_id and orchestrator reference are automatically propagated
```

When spawning through a parent, three things propagate automatically:

- `team_id` — child joins the same team
- `orchestrator` — child reports telemetry to the same coordinator
- `parent` — stored as `self._parent` on the child

### `on_start()` Hook

Always perform actor initialisation in `on_start()`, never in `__init__`.
`on_start()` runs inside the actor thread after startup, making it safe to
create child actors and attach state observers:

```python
class MyAgent(Akgent[MyConfig, MyState]):
    def on_start(self) -> None:
        self.state = MyState()
        self.state.observer(self)          # reactive state updates
        self._child = self.createActor(HelperAgent)
```

### Stopping

`stop()` cascades recursively — children are stopped before the parent.
To shut down a team, stop the Orchestrator:

```
orchestrator.stop()
  → stops team members (recursively)
  → stops orchestrator itself
  → sends StopMessage to telemetry log
```

## State & Configuration

### BaseConfig

`BaseConfig` is the typed configuration model for an agent. Subclass it to add
agent-specific fields:

```python
from akgentic.core import BaseConfig

class WorkerConfig(BaseConfig):
    max_retries: int = 3
    timeout: float = 30.0
```

Configuration is injected at creation and accessible as `self.config`
throughout the agent's lifetime. When agents are instantiated from an
`AgentCard`, `get_config_copy()` returns a deep copy — preventing shared
mutable state across instances.

### BaseState

`BaseState` is a Pydantic model with an observer pattern. State changes
automatically notify the Orchestrator via `StateChangedMessage`:

```python
from akgentic.core import BaseState

class WorkerState(BaseState):
    tasks_completed: int = 0
    current_task: str | None = None

class WorkerAgent(Akgent[WorkerConfig, WorkerState]):
    def on_start(self) -> None:
        self.state = WorkerState()
        self.state.observer(self)          # attach — triggers initial notification

    def receiveMsg_TaskMessage(self, msg: TaskMessage, sender: ActorAddress) -> None:
        self.update_state({
            "current_task": msg.task_id,
            "tasks_completed": self.state.tasks_completed + 1,
        })
        # Orchestrator is notified automatically
```

`update_state()` performs a full Pydantic round-trip: merges the dict into
`model_dump()`, deserializes via `AkgentDeserializeContext`, then calls
`init_state()` which preserves the observer and notifies.

## Orchestrator & Multi-Agent Coordination

### The Orchestrator

The `Orchestrator` is always the **root actor** of a team. It serves as the
central coordinator for:

- **Telemetry** — records every lifecycle event and message exchange (including `EventMessage`)
- **Team roster** — tracks which agents are alive via `StartMessage`/`StopMessage`
- **State snapshots** — stores the latest `BaseState` for each agent
- **Pub/sub** — distributes events to `EventSubscriber` implementations

```python
from akgentic.core import Orchestrator, BaseConfig

orchestrator_addr = system.createActor(
    Orchestrator,
    config=BaseConfig(name="orchestrator", role="Orchestrator"),
)
# team_id is generated here — this becomes the team's identity

# Spawn all other agents through the orchestrator so they inherit team_id
agent_addr = orchestrator_addr.createActor(MyAgent, ...)
```

**Team management (via proxy):**

```python
orch = system.proxy_ask(orchestrator_addr, Orchestrator)

orch.get_team()                    # Active agent addresses (excludes Orchestrator)
orch.get_team_member("@Writer")    # Find by name
orch.get_messages()                # Full telemetry log
orch.get_states()                  # Latest state per agent
orch.get_events()                  # All EventMessages (optional agent_id/event_class filters)
```

### `team_id` Inheritance

All non-orchestrator agents must be spawned **through** the Orchestrator (or
through an agent already in the team). Direct creation from `ActorSystem` gives
an isolated `team_id` — the agent will not appear in `get_team()` and its
telemetry will not flow to the Orchestrator.

```
ActorSystem.createActor(Orchestrator)   → team_id = <UUID-A>
  └─ Orchestrator.createActor(AgentA)   → team_id = <UUID-A>  (propagated)
       └─ AgentA.createActor(AgentB)    → team_id = <UUID-A>  (propagated again)
```

### Event Subscribers

Subscribe to the telemetry stream for persistence, streaming, or external
integrations:

```python
from akgentic.core import EventSubscriber
from akgentic.core.messages import Message

class MySubscriber(EventSubscriber):
    def on_message(self, msg: Message) -> None:
        print(f"[telemetry] {type(msg).__name__}")

    def on_stop(self) -> None:
        pass

orch.subscribe(MySubscriber())
```

`on_message()` receives all telemetry types: `StartMessage`, `StopMessage`,
`SentMessage`, `ReceivedMessage`, `ProcessedMessage`, `ErrorMessage`,
`StateChangedMessage`, `EventMessage`.

### Team Restoration

The Orchestrator's telemetry log is the single source of truth for crash
recovery. Because every lifecycle and business event flows through it, a team
can be fully reconstructed by:

1. Identifying agents alive at shutdown (`StartMessage` minus `StopMessage`)
2. Recreating those actors with original `agent_id`, `team_id`, and `config`
3. Replaying persisted events via `restore_message()` to rebuild in-memory state

`akgentic-team` implements the full 3-phase restore protocol on top of these
primitives. See [akgentic-team](../akgentic-team/README.md) for details.

## AgentCard — Capability Discovery

`AgentCard` is a declarative profile that describes an agent type. Register
profiles with the Orchestrator so running agents can discover capabilities
without hardcoding dependencies:

```python
from akgentic.core import AgentCard, BaseConfig

card = AgentCard(
    role="ResearchAgent",
    description="Performs web research and data gathering",
    skills=["web_search", "pdf_extraction"],
    agent_class=ResearchAgent,             # class or fully-qualified string
    config=BaseConfig(name="researcher", role="ResearchAgent"),
    routes_to=["WriterAgent"],             # empty = no routing restrictions
)

# Register with the Orchestrator
orch.register_agent_profile(card)

# Query the catalog
orch.get_agent_catalog()                   # all profiles
orch.get_agent_profile("ResearchAgent")    # by role
orch.get_profiles_by_skill("web_search")   # by skill
orch.get_available_roles()                 # role list
```

**From within an agent**, use the built-in discovery methods:

```python
class CoordinatorAgent(Akgent[BaseConfig, BaseState]):
    def receiveMsg_PlanMessage(self, msg, sender):
        writers = self.find_agents_with_skill("writing")
        card = self.get_agent_card("ResearchAgent")
        config = card.get_config_copy()    # deep copy — safe to mutate
```

**Profile vs. instance:**

```
AgentCard catalog  → "What agent types exist?" (static capability directory)
get_team()         → "What instances are running?" (dynamic runtime roster)
```

**`routes_to` routing constraints:**
- Empty list → no restrictions; the agent can send to any role
- Non-empty list → restricted; only listed roles are valid targets
- Responses are always allowed regardless of `routes_to`

## UserProxy — Human-in-the-Loop

`UserProxy` is a regular team actor that acts as the boundary between the agent
system and a human user. The interaction follows a two-leg flow:

```
Agent ──UserMessage──►  UserProxy  ──(telemetry)──►  Orchestrator
                                                           │
                                              EventSubscriber (e.g. WebSocket)
                                                           │
                                                      external UI
                                                           │
                                        ActorSystem.proxy_ask(user_proxy_addr, UserProxy)
                                                           │
                                    Agent ◄── process_human_input(content, msg)
```

**Leg 1 — forwarding to the human:**
When an agent needs human input it sends a `UserMessage` to the `UserProxy`
actor. `receiveMsg_UserMessage` fires in the proxy's thread. The default
implementation only logs — the message flows through the
Orchestrator as normal telemetry, so any registered `EventSubscriber` can
intercept it and forward it to the external system.

**Leg 2 — injecting the human's response:**
When the human replies, the external system calls `process_human_input()` on the
`UserProxy` via an `ActorSystem` proxy call. The default implementation wraps the
response in a `ResultMessage` and sends it back to `msg.sender` — the agent that
originally asked.

```python
from akgentic.core import UserProxy, UserMessage, ActorAddress

# Subclass to integrate with your UI
class MyUserProxy(UserProxy):
    def receiveMsg_UserMessage(self, msg: UserMessage, sender: ActorAddress) -> None:
        # log the message in the Orchestrator telemetry (received/processed messages)
        pass

# Spawn via the Orchestrator like any other team member
proxy_addr = orchestrator_addr.createActor(
    MyUserProxy,
    config=BaseConfig(name="@Human", role="UserProxy"),
)

# When the human replies, the external system injects the answer.
# Pass the original UserMessage so the proxy knows who to reply to.
proxy = system.proxy_ask(proxy_addr, MyUserProxy)
proxy.process_human_input("Approved", original_user_message)  # original_user_message: the UserMessage received in Leg 1
```

**`akgentic-agent` provides `HumanProxy`**, a richer subclass that handles
multi-hop routing via continuation chains — useful when the request travels
through several agents before reaching the human (e.g. Manager → Dev → Human →
Dev → Manager). See
[`akgentic-agent`](../akgentic-agent/README.md) for details.

## Examples

Six progressive, self-contained examples in the [examples/](examples/)
directory. Each includes a runnable `.py` script and a companion `.md`
explaining concepts and pitfalls.

```bash
uv run python examples/01_hello_world.py
```

| # | Script | Topic |
|---|---|---|
| 01 | `01_hello_world.py` | `Message`, `Akgent`, `ActorSystem` — first agent |
| 02 | `02_request_response.py` | Bidirectional messaging, `tell` vs `ask`, proxy wrappers |
| 03 | `03_dynamic_agents.py` | `createActor()`, parent-child hierarchy, `on_start()` |
| 04 | `04_stateful_agents.py` | `BaseConfig`, `BaseState`, observer pattern, Orchestrator |
| 05 | `05_multi_agent.py` | Multi-agent workflows, `UserProxy`, `EventSubscriber` |
| 06 | `06_agent_cards.py` | `AgentCard`, capability catalog, routing constraints |

See [`examples/README.md`](examples/README.md) for the full concept index.

## Development

### Prerequisites

- Python 3.12+
- [uv](https://docs.astral.sh/uv/) package manager

### Setup

```bash
uv sync --all-extras
```

### Commands

All commands run from the **monorepo root** (`akgentic-quick-start/`):

```bash
# Run tests
pytest packages/akgentic-core/tests/

# Run tests with coverage
pytest packages/akgentic-core/tests/ --cov=akgentic.core --cov-fail-under=80

# Lint
ruff check packages/akgentic-core/src/

# Format
ruff format packages/akgentic-core/src/

# Type check
mypy packages/akgentic-core/src/
```

## License

See the repository root for license information.
