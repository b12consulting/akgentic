# akgentic-agent

[![CI](https://github.com/b12consulting/akgentic-agent/actions/workflows/ci.yml/badge.svg)](https://github.com/b12consulting/akgentic-agent/actions/workflows/ci.yml)
[![Coverage](https://img.shields.io/endpoint?url=https://gist.githubusercontent.com/gpiroux/69ad301e9b6491972aa7324eb8953f8a/raw/coverage.json)](https://github.com/b12consulting/akgentic-agent/actions/workflows/ci.yml)

LLM-driven collaborative agents for the
[Akgentic](https://github.com/b12consulting/akgentic-quick-start) multi-agent framework.
`BaseAgent` composes the actor runtime, LLM integration, and tool infrastructure into a
single unit where agents communicate through a typed message protocol and route messages
to each other via structured LLM output.

## Table of Contents

- [Overview](#overview)
- [Installation](#installation)
- [Quick Start](#quick-start)
- [Communication Model](#communication-model)
- [Message Protocol](#message-protocol)
- [Team Composition](#team-composition)
- [Configuration](#configuration)
- [Tool Channels](#tool-channels)
- [Examples](#examples)
- [Development](#development)
- [License](#license)

## Overview

Each agent is an Akgent actor. When it receives an `AgentMessage`, it runs a REACT loop
(`ReactAgent.run_sync`) and returns a `StructuredOutput` — a list of `Request` objects
that each name a recipient and a message type. The framework resolves the recipients and
delivers the messages; the LLM navigates the conversation graph.

```
Human
  │  AgentMessage(content, type="request")
  ▼
HumanProxy ──send()──► BaseAgent (Manager)
                             │
                    receiveMsg_AgentMessage()
                             │  prepend reply protocol to the prompt:
                             │  "You received a request from @Human. Carry out the
                             │   task, then respond to @Human. ..."
                             │
                    process_message(prompt, sender)
                             │
                    act(prompt, StructuredOutput)
                      │
                      ├─ expand !!glob_pattern refs (if WorkspaceTool present)
                      └─ ReactAgent.run_sync(prompt, output_type=StructuredOutput)
                             │
                    StructuredOutput.messages = [
                        Request(recipient="@Assistant", message_type="instruction", message="..."),
                        Request(recipient="Developer",  message_type="request",     message="..."),
                    ]
                             │
                    for each Request:
                      ├─ "@Name" → resolve to existing actor
                      └─ "Role"  → hire_member(role) → create actor
                             │
                    send AgentMessage with enriched content:
                      "You received a request from @Manager:\n\n<message>"
```

## Installation

```bash
git clone git@github.com:b12consulting/akgentic-quick-start.git
cd akgentic-quick-start
git submodule update --init --recursive

uv venv && source .venv/bin/activate
uv sync --all-packages --all-extras
```

## Quick Start

```python
import time
from akgentic.agent import AgentConfig, AgentMessage, BaseAgent, HumanProxy
from akgentic.core import ActorSystem, AgentCard, BaseConfig, Orchestrator
from akgentic.llm import ModelConfig, PromptTemplate

# Actor runtime + Orchestrator
actor_system = ActorSystem()
orchestrator_addr = actor_system.createActor(
    Orchestrator, config=BaseConfig(name="@Orchestrator", role="Orchestrator")
)
orchestrator_proxy = actor_system.proxy_ask(orchestrator_addr, Orchestrator)

# Define and register a role blueprint
manager_card = AgentCard(
    role="Manager",
    description="Project manager who coordinates specialists",
    skills=["coordination", "delegation"],
    agent_class="akgentic.agent.BaseAgent",
    config=AgentConfig(
        name="@Manager",
        role="Manager",
        prompt=PromptTemplate(template="You are a project manager. Delegate to specialists."),
        model_cfg=ModelConfig(provider="openai", model="gpt-4.1"),
    ),
    routes_to=["Developer", "QA"],   # roles this agent can hire on demand
)
# Register role blueprints — accepts a list of AgentCard
orchestrator_proxy.register_agent_profiles([manager_card])

# Human entry point
human_addr = orchestrator_proxy.createActor(
    HumanProxy, config=BaseConfig(name="@Human", role="Human")
)
human_proxy = actor_system.proxy_tell(human_addr, HumanProxy)

# Instantiate Manager and send the first message
manager_addr = orchestrator_proxy.createActor(
    BaseAgent, config=manager_card.get_config_copy()
)

time.sleep(0.3)

# Send a message from the human to the manager
human_proxy.send(manager_addr, AgentMessage(content="Plan the next sprint."))
```

## Communication Model

Every message in the system carries an **intent** — a declaration of what the sender
expects from the recipient. Intent is the core abstraction that drives conversation flow
between agents.

### Intent: the driving concept

When an agent sends a message, it declares its intent via a `message_type`:

| Intent | Meaning | Expected reply |
|---|---|---|
| `request` | "Do this and **bring me the result**" | `response` |
| `instruction` | "Do this (possibly for a third party)" | `acknowledgment` |
| `response` | "Here is what you asked for" | Optional |
| `notification` | "FYI — no action needed" | None |
| `acknowledgment` | "Got it" | None |

The key distinction is **who needs the result**: a `request` means "bring it back to
me", an `instruction` means "go do this on my behalf".

Intent flows through the system in two complementary ways:

1. **When sending** — The LLM chooses an intent for each outbound `Request`. The
   framework delivers it as an `AgentMessage` whose `type` field preserves that intent,
   and whose `content` is enriched with the sender and intent
   (e.g., `"You received a request from @Manager: ..."`).

2. **When receiving** — The receiving agent's LLM sees the intent both in the message
   content (conversation history) and in its structured output schema (a per-call
   docstring that includes the sender, the intent, and a reply protocol). This **double
   reinforcement** ensures the LLM knows how to respond — even when the conversation
   history contains multiple interleaved exchanges.

### AgentMessage

All inter-agent communication uses a single `AgentMessage` type:

```python
class AgentMessage(Message):
    type: Literal["request", "response", "notification", "instruction", "acknowledgment"] = "request"
    content: str
```

The `type` field carries the sender's intent through the system. The first message is
typically sent by an external system (e.g., `HumanProxy`) as a `request` with plain
content. From there, each agent's LLM decides the intent it attaches to every outbound
message.

### StructuredOutput and Request

Each LLM call produces a `StructuredOutput` with a list of outbound `Request` objects:

```python
class Request(BaseModel):
    message_type: Literal[
        "request",        # ask recipient to perform a task and reply to you with the result
        "instruction",    # direct recipient to perform a task, you may ask for acknowledgement
        "response",       # respond to a previous request
        "notification",   # send information to the recipient, no reply is expected
        "acknowledgment", # confirm receipt of an instruction, no reply is expected
    ]
    message: str
    recipient: str   # "@MemberName" (existing actor) or "RoleName" (triggers hiring)

class StructuredOutput(BaseModel):
    messages: list[Request] = []
```

The LLM chooses both the **recipient** and the **intent** for every outbound message.
`Request.message_type` flows directly into the delivered `AgentMessage.type`, so every
receiver sees the sender's intent as first-class data.

An empty list means the agent has nothing more to send — but the LLM still runs. A
`notification` or `acknowledgment` means the output list should be empty, **not** that
the LLM call is skipped. The message is still processed and added to the agent's context
for future interactions.

### Static Schema + Prompt-Carried Reply Protocol

`act()` reasons against the **static** `StructuredOutput` type directly — there is no
per-call subclass and no `type()` metaprogramming on the hot path:

```python
output = self._react_agent.run_sync(prompt, deps=self, output_type=StructuredOutput)
```

`Request.recipient` is a **plain string** with no `enum` constraint. Recipient validity is
enforced at **routing time** in `process_message()`, not in the schema:

| Recipient format | Resolution |
|---|---|
| `@MemberName` | `get_team_member(name)` → direct send (skipped if not found) |
| `RoleName` | `hire_member(role)` → create actor → send |

The reply-protocol guidance lives where the LLM actually reads it — the **prompt**.
`receiveMsg_AgentMessage()` prepends a one-line protocol (keyed on the incoming message
type via `REPLY_PROTOCOLS`) to the raw content before handing it to `process_message()`:

```
You received a request from @Human. Carry out the task, then respond to @Human.
You may also delegate to others.

<raw message content>
```

> **Note:** This supersedes the schema-constrained-recipient + docstring-injection
> mechanism from Story 5.1 / ADR-004. The intent-driven 5-type protocol is unchanged —
> only its enforcement moved from a per-call schema to the prompt + routing-time validation.

### Routing and Delivery

`process_message()` resolves each `Request.recipient` (see the table above) and sends the
**raw** `request.message` as an `AgentMessage`. The sender does not enrich the content —
the reply-protocol prefix is added by the *receiving* agent's `receiveMsg_AgentMessage()`,
so the guidance is always keyed to the intent that agent actually received:

```python
# In the receiver's receiveMsg_AgentMessage(), before process_message():
prompt = f"You received a request from @Manager. {reply_protocol}\n\n{message.content}"
```

On `LLMUsageLimitError`, the agent escalates to the first team member with
`role="human"` via `notify_human()`.

### HumanProxy

`HumanProxy` extends `UserProxy` from `akgentic-core`. It serves two roles:

- **Telemetry sink** — `receiveMsg_AgentMessage()` pushes incoming messages into the
  event system. The consumer is pluggable: console printer, WebSocket to a frontend,
  WhatsApp, email, etc.
- **Human input bridge** — `process_human_input()` routes a human's reply back to the
  agent that asked.

```python
# Send a message from the human to an agent
human_proxy = actor_system.proxy_tell(human_addr, HumanProxy)
human_proxy.send(agent_addr, AgentMessage(content="Do X"))

# Route a human reply back to the agent that asked
human_proxy.process_human_input("My answer", original_message)
```

## Message Protocol

The 5-type intent protocol controls conversation flow. When an incoming message is
received, the matching `REPLY_PROTOCOLS` instruction is prepended to the **user prompt**
(not the output schema), so the LLM reads the guidance inline with the content:

| Intent | Receiver instruction (`REPLY_PROTOCOLS`) |
|---|---|
| `request` | "Carry out the task, then respond to {sender}. You may also delegate to others." |
| `response` | "Analyse the response, then continue or end the exchange." |
| `instruction` | "Carry out the task, then acknowledge to {sender} if requested." |
| `notification` | "Informational message. No reply is expected." |
| `acknowledgment` | "No further action needed." |

The protocol is **soft guidance**, not framework enforcement. The LLM is guided to send no
further messages for `notification` and `acknowledgment`, but the framework processes
whatever the LLM returns. This is intentional: LLMs are probabilistic, and rigid
enforcement would be brittle.

**No reply does not mean no processing.** When the protocol says "return an empty list",
the LLM still runs — it absorbs the message into its context, which may inform future
decisions. The empty list simply means no outbound messages are sent.

## Team Composition

### AgentCard

Declarative role definition registered with the Orchestrator. Acts as a blueprint: agents
can be instantiated from it on demand without hard-coding actor addresses.

```python
AgentCard(
    role="Developer",
    description="Writes and reviews code",
    skills=["python", "testing"],
    agent_class="akgentic.agent.BaseAgent",   # FQCN string or class reference
    config=AgentConfig(...),
    routes_to=["Reviewer", "Tester"],         # roles this agent can hire
)
```

`register_agent_profiles([card, ...])` stores cards in the Orchestrator so any agent can
hire a role by name without knowing the class.

`AgentCard.get_config_copy()` returns a fresh `AgentConfig` suitable for `createActor()`.

### Dynamic Hiring

When `process_message()` sees `recipient="Developer"` (no `@` prefix), it calls
`hire_member("Developer")` which:

1. Looks up the `AgentCard` for `"Developer"` in the Orchestrator
2. Calls `createActor(agent_class, config=card.get_config_copy())`
3. Returns the new actor address for immediate message delivery

The LLM in the sending agent triggers this transparently by naming a role instead of a
team member.

### EventSubscriber

Attach an `EventSubscriber` to the Orchestrator to observe all messages and events:

```python
class MessagePrinter(EventSubscriber):
    def on_message(self, message: Message) -> None:
        if isinstance(message, SentMessage):
            print(f"[{message.sender.name}] → {message.recipient.name}: {message.message.content}")
        elif isinstance(message, EventMessage) and isinstance(message.event, ToolCallEvent):
            print(f"TOOL: {message.event.tool_name}")

orchestrator_proxy.subscribe(MessagePrinter())
```

## Configuration

### AgentConfig

Extends `BaseConfig` from `akgentic-core`:

| Field | Type | Default | Description |
|---|---|---|---|
| `prompt` | `PromptTemplate` | `PromptTemplate()` | Agent backstory rendered into `AgentState.backstory` and injected as LLM system prompt |
| `model_cfg` | `ModelConfig` | `ModelConfig()` | LLM provider, model name, API settings |
| `runtime_cfg` | `RuntimeConfig` | `RuntimeConfig()` | Retries, parallel tools, HTTP timeouts |
| `usage_limits` | `UsageLimits` | `UsageLimits()` | Token and request caps |
| `max_help_requests` | `int` | `5` | Maximum delegation depth before error |
| `tools` | `list[ToolCard]` | `[]` | Tool cards; `TeamTool` is always prepended automatically |

### AgentState

Runtime state extending `BaseState`:

| Field | Type | Description |
|---|---|---|
| `backstory` | `str` | `config.prompt` rendered at `on_start()`, injected as LLM system context on every call |

## Tool Channels

`ToolFactory` organises tool cards into three channels:

| Channel | Consumer | Examples |
|---|---|---|
| `TOOL_CALL` | LLM via pydantic-ai tools | `hire_members()`, `fire_members()`, `web_search()`, `workspace_read()` |
| `SYSTEM_PROMPT` | LLM system prompt (per call) | team roster, role profiles, backstory, mailbox notifications |
| `COMMAND` | Python caller | `cmd_hire_member()`, `cmd_fire_member()`, `cmd_get_planning()`, `cmd_get_team_roster()` |

`TeamTool` is always prepended to `config.tools` if not already present, ensuring every
`BaseAgent` can hire and fire members.

### Programmatic Commands

Access via `actor_system.proxy_ask(agent_addr, BaseAgent)`:

| Command | Returns | Description |
|---|---|---|
| `cmd_hire_member(role)` | `ActorAddress \| str` | Hire by role; returns error string on failure |
| `cmd_fire_member(name)` | `str` | Fire by name; returns confirmation or error |
| `cmd_get_planning()` | `str` | Full team planning text (requires `PlanningTool`) |
| `cmd_get_team_roster()` | `str` | Current team member list |
| `cmd_get_role_profiles()` | `str` | Available roles and descriptions |
| `cmd_get_planning_task(id)` | `Task \| str` | Single planning task by ID |
| `get_usage_summary(by_run)` | `AgentUsageSummary` | Aggregated LLM usage and cost; queries orchestrator for `LlmUsageEvent`s via `aggregate_usage()` from `akgentic.llm`. Pass `by_run=True` for per-run breakdown. Callable via Pykka proxy. |

### Media Expansion

When `WorkspaceTool` is in `config.tools`, `act()` expands inline file references before
the LLM call:

```
!!file.png               → BinaryContent injected into the prompt
!!"my screenshot.png"   → same, for paths with spaces
!!*.png                  → glob — all matching files
!!nonexistent.png        → "!!_nonexistent.png_[Error: no image found]" forwarded to LLM
```

Expansion happens in `act()` before `run_sync()`. Errors and document hints
(`[=> Use workspace_read tool]`) are forwarded to the LLM rather than silently dropped.
Agents without `WorkspaceTool` are unaffected — the expansion block is a no-op.

## Examples

```bash
cd packages/akgentic-agent
uv run python examples/simple_team.py
```

| Script | Topic |
|---|---|
| `simple_team.py` | Three-role interactive team with search, workspace, planning, `/commands`, and `/usage` for per-agent cost reporting via `EventSubscriber` |

See the [Examples README](examples/README.md) for full descriptions and running instructions.

## Documentation

- [Agent Collaboration System](docs/agent-collaboration.md) — Collaboration model,
  routing mechanics, delegation patterns, and typed protocol walkthrough

## Development

### Prerequisites

- Python 3.12+
- [uv](https://docs.astral.sh/uv/) package manager

### Setup

```bash
uv sync --all-packages --all-extras
```

### Commands

```bash
# Run tests
uv run pytest packages/akgentic-agent/tests/

# Run tests with coverage
uv run pytest packages/akgentic-agent/tests/ --cov=akgentic.agent --cov-fail-under=80

# Lint
uv run ruff check packages/akgentic-agent/src/

# Format
uv run ruff format packages/akgentic-agent/src/

# Type check
uv run mypy packages/akgentic-agent/src/
```

### CI Pipeline

The package uses GitHub Actions for continuous integration. On every push and pull
request, the pipeline:

1. Checks out the full `akgentic-quick-start` workspace (with all submodules)
2. Overrides the `akgentic-agent` submodule with the current branch
3. Installs all dependencies via `uv sync --all-packages --all-extras`
4. Runs **mypy** (strict type checking)
5. Runs **ruff** (linting)
6. Runs **pytest** with coverage (minimum 80%, branch coverage enabled)
7. Updates the coverage badge gist on `master` pushes

> **Note:** No pre-commit hooks are configured in this package. Quality checks run
> exclusively in CI.

### Project Structure

```
src/akgentic/agent/
    __init__.py          # Public API: BaseAgent, AgentConfig, HumanProxy, AgentMessage
    agent.py             # BaseAgent — actor + LLM + tool composition, routing logic
    config.py            # AgentConfig, AgentState
    human_proxy.py       # HumanProxy — human-in-the-loop bridge
    messages.py          # AgentMessage with typed protocol
    output_models.py     # StructuredOutput, Request, REPLY_PROTOCOLS
examples/                # Runnable examples with README
tests/                   # Tests organised by module
docs/
    agent-collaboration.md
```

## License

See the repository root for license information.
