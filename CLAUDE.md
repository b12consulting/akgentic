# CLAUDE.md

## Project Overview

Akgentic is a Python 3.12+ actor-based multi-agent framework. This root repo (`akgentic-quick-start`) is a UV workspace that aggregates seven packages via **git submodules**. Each package lives in its own repo under `packages/`.

## Golden Rules (MANDATORY)

These rules are non-negotiable. Every implementing agent MUST follow them.

### 1. Always Pydantic models — NEVER `dict[str, Any]`

`dict[str, Any]` is a bug factory for agents. Always define a Pydantic model for:
- Messages and events
- Agent configuration and capabilities
- Any structured data crossing a boundary (agent→agent, orchestrator→agent)

```python
# CORRECT — typed, validated, discoverable
class AgentCard(SerializableBaseModel):
    role: str
    description: str
    skills: list[str]
    agent_class: str | type
    config: BaseConfig
    routes_to: list[str] = []

# WRONG — agents will misuse keys, miss fields, introduce silent bugs
def create_agent(card: dict[str, Any]) -> dict[str, Any]: ...
```

### 1b. `ToolCard` and `BaseToolParam` MUST be fully Pydantic-serializable

`ToolCard` subclasses are configuration models — they must round-trip through Pydantic serialization cleanly. This means:

- **NEVER use `ConfigDict(arbitrary_types_allowed=True)`** on a `ToolCard` or `BaseToolParam` subclass — it breaks serialization and is a sign that a non-serializable type is leaking into the model's fields.
- All Pydantic **fields** must use serializable types (primitives, other `BaseModel` subclasses, enums, `list`/`dict` of the above).
- **Runtime state** (actor proxies, file-system handles, open connections) MUST be stored as `PrivateAttr` — excluded from serialization by design.

```python
# CORRECT — serializable fields, runtime state in PrivateAttr
class WorkspaceReadTool(ToolCard):
    workspace_read: WorkspaceRead | bool = True
    _workspace: Filesystem | None = PrivateAttr(default=None)  # runtime only

# WRONG — leaks a non-serializable type into a Pydantic field
class WorkspaceReadTool(ToolCard):
    model_config = ConfigDict(arbitrary_types_allowed=True)
    _workspace: Filesystem | None = None  # breaks serialization
```

### 2. Never use bash for inline verification or file search

