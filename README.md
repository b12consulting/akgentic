# akgentic-tool

[![CI](https://github.com/b12consulting/akgentic-tool/actions/workflows/ci.yml/badge.svg)](https://github.com/b12consulting/akgentic-tool/actions/workflows/ci.yml)
[![Coverage](https://img.shields.io/endpoint?url=https://gist.githubusercontent.com/gpiroux/c0f2e0aa0a8c184ee8823dd4feefddd5/raw/coverage.json)](https://github.com/b12consulting/akgentic-tool/actions/workflows/ci.yml)

Tool infrastructure and domain tools for the [Akgentic](https://github.com/b12consulting/akgentic-quick-start)
multi-agent framework. Define, compose, and expose capabilities to LLM agents through a unified
channel system — as tool calls, system prompt injections, or programmatic commands.

## Table of Contents

- [Overview](#overview)
- [Installation](#installation)
- [Quick Start](#quick-start)
- [Architecture](#architecture)
- [Channel System](#channel-system)
- [Tool Catalog](#tool-catalog)
  - [WorkspaceTool](#workspacetool)
  - [PlanningTool](#planningtool)
  - [KnowledgeGraphTool](#knowledgegraphtool)
  - [SearchTool](#searchtool)
  - [TeamTool](#teamtool)
  - [MCPTool](#mcptool)
  - [ExecTool](#exectool)
- [Error Handling](#error-handling)
- [Optional Extras](#optional-extras)
- [Development](#development)
- [License](#license)

## Overview

`akgentic-tool` is the capability layer between the Akgentic actor system and the LLM agents
running inside it. It provides:

- **Abstract contracts** — `ToolCard` and `BaseToolParam` define the serializable configuration
  model every tool follows; `ToolFactory` aggregates multiple cards into agent-ready callables
- **Channel system** — each capability declares whether it surfaces as a `TOOL_CALL` (LLM
  invokes it), a `SYSTEM_PROMPT` (injected into context before each LLM call), or a `COMMAND`
  (programmatic call from another agent or the orchestrator)
- **Observer protocols** — `ToolObserver`, `ActorToolObserver`, and `TeamManagementToolObserver`
  give tools access to the actor system, event emission, and team lifecycle hooks.
  > **Migration note (ADR-018):** `ToolCallEvent` has been removed from `akgentic-tool`. Tool call
  > observability is now handled by `akgentic-llm`. Import `ToolCallEvent` and `ToolReturnEvent`
  > from `akgentic.llm.event` instead.
- **RetriableError** — tools signal recoverable failures; `ToolFactory` translates them to the
  framework-specific retry exception without coupling tool logic to pydantic-ai
- **Domain tools** — seven production-ready tool implementations covering workspace I/O, task
  planning, knowledge graph, web search, team management, MCP server integration, and sandboxed
  shell execution

```
ToolCard(s)
    │
    ▼
ToolFactory
    │
    ├── get_tools()          → list[Callable]  ─────▶ LLM ReAct loop
    ├── get_system_prompts() → list[Callable]  ─────▶ injected into LLM context
    ├── get_commands()       → dict[type, Callable] ▶ orchestrator / other agents
    └── get_toolsets()       → list[MCPServer] ─────▶ pydantic-ai MCP integration
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

All dependencies (`akgentic-core`, `pydantic-ai`, `tavily-python`) resolve automatically via
workspace configuration.

### Optional Extras

```bash
# Semantic search for planning and knowledge graph (numpy + OpenAI embeddings)
uv sync --extra vector_search

# Binary file reading for workspace_read (PDF, DOCX, XLSX, PPTX via MarkItDown)
uv sync --extra docs

# Image resizing for workspace_view (Pillow)
uv sync --extra vision

# Everything
uv sync --all-extras
```

## Quick Start

Attach tools to an agent configuration:

```python
from akgentic.tool import (
    ToolFactory,
    WorkspaceTool,
)
from akgentic.tool.planning import PlanningTool
from akgentic.tool.search import SearchTool

# Build a factory with multiple tools
factory = ToolFactory(
    tool_cards=[
        WorkspaceTool(),          # full read/write workspace access
        PlanningTool(),           # shared team task board
        SearchTool(),             # Tavily web search + fetch
    ],
    observer=agent,               # ActorToolObserver (provided by BaseAgent)
    retry_exception=ModelRetry,   # pydantic-ai retry — injected by BaseAgent
)

# Get callables ready for pydantic-ai agent registration
tools = factory.get_tools()            # LLM-callable functions
prompts = factory.get_system_prompts() # dynamic context injections
commands = factory.get_commands()      # programmatic calls from orchestrator
```

Grant read-only workspace access to a reviewer agent:

```python
WorkspaceTool(read_only=True)
```

Custom planning configuration with semantic search:

```python
PlanningTool(
    get_planning=GetPlanning(filter_by_agent=False),  # show all tasks, not just own
)
```

## Architecture

The package follows a two-layer design: a **core layer** of abstract contracts and a **domain
layer** of independent tool implementations. Domain submodules never import each other — cross-
tool composition happens at the agent level.

```
┌──────────────────────────────────────────────────────────────────┐
│  Domain Tools                                                    │
│  workspace  │  planning  │  knowledge_graph  │  search  │  team  │  mcp  │  sandbox  │
├──────────────────────────────────────────────────────────────────┤
│  Core Layer: ToolCard, BaseToolParam, ToolFactory, Channels      │
│              RetriableError, Observer protocols                   │
├──────────────────────────────────────────────────────────────────┤
│  Vector infrastructure (optional): VectorIndex, EmbeddingService │
├──────────────────────────────────────────────────────────────────┤
│  akgentic-core (Pykka actors, ActorAddress, Orchestrator)        │
└──────────────────────────────────────────────────────────────────┘
```

### ToolCard

`ToolCard` is the base class for all tool configurations. It is a Pydantic model — fully
serializable, round-trippable through `model_dump()` / `model_validate()`.

**Serialization rules (Golden Rule 1b):**
- All fields must use serializable types (primitives, `BaseModel` subclasses, enums, collections)
- `ConfigDict(arbitrary_types_allowed=True)` is **forbidden** on any `ToolCard` subclass
- Runtime state (actor proxies, filesystem handles) goes in `PrivateAttr` — excluded from serialization

```python
class MyTool(ToolCard):
    config_value: str = "default"
    _runtime_handle: Handle | None = PrivateAttr(default=None)  # not serialized

    def observer(self, observer: ActorToolObserver) -> "MyTool":
        self._observer = observer
        self._runtime_handle = setup_handle()
        return self

    def get_tools(self) -> list[Callable]:
        handle = self._runtime_handle

        def my_tool(input: str) -> str:
            """Do something with input."""
            try:
                return handle.process(input)
            except ValueError as e:
                raise RetriableError(f"Invalid input: {e}")

        return [my_tool]
```

### ToolFactory

Aggregates multiple `ToolCard` instances into flat lists. When `retry_exception` is set, wraps
every tool callable with a converter that catches `RetriableError` and re-raises it as the
framework-specific exception (e.g., pydantic-ai's `ModelRetry`).

```python
ToolFactory(
    tool_cards=[tool_a, tool_b],
    observer=agent,
    retry_exception=ModelRetry,
)
```

### BaseToolParam: Configuration, Not Schema

Every custom field on a `BaseToolParam` subclass must be read at factory bind time and influence
the tool's runtime behavior — as a closure variable, function default, or observer setup value.
Fields that merely mirror the LLM-facing function signature are dead code: they look configurable
but have no effect.

**Rule:** if a developer writes `MyParam(field=value)`, that value must influence runtime behavior.
LLM-facing parameters belong exclusively on the factory-produced function signature.

```python
# CORRECT — field captured at bind time, controls behavior
class GetPlanning(BaseToolParam):
    filter_by_agent: bool = True  # read by factory, stored in closure

# CORRECT — no custom fields, search params live on the function signature
class SearchGraph(BaseToolParam):
    expose: set[Channels] = {TOOL_CALL, COMMAND}

# WRONG — fields duplicate function signature but are never read
class BadParam(BaseToolParam):
    status: str | None = None  # never consumed by factory
```

## Channel System

The `Channels` enum (`TOOL_CALL`, `SYSTEM_PROMPT`, `COMMAND`) controls how a capability is
surfaced. Each `BaseToolParam` subclass declares its `expose` set. A single capability can
appear on multiple channels simultaneously.

| Channel | Consumer | Invocation |
|---|---|---|
| `TOOL_CALL` | LLM agent | Called by the LLM during the ReAct loop |
| `SYSTEM_PROMPT` | LLM context | Injected as dynamic content before each LLM call |
| `COMMAND` | Orchestrator / agents | Called programmatically via `proxy_call` |

```python
class GetPlanning(BaseToolParam):
    expose: set[Channels] = {SYSTEM_PROMPT, COMMAND}  # never a direct LLM call

class GetPlanningTask(BaseToolParam):
    expose: set[Channels] = {TOOL_CALL, COMMAND}      # LLM + programmatic
```

`BaseToolParam.instructions` appends runtime guidance to a tool's docstring without modifying
source — useful for injecting team-specific constraints at configuration time.

## Tool Catalog

### WorkspaceTool

Sandboxed read/write access to a shared team filesystem. A single `WorkspaceTool` class covers
both read-only and full access via a `read_only: bool` field.

```python
from akgentic.tool import WorkspaceTool

WorkspaceTool()                          # full access (default)
WorkspaceTool(read_only=True)            # read tools only
WorkspaceTool(workspace_id="shared")     # shared workspace across teams
WorkspaceTool(read_only=True, workspace_glob=False)  # fine-grained capability control
```

| Tool | Description |
|---|---|
| `workspace_read` | Read file with line-number pagination; auto-converts PDF, DOCX, XLSX, images to Markdown |
| `workspace_list` | List directory (flat or ASCII tree by depth) |
| `workspace_glob` | Find files by glob pattern with `{py,ts}` brace expansion; results sorted by mtime |
| `workspace_grep` | Regex search across files; uses `rg` if available, falls back to Python |
| `workspace_view` | View image as `BinaryContent` for LLM vision (PNG, JPG, WebP, GIF, BMP) |
| `workspace_write` | Overwrite or create a file; auto-detects CRLF/LF line endings |
| `workspace_edit` | Surgical find-and-replace with 7-strategy cascade (exact → fuzzy, threshold 0.85) |
| `workspace_multi_edit` | Apply multiple `EditItem` operations across files in one call |
| `workspace_patch` | Apply unified diff patch (GNU format) |
| `workspace_delete` | Delete a file |
| `workspace_mkdir` | Create directory tree (parents included, idempotent) |

The workspace root is resolved from `AKGENTIC_WORKSPACES_ROOT` (default `./workspaces`). All
path operations validate against the root — traversal attacks (`../`) raise `RetriableError`.

**Binary file reading** (requires `akgentic-tool[docs]`): `workspace_read` transparently
handles PDF, DOCX, XLSX, PPTX, and images via MarkItDown. A sidecar cache (`.report.pdf.md`)
avoids re-extraction on subsequent reads.

**Image viewing** (requires `akgentic-tool[vision]`): `workspace_view` delivers raw pixels
to the model's vision endpoint. Images are optionally resized (default `max_dimension=1568`)
with a sidecar cache for the resized version.

### PlanningTool

Shared actor-based task board for multi-agent teams. A singleton `PlanActor` (named
`#PlanningTool`) lives in the orchestrator and persists across all agents' tool calls.

```python
from akgentic.tool.planning import PlanningTool

PlanningTool()                                    # default config
PlanningTool(get_planning=GetPlanning(filter_by_agent=False))  # show all tasks
```

| Capability | Default channel | Description |
|---|---|---|
| `get_planning` | `SYSTEM_PROMPT`, `COMMAND` | Team plan injected into LLM context; scoped to calling agent by default |
| `get_planning_task` | `TOOL_CALL`, `COMMAND` | Look up a single task by integer ID |
| `update_planning` | `TOOL_CALL` | Batch create / update / delete tasks in one call |
| `search_planning` | `TOOL_CALL`, `COMMAND` | Filter tasks by status, owner, creator, or natural-language query |

**Task model constraints:** `description` max 300 chars; `output` max 150 chars (auto-truncated
if exceeded — no `ValidationError`). Constraints are stated explicitly in the tool schema so
LLMs respect them before composing a call.

**Semantic search** (requires `akgentic-tool[vector_search]`): task descriptions are embedded
on create/update. `search_planning(query=...)` runs keyword UNION semantic search (cosine ≥ 0.5,
top_k=20). Degrades gracefully to keyword-only when vector deps are absent.

```python
# System prompt output example (filter_by_agent=True)
"""
**Team planning:** 5 tasks total
Owners: @Alice: 3 | @Bob: 1 | unassigned: 1

**Your tasks** (owner or creator: @Alice):
- ID 3 [started] Implement auth module (Owner: @Alice, Creator: @Alice)
- ID 7 [pending] Review PR #42 — Output: pending (Owner: @Bob, Creator: @Alice)

Use get_planning_task(id) for exact ID lookup or search_planning(...) to filter tasks.
"""
```

### KnowledgeGraphTool

Persistent actor-based knowledge graph for structured entity and relationship storage with
hybrid keyword + semantic search.

```python
from akgentic.tool.knowledge_graph import KnowledgeGraphTool

KnowledgeGraphTool()
```

Exposes `get_graph`, `update_graph`, and `search_graph` capabilities. Entities and relations
are stored in a `KnowledgeGraphActor`. Semantic search uses the shared `VectorIndex`
infrastructure (requires `akgentic-tool[vector_search]`).

### SearchTool

Web search and content fetching via the [Tavily](https://tavily.com/) API.

```python
from akgentic.tool.search import SearchTool

SearchTool()
```

| Tool | Description |
|---|---|
| `web_search` | Tavily search — returns titles, URLs, and snippets |
| `web_fetch` | Fetch and extract clean text from a URL (Tavily extract) |
| `web_crawl` | Crawl a URL and return structured content |

Requires `TAVILY_API_KEY` environment variable.

### TeamTool

Exposes team management capabilities (hire/fire agents, roster view) to the LLM. Used by
`BaseAgent` in `akgentic-agent` to enable orchestrator-level agents to dynamically extend
the team.

```python
from akgentic.tool.team import TeamTool

TeamTool()
```

Requires a `TeamManagementToolObserver` (provided by `BaseAgent`). Surfaces agent roster and
available profiles as a system prompt; `hire_team_member` and `fire_team_member` as tool calls.

### MCPTool

Integrates external [Model Context Protocol](https://modelcontextprotocol.io) servers as native
pydantic-ai toolsets, supporting both HTTP+SSE and stdio transport.

```python
from akgentic.tool.mcp import MCPTool, MCPHTTPConnectionConfig

MCPTool(
    connections=[
        MCPHTTPConnectionConfig(
            url="https://my-mcp-server.example.com/sse",
        )
    ]
)
```

Returns registered MCP tools via `get_toolsets()` — pydantic-ai handles schema resolution and
dispatch. OAuth 2.0 flows are supported for MCP servers requiring authentication (see
`mcp/oauth_handler.py`).

### ExecTool

Sandboxed shell command execution inside the team workspace. A single `SandboxActor` is spawned
per team and reused across all `ExecTool` calls. The backend is selected via the `mode` field.

```python
from akgentic.tool.sandbox.tool import ExecTool

ExecTool()                        # auto mode (default — probe: bwrap → seatbelt → docker → local)
ExecTool(mode="local")            # local mode (subprocess, no filesystem isolation)
ExecTool(mode="bwrap")            # Linux bubblewrap (filesystem namespace isolation)
ExecTool(mode="seatbelt")         # macOS Apple Seatbelt (sandbox-exec profile)
ExecTool(mode="docker")           # persistent Docker container per team
ExecTool(workspace_id="shared")   # share workspace directory with WorkspaceTool
```

**Sandbox modes:**

| Mode | Platform | Isolation | Requirement |
|---|---|---|---|
| `local` | Any | None — subprocess only | No extra tools needed |
| `bwrap` | Linux | Filesystem namespace (bubblewrap) | `bwrap` on PATH |
| `seatbelt` | macOS | Apple Seatbelt profile (`sandbox-exec`) | `sandbox-exec` on PATH |
| `docker` | Any | Persistent container per team | Docker daemon on PATH |
| `auto` | Any | Best available (probe order: bwrap → seatbelt → docker → local) | Automatic |

**Allowed commands** (enforced by `ALLOWED_COMMANDS` allowlist — first token only):

`python`, `python3`, `pytest`, `ruff`, `mypy`, `git`, `uv`, `pip`, `cat`, `ls`, `find`,
`grep`, `mkdir`, `cp`, `mv`, `rm`, `echo`, `touch`, `curl`, `wget`, `make`, `bash`, `sh`,
`node`, `npm`, `npx`

**Auto-mode probe order (`_resolve_auto_mode()`):** When `mode="auto"`, the function probes
the host at `ExecTool.observer()` call time in the following order: `bwrap` (Linux bubblewrap)
→ `seatbelt` (macOS `sandbox-exec`) → `docker` → `local` (fallback, no isolation). If `local`
is selected as the fallback, a `DeprecationWarning` is emitted to alert that no isolation
backend was found.

**Platform notes:**

- **RLIMIT_AS on Darwin:** The `local` mode sets `RLIMIT_AS` (virtual address space limit) to
  512 MB on Linux but skips this resource limit on macOS/Darwin, where `RLIMIT_AS` is not
  reliably enforceable. CPU time and file size limits are applied on all platforms.
- **Seatbelt DeprecationWarning:** `SeatbeltSandboxActor._start_sandbox()` emits a
  `DeprecationWarning` because `sandbox-exec` is deprecated since macOS 10.15 Catalina and
  may be removed in a future macOS release. The seatbelt mode is intended for macOS developer
  workstations only.

**Error handling:** All errors from the sandbox backend surface as a `SandboxError` string
returned to the LLM (never raised). Disallowed commands return a `CommandNotAllowedError`
string listing the allowed commands.

```python
# Example tool response for a disallowed command:
# "CommandNotAllowedError: Command 'curl' is not in the allowed commands list.
#  Allowed: ['bash', 'cat', 'cp', ...]"

# Example tool response for a backend failure:
# "SandboxError: TimeoutExpired: Command 'python main.py' timed out after 30s"
```

**`SANDBOX_ACTOR_CLASSES` registry:** The backend registry is a mutable `dict[str, type[SandboxActor]]`
exposed at `akgentic.tool.sandbox.tool.SANDBOX_ACTOR_CLASSES`. Infrastructure packages (e.g.,
`akgentic-infra`) can inject additional backends at import time before any `ExecTool` is
constructed:

```python
from akgentic.tool.sandbox.tool import SANDBOX_ACTOR_CLASSES
from my_infra.e2b_actor import E2BSandboxActor

SANDBOX_ACTOR_CLASSES["e2b"] = E2BSandboxActor  # now available as ExecTool(mode="e2b")
```

## Error Handling

`RetriableError` (defined in `akgentic.tool.errors`) is the single signal for recoverable
failures. Tools raise it with a clear, actionable message. `ToolFactory` translates it to
the framework-specific retry exception (e.g., pydantic-ai `ModelRetry`) via injection —
tool logic stays framework-agnostic.

```python
from akgentic.tool.errors import RetriableError

def my_tool(path: str) -> str:
    """Read a file."""
    try:
        return backend.read(path)
    except FileNotFoundError:
        raise RetriableError(f"File not found: {path}")
    except PermissionError:
        raise RetriableError("Path escapes workspace root — use a relative path")
```

**Rule:** no raw Python exception should escape a tool callable. An unhandled exception
produces no tool response and stalls the agent's ReAct loop.

| Exception | Treatment |
|---|---|
| `FileNotFoundError` | Wrap as `RetriableError("File not found: {path}")` |
| `PermissionError` (path escape) | Wrap as `RetriableError("Path escapes workspace root ...")` |
| `re.error` (bad regex) | Wrap as `RetriableError("Invalid regex pattern: {error}")` |
| `RuntimeError` (uninitialised state) | Let propagate — programming error, not an LLM error |

## Optional Extras

| Extra | Packages | Enables |
|---|---|---|
| `vector_search` | `numpy`, `openai` | Semantic search in `PlanningTool` and `KnowledgeGraphTool` |
| `docs` | `markitdown[pdf,docx,xlsx,xls,pptx,outlook]` | Binary file reading in `workspace_read` |
| `vision` | `Pillow>=10.0` | Image resizing + sidecar cache in `workspace_view` |

All extras degrade gracefully when absent: planning falls back to keyword-only search,
workspace binary reads raise `ValueError` with an install hint, image resizing is skipped
with a one-time warning.

## Development

### Prerequisites

- Python 3.12+
- [uv](https://docs.astral.sh/uv/) package manager

### Setup

```bash
uv sync --all-extras
```

### Commands

```bash
# Run tests
uv run pytest packages/akgentic-tool/tests/

# Run tests with coverage
uv run pytest packages/akgentic-tool/tests/ --cov=akgentic.tool --cov-fail-under=80

# Lint
uv run ruff check packages/akgentic-tool/src/

# Format
uv run ruff format packages/akgentic-tool/src/

# Type check
uv run mypy packages/akgentic-tool/src/
```

### CI Pipeline

Every pull request runs the full quality gate via GitHub Actions
(`.github/workflows/ci.yml`):

| Step | Command | Gate |
|---|---|---|
| Type check | `mypy packages/akgentic-tool/src/` (strict, Python 3.12) | Zero errors |
| Lint | `ruff check packages/akgentic-tool/src/` | Zero errors |
| Tests | `pytest packages/akgentic-tool/tests/ --cov=akgentic.tool --cov-fail-under=80` | All pass, ≥ 80% coverage |

The CI badge at the top of this README reflects the current state of `master`. PRs are
blocked from merging until all four steps are green.

### Project Structure

```
src/akgentic/tool/
    __init__.py               # Public API
    core.py                   # ToolCard, BaseToolParam, ToolFactory, Channels
    errors.py                 # RetriableError
    event.py                  # ToolObserver, ActorToolObserver,
    │                         #   TeamManagementToolObserver
    vector.py                 # VectorEntry, EmbeddingService, VectorIndex
    │                         #   [optional: vector_search extra]
    planning/
    │   planning_actor.py     # Task models, PlanConfig, PlanActor
    │   └── planning.py       # PlanningTool ToolCard
    knowledge_graph/
    │   models.py             # Entity, Relation, CRUD + query models
    │   kg_actor.py           # KnowledgeGraphActor
    │   └── kg_tool.py        # KnowledgeGraphTool ToolCard
    search/
    │   └── search.py         # SearchTool (Tavily)
    team/
    │   └── team.py           # TeamTool
    mcp/
    │   mcp.py                # MCPTool, connection configs
    │   └── oauth_handler.py  # OAuth 2.0 flow
    workspace/
        workspace.py          # Workspace Protocol, Filesystem, get_workspace()
        edit.py               # EditMatcher (7-strategy), FilePatch, parse_patch
        readers.py            # DocumentReader (Pydantic BaseModel), TEXT_EXTENSIONS
        └── tool.py           # WorkspaceTool ToolCard
    sandbox/
        __init__.py           # Public exports: ExecTool, SandboxActor subclasses, models
        actor.py              # SandboxActor (abstract), SandboxConfig, ALLOWED_COMMANDS
        local.py              # LocalSandboxActor (subprocess, resource limits)
        docker.py             # DockerSandboxActor (persistent container per team)
        seatbelt.py           # SeatbeltSandboxActor (macOS Apple Seatbelt)
        bwrap.py              # BwrapSandboxActor (Linux bubblewrap)
        tool.py               # ExecTool ToolCard, SANDBOX_ACTOR_CLASSES registry
        └── Dockerfile        # Bundled image definition for akgentic-sandbox:latest
tests/                        # Tests organised by domain
```

## License

See the repository root for license information.
