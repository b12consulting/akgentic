# Planning Tool — Semantic Search Example

Demonstrates end-to-end usage of `PlanningTool` with a realistic sprint
backlog scenario, covering task creation, exact-ID lookup, semantic search,
and the agent-scoped planning system prompt.

## What It Demonstrates

- **Planning tool configuration** — wiring `PlanningTool` with `GetPlanning`,
  `GetPlanningTask`, and `UpdatePlanning` via a minimal observer pattern
- **Task lifecycle** — creating sprint tasks with `UpdatePlan` / `TaskCreate`,
  including statuses, owners, and dependencies
- **Exact-ID lookup** — `get_planning_task(int)` returns a typed `Task` object
  by integer ID
- **Semantic query** — `get_planning_task(str)` dispatches to embedding-based
  search (e.g., `"login security"` finds the authentication middleware task)
- **Agent-scoped system prompt** — `GetPlanning(filter_by_agent=True)` produces
  a compact summary showing team totals plus only the calling agent's own tasks

## Key Concepts

### Exact-ID vs Semantic Dispatch

`get_planning_task` accepts `int | str`:

- **`int`** — direct ID match against the task list, always available
- **`str`** — embeds the query and performs cosine similarity search against
  task description embeddings, requires an embedding provider (OpenAI)

### `filter_by_agent` in `GetPlanning`

When `filter_by_agent=True` (the default), the system prompt includes:

1. A **team summary** — total task count and per-owner breakdown (always shown)
2. **Your tasks** — only tasks where the calling agent is owner or creator

This keeps the prompt proportional to `O(own_tasks)` instead of `O(all_tasks)`,
which matters in large multi-agent plans.

When `filter_by_agent=False`, all tasks are listed with owner and creator labels.

### Graceful Degradation

Semantic search requires the `[vector_search]` optional dependency and a valid
`OPENAI_API_KEY`. Without either:

- Task CRUD works normally
- Exact-ID lookup works normally
- Semantic search returns `"Semantic search unavailable."` instead of crashing
- Embedding failures during task creation are logged and swallowed

## Running the Example

**Keyword mode** (no API key required):

```bash
uv run python packages/akgentic-tool/examples/planning_agent.py
```

**Semantic mode** (requires OpenAI API key):

```bash
OPENAI_API_KEY=sk-... uv run python packages/akgentic-tool/examples/planning_agent.py
```

From the **akgentic-tool directory**:

```bash
cd packages/akgentic-tool
uv run python examples/planning_agent.py
```