Do NOT use `python3 -c "..."` scripts or `grep`/`rg` via Bash to verify implementation or search files.
These patterns are not prescribed by any BMAD workflow step (violates Golden Rule #3) and interrupt the user with unnecessary confirmation prompts.

Instead:
- Use the `Grep` tool to search file contents
- Use the `Read` tool to inspect files directly
- Use `pytest` to verify correctness via tests
- Reason about correctness from code you have already read — no shell evaluation needed

### 2b. Bash command patterns that trigger user validation

The following patterns in Bash commands trigger a manual user approval prompt. **Avoid them unconditionally:**

- **`$(...)` (command substitution)** — Never use subshell expansion in Bash commands. Compute values beforehand and inline them directly.
- **Quoted newlines followed by `#`-prefixed lines** — A multi-line string containing `\n#...` is flagged as potentially hiding arguments from permission checks. Keep multi-line strings free of `#`-prefixed lines, or split the command into separate steps.

### 2c. Spawned agents must load their BMAD persona

When spawning a subagent (via the Agent tool) that performs a BMAD role (SM, Dev, Code Reviewer, etc.), the spawn prompt **MUST** instruct the agent to read and load its full persona file from `{project-root}/_bmad/bmm/agents/<agent>.md` before executing any workflow. Without the persona, the agent lacks its review checklist, communication style, and workflow-specific rules.

Available personas:
- `sm.md` — Bob (Scrum Master / Story Creator)
- `dev.md` — Amelia (Developer)
- `dev-reviewer.md` — Rex (Code Reviewer)
- `architect.md` — Winston (Architect)
- `pm.md` — John (Product Manager)

### 3. Follow BMAD workflow steps exactly

**NEVER create your own todo list when executing a BMAD workflow.**

When running any BMAD workflow (dev-story, code-review, create-story, etc.):

1. **ALWAYS load and follow the workflow's `instructions.xml`** from the workflow directory — Execute steps in EXACT order
2. **NEVER skip workflow steps** — Even if you think they're not needed
3. **NEVER substitute your own task list** — The workflow steps ARE the task list
4. **NEVER make "judgment calls" to defer steps** — If a step says MANDATORY, do it

### 4. Never modify code outside the active submodule

Each story targets ONE submodule. Agents MUST NOT edit, commit, or create branches in other submodules, even if the architecture lists cross-submodule prerequisites. If a story requires changes in another submodule:

- **Document the dependency** in the story file as a blocker
- **HALT and tell the user**: "This story requires changes in `{other-submodule}` first — create a separate story/branch/PR for it"
- **Never silently make the changes** — each submodule is a separate repo with its own branch/PR/CI workflow

This applies to all agents: create-story must not include tasks for other submodules, dev must not edit files outside `packages/{submodule}/`, and code review must flag any cross-submodule changes as a violation.

### 5. CI must pass before marking a story as review or done

If the submodule has a CI pipeline (GitHub Actions), the dev agent MUST trigger and verify CI passes before marking a story as `review`. The code review agent MUST verify CI is green before marking `done` and creating a PR.

- Run `gh pr checks` or equivalent to verify CI status
- If CI fails, fix the issues before proceeding
- Never mark a story complete with failing CI

### 6. ALL tests must pass — no exceptions, no excuses

A story is NOT done until every single test in the submodule passes. There is no such thing as "this test failure is unrelated to my change."

- If a test fails, **you own it** — fix it or identify the root cause before proceeding
- NEVER dismiss a test failure as pre-existing, flaky, or out of scope
- NEVER say "this test was already broken before my change" — if it fails on your branch, it's your problem
- Run the full test suite (`pytest packages/<submodule>/tests/`), not just the tests you wrote
- If fixing a pre-existing failure requires changes outside your submodule, **HALT and tell the user** — but do NOT skip the failure

### 7. A story is not done until CI is green

This is the definitive gate — not your local test run, not your judgment. The GitHub Actions CI pipeline is the single source of truth for story completion.

- After pushing your branch, **wait for CI to finish and verify it passes**
- If CI fails for ANY reason (tests, lint, type checks, coverage), the story is NOT done
- Fix the failure, push again, and re-verify — repeat until CI is fully green
- NEVER mark a story as `review` or `done` with red CI
- NEVER create a PR with failing CI checks

## Repository Structure

```
packages/
  akgentic-core/       → Actor framework (Pykka), messaging, orchestrator, AgentCard
  akgentic-llm/        → LLM integration (pydantic-ai): REACT pattern, multi-provider, context mgmt
  akgentic-tool/       → Tool abstractions: ToolCard, ToolFactory, Tavily search integration
  akgentic-agent/      → Collaborative agent patterns: BaseAgent, StructuredOutput routing, HumanProxy
  akgentic-catalog/    → Configuration registry: declarative CRUD catalogs for templates, tools, agents, teams
  akgentic-team/       → Team lifecycle management: create/resume/stop/delete, event sourcing
  akgentic-infra/      → Infrastructure backend plugins: Redis, persistence adapters (planned)
src/
  agent_team.py    → Main demo: interactive multi-agent team with @mention routing
```

All packages share a namespace: `akgentic.core`, `akgentic.llm`, `akgentic.tool`, `akgentic.agent`, `akgentic.catalog`, `akgentic.team`, `akgentic.infra`. Cross-package namespace discovery uses `pkgutil.extend_path`.

## Architecture

**Dependency graph** (lower layers have no upward dependencies):

```
akgentic-infra   ──depends on──▶  ALL packages (deployment project, not a library)
akgentic-catalog ──depends on──▶  akgentic-core + akgentic-llm + akgentic-tool + akgentic-team
akgentic-team    ──depends on──▶  akgentic-core
akgentic-agent   ──depends on──▶  akgentic-core + akgentic-llm + akgentic-tool
akgentic-tool    ──depends on──▶  akgentic-core + (pydantic, pydantic-ai, tavily-python, httpx)
akgentic-llm     ──depends on──▶  (pydantic-ai, httpx, tenacity)
akgentic-core    ──depends on──▶  (pydantic, pykka)  ← zero infrastructure deps
```

**Module boundary rules:**
- `akgentic-core` MUST NOT import from llm, tool, catalog, agent, team, or infra
- `akgentic-llm` MUST NOT import from core, tool, catalog, agent, team, or infra
- `akgentic-tool` MAY import from core only
- `akgentic-team` MAY import from core only
- `akgentic-agent` MAY import from core, llm, and tool ONLY — MUST NOT import from team, catalog, or infra
- `akgentic-catalog` MAY import from core, llm, tool, and team
- `akgentic-infra` is a deployment project, not a library — it MAY import from ALL packages (core, llm, tool, agent, team, catalog) to wire them together

**Actor internals boundary rule:**
- Outside `akgentic-core`, code MUST interact with actors through the **public proxy API** only (`Akgent.createActor()`, `ActorSystem.proxy_ask()`, `ActorSystem.proxy_tell()`).
- NEVER cast through `ActorAddressImpl._actor_ref._actor` to access the raw Pykka actor or its private attributes (`_orchestrator`, `_user_id`, `_children`, etc.).
- NEVER call `agent_class.start()` directly — that is a Pykka internal. Use `createActor()` which handles context propagation (user_id, team_id, orchestrator, parent, children tracking) and orchestrator notification.
- This rule applies to all packages except `akgentic-core` itself, which owns the actor implementation.

**Key patterns:**
- **Actor model**: All agents are Pykka actors communicating via async messages. Agent base class is `Akgent` (core) or `BaseAgent` (agent, with LLM integration).
- **Orchestrator**: Central coordinator that tracks team state, routes events to `EventSubscriber`s, and manages agent lifecycle.
- **AgentCard**: Declarative role definitions (role, skills, config, `routes_to`) registered with the orchestrator. Agents are hired/fired dynamically at runtime.
- **StructuredOutput routing**: Agents produce `StructuredOutput` with `list[Request]` to route messages to other agents. Routing is handled in `BaseAgent.process_message()`.
- **Message handler convention**: Methods named `receiveMsg_<MessageType>` on `Akgent` subclasses (e.g., `receiveMsg_UserMessage`). The `N802` ruff rule is suppressed for this pattern.

## Git Submodules

Each package under `packages/` is a separate git submodule pointing to `git@github.com:b12consulting/<name>.git`. When making changes to a submodule, commits must be made inside that submodule directory, then the parent repo updated to point to the new commit.

- **Never use `git add .` or `git add -A` in the root repo** — This can accidentally update submodule pointers. Always stage files by name.
- **Never use `cd <path> && <command>`** — Compound `cd && ...` commands bypass the allowlist. Use path flags instead: `git -C <path>` for git, `gh ... -R <owner/repo>` for GitHub CLI. All these commands must run from the workspace root.

## Common Commands

```bash
# Install all packages (after git submodule update --init --recursive)
uv sync --all-packages --all-extras

# Run all tests
pytest

# Run tests for a single package
pytest packages/akgentic-core/tests/
pytest packages/akgentic-llm/tests/

# Run a single test file
pytest packages/akgentic-core/tests/test_public_api.py

# Run a single test
pytest packages/akgentic-core/tests/test_public_api.py::test_name -v

# Type checking (per-package, strict mode)
mypy packages/akgentic-core/src/
mypy packages/akgentic-llm/src/

# Lint
ruff check packages/akgentic-core/src/

# Format
ruff format packages/akgentic-core/src/

# Run the demo
python src/agent_team.py
```

## Coding Conventions

- **Python >= 3.12** with type hints
- **Line length**: 100 characters
- **Method length**: Maximum ~50 lines per method. If a method exceeds this, extract private helpers. Comment-delimited "phases" inside a method are a sign extraction is overdue. Public methods orchestrate; private methods implement.
- **Ruff rules**: E, F, I, N, UP, ANN, ASYNC, C901, PLR0912, PLR0915. `ANN401` (Any) and `N802` (receiveMsg_ pattern) are ignored. Complexity rules enforce method length at CI time.
- **mypy**: Strict mode, Python 3.12 target
- **Naming**: `snake_case` functions/modules, `PascalCase` classes, `UPPER_CASE` constants
- **Imports**: Group per isort sections (stdlib, third-party, first-party, local)
- **pytest**: `asyncio_mode = "auto"`, test paths are `packages/*/tests`
- **Coverage**: 80% minimum, branch coverage enabled, enforced via `--cov-fail-under=80`
- **Build backend**: Hatchling for all packages

## Commit & PR Standards

- **Every branch MUST be linked to a GitHub issue** — No branch without an associated issue.
- **Branch naming convention**: `<type>/<issue-number>-<short-description>`
  - `type`: one of `feat`, `fix`, `docs`, `refactor`, `chore`, `test`
  - `issue-number`: the GitHub issue number
  - `short-description`: kebab-case summary (2-4 words)
  - Examples: `feat/42-actor-lifecycle`, `fix/17-routing-deadlock`, `docs/31-update-readme`
- **Never push directly to `master`** — Always work on a feature branch and open a PR.
- **Sign commits**: `git commit -s` (DCO requirement)
- **Conventional Commits**: `feat:`, `fix:`, `docs:`, `refactor:`, `chore:`
- **One commit, one responsibility** — Each commit must contain a single type of change. Never mix concerns (e.g., a `feat:` commit must not include unrelated refactoring, formatting, or bug fixes). Split into separate commits if needed.
- **Link issues**: `Relates to #123` in commits
- **Require green lint and tests before PR**
- **Never mention AI assistants in PRs/diffs**
- **Do not include test plans or effort estimates in PRs**
- **Always update sprint-status with issue and PR numbers** — When a GitHub issue or PR is created for a story, immediately update the corresponding entry in `sprint-status.yaml` with `# issue #N, pr #M`. This applies to all agents: the dev agent must add the annotation after creating the issue/PR, and the code reviewer must verify the annotation exists before marking a story as done.
