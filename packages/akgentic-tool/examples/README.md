# akgentic-tool Examples

Progressive examples demonstrating the akgentic-tool module capabilities.
Each example is a self-contained script paired with a companion `.md` file
explaining concepts.

## Running the Examples

From the **project root**:

```bash
uv run python packages/akgentic-tool/examples/knowledge_agent.py
```

From the **akgentic-tool directory**:

```bash
cd packages/akgentic-tool
uv run python examples/knowledge_agent.py
```

## Available Examples

### [knowledge_agent.py](knowledge_agent.py) — Knowledge Graph Tool End-to-End

Demonstrates end-to-end usage of `KnowledgeGraphTool` in a realistic
software tech stack scenario. Covers entity and relation creation,
full graph and subgraph BFS queries, root-entity filtering, keyword
and hybrid/vector search, and compact system prompt injection for LLM agents.

Companion docs: [knowledge_agent.md](knowledge_agent.md)

### [planning_agent.py](planning_agent.py) — Planning Tool with Semantic Search

Demonstrates end-to-end usage of `PlanningTool` with a sprint backlog
scenario. Covers task creation via `UpdatePlan`, exact-ID lookup via
`get_planning_task(int)`, semantic query via `get_planning_task(str)`,
and the agent-scoped planning system prompt with `filter_by_agent`.

Companion docs: [planning_agent.md](planning_agent.md)

## Prerequisites

All examples require:

- Python 3.12+
- `akgentic-core`
- `akgentic-tool`

Install with:

```bash
uv sync --all-packages --all-extras
```

The `[vector_search]` extra installs `openai` and `numpy` required by
embedding-based search. Keyword search, exact-ID lookup, and basic
operations work without an OpenAI API key; only semantic/hybrid search
requires a valid `OPENAI_API_KEY`.
