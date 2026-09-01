<div align="center">
<img src="assets/akgents.png" alt="Akgents - Powered by Yuma" width="400">
<br><br>

**Modern actor-based agent framework for Python 3.12+**

A comprehensive framework for building intelligent multi-agent systems with LLM integration, dynamic team composition, and actor-based architecture.

</div>

| Package | CI | Coverage | Dependencies |
|---|---|---|---|
| [akgentic-core](https://github.com/b12consulting/akgentic-core) <br> Actor framework, messaging, and orchestrator | [![CI](https://github.com/b12consulting/akgentic-core/actions/workflows/ci.yml/badge.svg)](https://github.com/b12consulting/akgentic-core/actions/workflows/ci.yml) | [![Coverage](https://img.shields.io/endpoint?url=https://gist.githubusercontent.com/gpiroux/5fae2fa4f4f3cd3fc5cc08f5d2a7da44/raw/coverage.json)](https://github.com/b12consulting/akgentic-core/actions/workflows/ci.yml) | — |
| [akgentic-llm](https://github.com/b12consulting/akgentic-llm) <br> Multi-provider LLM integration and REACT pattern | [![CI](https://github.com/b12consulting/akgentic-llm/actions/workflows/ci.yml/badge.svg)](https://github.com/b12consulting/akgentic-llm/actions/workflows/ci.yml) | [![Coverage](https://img.shields.io/endpoint?url=https://gist.githubusercontent.com/gpiroux/dd80a44fe9e2e27b46f7f3431e19202f/raw/coverage.json)](https://github.com/b12consulting/akgentic-llm/actions/workflows/ci.yml) | — |
| [akgentic-tool](https://github.com/b12consulting/akgentic-tool) <br> Tool abstractions, workspace, planning, web search, MCP, ... | [![CI](https://github.com/b12consulting/akgentic-tool/actions/workflows/ci.yml/badge.svg)](https://github.com/b12consulting/akgentic-tool/actions/workflows/ci.yml) | [![Coverage](https://img.shields.io/endpoint?url=https://gist.githubusercontent.com/gpiroux/c0f2e0aa0a8c184ee8823dd4feefddd5/raw/coverage.json)](https://github.com/b12consulting/akgentic-tool/actions/workflows/ci.yml) | core |
| [akgentic-team](https://github.com/b12consulting/akgentic-team) <br> Team lifecycle, event sourcing, YAML/MongoDB persistence | [![CI](https://github.com/b12consulting/akgentic-team/actions/workflows/ci.yml/badge.svg)](https://github.com/b12consulting/akgentic-team/actions/workflows/ci.yml) | [![Coverage](https://img.shields.io/endpoint?url=https://gist.githubusercontent.com/gpiroux/e986fdd05c8c3d93e718782dc034e0c1/raw/coverage.json)](https://github.com/b12consulting/akgentic-team/actions/workflows/ci.yml) | core |
| [akgentic-agent](https://github.com/b12consulting/akgentic-agent) <br> LLM-powered agents with typed message routing | [![CI](https://github.com/b12consulting/akgentic-agent/actions/workflows/ci.yml/badge.svg)](https://github.com/b12consulting/akgentic-agent/actions/workflows/ci.yml) | [![Coverage](https://img.shields.io/endpoint?url=https://gist.githubusercontent.com/gpiroux/69ad301e9b6491972aa7324eb8953f8a/raw/coverage.json)](https://github.com/b12consulting/akgentic-agent/actions/workflows/ci.yml) | core, llm, tool |
| [akgentic-catalog](https://github.com/b12consulting/akgentic-catalog) <br> Configuration registry for teams, YAML/MongoDB persistence | [![CI](https://github.com/b12consulting/akgentic-catalog/actions/workflows/ci.yml/badge.svg)](https://github.com/b12consulting/akgentic-catalog/actions/workflows/ci.yml) | [![Coverage](https://img.shields.io/endpoint?url=https://gist.githubusercontent.com/gpiroux/35850b0665f1d1dd2402c43362ee4d35/raw/coverage.json)](https://github.com/b12consulting/akgentic-catalog/actions/workflows/ci.yml) | core, llm, tool, team |
| [akgentic-infra](https://github.com/b12consulting/akgentic-infra) <br> Infrastructure backend — protocol abstractions, community/department/enterprise tiers | [![CI](https://github.com/b12consulting/akgentic-infra/actions/workflows/ci.yml/badge.svg)](https://github.com/b12consulting/akgentic-infra/actions/workflows/ci.yml) | [![Coverage](https://img.shields.io/endpoint?url=https://gist.githubusercontent.com/gpiroux/73f98d6bf131b998029a9d28a0007614/raw/coverage.json)](https://github.com/b12consulting/akgentic-infra/actions/workflows/ci.yml) | core, llm, tool, agent, catalog, team |
| [akgentic-frontend](https://github.com/b12consulting/akgentic-frontend) <br> Angular-based web UI | — | — | — |

## What is the Akgentic Framework?

Most agent frameworks give you a **pipeline**: a chain or graph you draw in
advance, where control flows from step to step. That works until the problem
stops being a pipeline — until one agent needs to ask another a question, wait
for an answer, and carry on; until a coordinator needs to bring in a specialist
it did not know it would need.

The Akgentic Framework gives you a **team** instead. Each agent runs on its
own thread with its own mailbox, its own context and its own LLM. They send
each other typed messages, work in parallel, and are hired and fired while the
system is running. You describe *who is on the team and how each one should
behave* — not the order things happen in.

### Agents are actors

The Akgentic Framework is built on the
[actor model](https://en.wikipedia.org/wiki/Actor_model) (via
[Pykka](https://pykka.readthedocs.io/)), which has run telecoms and messaging
systems for decades. One rule makes it work: **actors share nothing and
communicate only by messages.**

That rule buys four things that agentic systems need badly:

| | |
|---|---|
| **Isolation** | An agent's conversation, context and memory are its own. No shared mutable state, so no locks and no cross-talk between agents. |
| **Concurrency for free** | Three agents thinking at once is the default, not an optimisation. A slow tool call blocks one mailbox, not the team. |
| **Failure containment** | An agent that crashes or loops is one actor. The orchestrator sees it and the rest of the team keeps working. |
| **Live membership** | Actors are created and stopped at runtime, so a manager can hire a specialist during the process. |

A team looks like this — a human talking to a manager that delegates, with an
orchestrator watching every message go by:

```mermaid
flowchart TD
    H["@Human"] <--> M["@Manager"]
    H <--> A["@Assistant"]
    H <--> E["@Expert"]
    M <--> A
    M <--> E
    A <--> E
    O["Orchestrator<br/><i>tracks the team, streams events</i>"]
    O -.observes.- H
    O -.- M
    O -.- A
    O -.- E
```

**Every line is two-way, and the graph is complete on purpose.** The runtime
imposes no topology: any actor can send to any other, and the human can address
any agent directly. What makes a team behave like a team — the manager
delegating rather than answering everything itself — is written in the agents'
prompts, not enforced by the plumbing.

Every arrow is a validated Pydantic model, not a dict, so a malformed message
fails where it is sent rather than three hops later.

### A team is declared, not wired

An agent is an **`AgentCard`**: who it is, what it is good at, which model it
runs on, and which tools it may call.

```python
manager = AgentCard(
    description="Coordinates the team",
    skills=["coordination", "delegation"],
    agent_class="akgentic.agent.BaseAgent",
    config=AgentConfig(
        name="@Manager",
        role="Manager",
        prompt=PromptTemplate(
            template=(
                "You are a helpful manager. Delegate specialised questions to "
                "@Expert and research to @Assistant, then report back."
            ),
        ),
        model_cfg=ModelConfig(provider="openai", model="gpt-5.2"),
        tools=tools,
    ),
)
```

**The prompt is where collaboration rules live.** Who to delegate to, when to
answer directly, when to come back to the human — that is prose in the system
prompt, which is what makes it something you can iterate on rather than a
topology you have to redeclare. The model does not have to be told who exists:
the live team roster reaches it as context, so it can address a colleague hired
after the prompt was written.

Registering a card with the orchestrator makes that role *available* to the
team, which hires from it on demand rather than starting every agent up front.

Routing then happens two ways, and both end up as the same message send:

- **You address an agent** — `@Expert what is your role?` from the human;
- **An agent addresses another** — it returns a `StructuredOutput` containing
  requests, and `BaseAgent` delivers them.

The same team can be written in Python, as above, or declared entirely in YAML
and loaded from a **catalog** — same objects, no code.

### From component library to enterprise infrastructure

The Akgentic Framework is a stack of small packages, each published on its
own. Take the one layer you need, or the whole platform — **nothing above
forces anything below**, and `akgentic-core` has zero infrastructure
dependencies.

| Layer | Package | Reach for it when you want… |
|---|---|---|
| **Actors** | `akgentic-core` | the actor runtime, messaging and orchestrator — no LLM, no server |
| **Models** | `akgentic-llm` | one API over OpenAI, Anthropic, Google, Mistral, Azure… with cost limits |
| **Tools** | `akgentic-tool` | to give agents capabilities: workspace, search, planning, MCP |
| **Agents** | `akgentic-agent` | LLM-backed agents with the typed message protocol and human proxy |
| **Teams** | `akgentic-team` | lifecycle and event sourcing: create, resume, stop, replay |
| **Catalog** | `akgentic-catalog` | to declare agents, tools and teams in YAML instead of code |
| **Platform** | `akgentic-infra` + `akgentic-frontend` | a REST/WebSocket server and a web UI to run teams from |

The top layer also scales *outwards*. The same server code runs in three
deployment tiers, chosen by configuration rather than a rewrite:

| Tier | Shape | For |
|---|---|---|
| **Community** | single process, files on disk | development, demos, one team at a time — **this bundle** |
| **Department** | Docker Compose, Mongo + Redis, several workers | a team of people sharing one host |
| **Enterprise** | Kubernetes/Dapr, SSO and RBAC | production, many tenants |

Community is what ships here and what the guides below run. Department and
enterprise implement the same protocols in the sibling
`akgentic-infra-department` and `akgentic-infra-enterprise` packages, so moving
up a tier changes deployment, not your agents.

## Get started

Pick the path that matches what you are doing. Each is self-contained — you do
not need to read the others.

| I want to… | Path | Needs |
|---|---|---|
| **Use it as a library** | [Installation](docs/installation.md) | Python 3.12+ |
| **Try it, fastest** | [Run with Docker Compose](docs/run-docker.md) | Docker |
| **Drive a simple team from the terminal** | [Run the CLIs](docs/run-cli.md) | Python 3.12+ |
| **Run the server and UI from terminal** | [Run locally](docs/run-local.md) | Python 3.12+ |
| **Want to run from source code?** | [Run from source](docs/run-from-source.md) | Python 3.12+, Node, submodules |

Two commands, if you just want to see it work:

```bash
cp .env.example .env      # then fill in OPENAI_API_KEY
docker compose up -d --build
```

Web UI on <http://localhost:4200>, API docs on <http://localhost:8000/docs>.

![Akgentic Frontend](assets/akgentic_frontend.png)
![Akgentic OpenAPI](assets/akgentic_openapi.png)

### Where things come from

Every path offers the same choice: **published artifacts** (the default —
nothing to build, no submodules) or **local sources** (for changing framework
code). The naming is deliberately the same on both sides.

| | Published (default) | Local sources |
|---|---|---|
| Python packages | PyPI wheels, pinned as a release set | `packages/akgentic-*`, editable |
| Web UI | prebuilt bundle from its GitHub release | Angular build from `packages/akgentic-frontend` |
| In Docker | `BUILD_SOURCE=wheel`, `FRONTEND_SOURCE=release` | `BUILD_SOURCE=source`, `FRONTEND_SOURCE=source` |

`akgentic-framework` is a meta-distribution: no code of its own, only a pinned
set of requirements, so an extra installs the exact subpackage versions built
and tested together for that release.

## Architecture

Each package lives in its own repository and publishes itself to PyPI. This
repository is the entry point: it pins a coherent set of them and, in source
mode, mounts them as submodules under `packages/`.

```
packages/                 (submodules — empty until `git submodule update --init`)
  akgentic-core/        → Zero-dependency actor framework (Pykka, messaging, orchestrator)
  akgentic-llm/         → LLM integration layer (pydantic-ai, multi-provider, REACT pattern)
  akgentic-team/        → Team lifecycle management (create/resume/stop/delete, event sourcing)
  akgentic-tool/        → Tool abstractions (ToolCard, ToolFactory, workspace, planning, search, KG, MCP)
  akgentic-agent/       → Collaborative agent patterns (BaseAgent, typed message protocol, HumanProxy)
  akgentic-catalog/     → Configuration registry (YAML-driven CRUD catalogs)
  akgentic-infra/       → Infrastructure backend (three-tier: community, department, enterprise)
  akgentic-frontend/    → Angular web UI (REST + WebSocket client for akgentic-infra)
```

**Dependency graph** (lower layers have no upward dependencies):

```
akgentic-core     ──depends on──>  (pydantic, pykka)  ← zero infrastructure deps
akgentic-llm      ──depends on──>  (pydantic-ai, httpx, tenacity)
akgentic-team     ──depends on──>  akgentic-core (only)
akgentic-tool     ──depends on──>  akgentic-core + (pydantic, pydantic-ai, tavily-python, httpx)
akgentic-agent    ──depends on──>  akgentic-core + akgentic-llm + akgentic-tool
akgentic-catalog  ──depends on──>  akgentic-core + akgentic-llm + akgentic-tool + akgentic-team
akgentic-infra    ──depends on──>  akgentic-core + akgentic-llm + akgentic-tool + akgentic-agent + akgentic-catalog + akgentic-team
akgentic-frontend ──depends on──>  akgentic-infra (REST + WebSocket API)
```

### akgentic-core

Core actor framework with zero infrastructure dependencies.

**Features:**

- **Actor-Based Architecture** - Scalable message-passing concurrency model
- **Type-Safe Messaging** - Pydantic-validated message definitions
- **Orchestrator Pattern** - Centralized agent coordination and event observation
- **AgentCard System** - Role-based agent definitions and dynamic hiring
- **In-Memory Execution** - Fast, testable, and easy to deploy

**Quick Example:**

```python
from akgentic.core import ActorSystem, Akgent
from akgentic.core.messages import Message

class EchoMessage(Message):
    content: str

class EchoAgent(Akgent):
    def receiveMsg_EchoMessage(self, message: EchoMessage, sender):
        print(f"Received: {message.content}")

system = ActorSystem()
agent = system.createActor(EchoAgent)
system.tell(agent, EchoMessage(content="Hello!"))
```

See [the akgentic-core README](https://github.com/b12consulting/akgentic-core/blob/master/README.md) for full documentation.

### akgentic-llm

LLM integration layer supporting OpenAI, Anthropic, Google, and more.

**Features:**

- **Multi-Provider Support** - OpenAI, Azure, Anthropic, Google, Mistral, NVIDIA
- **REACT Pattern** - Reasoning and Acting with tool execution
- **Usage Limits** - Cost control and safety with granular token limits
- **HTTP Retry Logic** - Production-grade reliability with configurable backoff
- **Context Management** - Checkpointing, rewind, and compactification
- **Dynamic Prompts** - Programmatic system prompt registry

See [the akgentic-llm README](https://github.com/b12consulting/akgentic-llm/blob/master/README.md) for details.

### akgentic-tool

Tool infrastructure and domain tool implementations.

**Features:**

- **ToolCard / ToolFactory** — Pydantic-serializable tool definitions; factory aggregates cards into LLM-callable tools, system prompts, and programmatic commands
- **3-Channel System** — `TOOL_CALL` (LLM invokes), `SYSTEM_PROMPT` (injected context), `COMMAND` (programmatic API)
- **WorkspaceTool** — Read/write filesystem access with glob, grep, edit, patch, PDF/image reading
- **PlanningTool** — Shared actor-based task board with semantic search
- **KnowledgeGraphTool** — Persistent entity/relation storage with hybrid search
- **VectorStoreTool** — Named vector stores backing semantic search for the other tools
- **SearchTool** — Tavily web search and content fetching
- **MCPTool** — Model Context Protocol server integration (HTTP+SSE and stdio)
- **TeamTool** — Hire/fire members, role profiles, roster and who-is-working activity
- **NotificationTool** — Schedule a delayed message to yourself via the deferred-result actor mechanism
- **RetriableError** — Framework-agnostic retry signal for recoverable failures

See [the akgentic-tool README](https://github.com/b12consulting/akgentic-tool/blob/master/README.md) for complete documentation.

### akgentic-agent

Collaborative agent patterns — the integration layer combining core, llm, and tool.

**Features:**

- **BaseAgent** — LLM-powered agent composing `ReactAgent` and `ToolFactory`
- **Typed Message Protocol** — 5-type intent system (`request`, `response`, `notification`, `instruction`, `acknowledgment`)
- **Intent-Driven Routing** — LLM chooses recipients and message types via `StructuredOutput`; schema-constrained recipients prevent invalid routing
- **Dynamic Team Composition** — Hire/fire agents by role at runtime
- **HumanProxy** — Seamless human-in-the-loop interactions
- **Media Expansion** — `!!file.png` and `!!*.md` inline file injection into LLM prompts

See [the akgentic-agent README](https://github.com/b12consulting/akgentic-agent/blob/master/README.md) for complete documentation.

### akgentic-catalog

Configuration-driven team assembly from YAML files — no code changes needed.

**Features:**

- **Unified `Entry` model** — one Pydantic shape for every kind (`team`, `agent`, `tool`, `model`, `prompt`, `meta`), each namespace anchored by a team or meta entry
- **One namespace, one agent team** — the namespace is the organising unit; `global` namespaces share entries cross-namespace via `shareable`/`public`
- **Strict validation** — unknown payload keys are errors, never silent drops; payloads validate against their `model_type` Pydantic class
- **Ref markers** — a payload embeds `{"__ref__": "global.id_gpt_41"}` as a pure pointer, resolved at load time
- **Namespace bundles** — export/import every entry in a namespace as a single YAML document
- **YAML / MongoDB / PostgreSQL backends** — file-per-entry YAML (default) or database collections
- **Delete protection** — prevents removing entries still referenced by others
- **CLI + REST API** — `ak-catalog` CLI and FastAPI REST layer for all CRUD operations

See [the akgentic-catalog README](https://github.com/b12consulting/akgentic-catalog/blob/master/README.md) for complete documentation.

### akgentic-team

Team lifecycle management with crash-recovery and event sourcing.

**Features:**

- **TeamManager** — Create, resume, stop, delete teams via a lifecycle facade
- **Event Sourcing** — Events persisted live as they flow; crash recovery without explicit checkpoints
- **TeamCard** — Declarative team definition (agents, entry point, supervisors)
- **YAML / MongoDB stores** — Zero-infra default (YAML), scalable alternative (MongoDB via `[mongo]` extra)
- **Resume from any STOPPED team** — Rebuild LLM conversation history from event replay log

See [the akgentic-team README](https://github.com/b12consulting/akgentic-team/blob/master/README.md) for complete documentation.

### akgentic-infra

Infrastructure backend for the Akgentic platform. Provides protocol abstractions that decouple the server and CLI from any specific deployment model, available in three tiers:

| Tier | Target | Key characteristics |
|---|---|---|
| **Community** | Single process | `NoAuth`, local placement, YAML event store, local filesystem — zero external dependencies |
| **Department** | Docker Compose | OAuth2 + API key, Redis-backed cache and channels, MongoDB persistence, HTTP remote workers |
| **Enterprise** | Kubernetes / Dapr | SSO + RBAC, Dapr service invocation, auto-restore recovery, OTel observability, NFS/EFS storage |

**Features:**

- **Protocol abstractions** — Auth, placement, worker lifecycle, team interaction, persistence, and observability are all swappable interfaces
- **Community tier** — Fully functional single-process deployment with no external services required
- **Department tier** — Redis-backed channels and state, MongoDB event store, HTTP remote workers for Docker Compose setups
- **Enterprise tier** — Dapr-native service mesh, auto-restore recovery, zone-aware placement, and full OpenTelemetry integration

See [the akgentic-infra README](https://github.com/b12consulting/akgentic-infra/blob/master/README.md) for the full three-tier architecture and deployment guide.

### akgentic-frontend

Angular single-page application providing real-time visualization and management of multi-agent teams. Connects to `akgentic-infra` via REST and WebSocket.

**Features:**

- **Directed agent graph** — Live ECharts visualization of agents (nodes) and message flows (edges); updates incrementally as events arrive
- **Real-time message stream** — Color-coded chat panel with per-agent message history and playback controls (play / pause / step-forward / step-back)
- **Agent inspection** — LLM context viewer and schema-driven state editor per agent
- **Workspace explorer** — File browser for agent workspaces with upload support
- **Knowledge graph** — Entity/relation visualization for agents using `KnowledgeGraphTool`
- **Auth-ready** — API key and OAuth2 authentication with route guards

**Key libraries:** Angular 19, PrimeNG 19, ECharts (ngx-echarts), RxJS, ngx-markdown, Monaco Editor.

See [the akgentic-frontend README](https://github.com/b12consulting/akgentic-frontend/blob/master/README.md) for setup and development instructions.

## Design Principles

1. **Zero infrastructure dependencies** in core
2. **80% minimum test coverage** (enforced)
3. **Comprehensive type hints** (mypy strict mode)
4. **Modular packages** (use what you need)
5. **10-minute time-to-first-agent** target

## Testing Standards

All packages maintain:

- ✅ 80%+ test coverage
- ✅ mypy strict mode compliance
- ✅ Comprehensive unit tests
- ✅ Integration tests for cross-package features

## Documentation

**Guides**

- [Installation](docs/installation.md) — PyPI, extras, from a clone
- [Run with Docker Compose](docs/run-docker.md) — the whole stack in containers
- [Run locally](docs/run-local.md) — server and UI as local processes
- [Run from source](docs/run-from-source.md) — working on framework code
- [Run the CLI](docs/run-cli.md) — scripted teams, `ak-infra`, `ak-catalog`
- [Development](docs/development.md) — repository layout, cutting a release

**Packages**

- [akgentic-core README](https://github.com/b12consulting/akgentic-core/blob/master/README.md) - Core framework documentation
- [akgentic-core examples](https://github.com/b12consulting/akgentic-core/tree/master/examples) - Hands-on tutorials
- [akgentic-llm README](https://github.com/b12consulting/akgentic-llm/blob/master/README.md) - LLM integration and multi-provider support
- [akgentic-tool README](https://github.com/b12consulting/akgentic-tool/blob/master/README.md) - Tool infrastructure and domain tools
- [akgentic-agent README](https://github.com/b12consulting/akgentic-agent/blob/master/README.md) - LLM agents and typed message protocol
- [akgentic-catalog README](https://github.com/b12consulting/akgentic-catalog/blob/master/README.md) - Configuration registry
- [akgentic-team README](https://github.com/b12consulting/akgentic-team/blob/master/README.md) - Team lifecycle management
- [akgentic-infra README](https://github.com/b12consulting/akgentic-infra/blob/master/README.md) - Infrastructure backend plugins

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for development guidelines, branch naming conventions, commit standards, and how to open a PR from a fork.

## License

This project is licensed under the [GNU Affero General Public License v3.0 (AGPL-3.0)](LICENSE).

> **Dual licensing & CLA** — Akgentic is available under the AGPL-3.0 open-source license. A commercial license is also planned for organizations that require alternative terms. Contact [Yuma](https://www.weareyuma.com/en/contact) for more information. External contributions will be accepted once a Contributor License Agreement (CLA) is in place. Until then, please hold off on submitting pull requests.
