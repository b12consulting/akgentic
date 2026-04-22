# Knowledge Graph Tool — End-to-End Example

Demonstrates end-to-end usage of `KnowledgeGraphTool` in a realistic
software tech stack scenario, covering graph construction, queries,
search, and system prompt injection.

## What It Demonstrates

- **KnowledgeGraphTool configuration** — wiring with `GetGraph`,
  `update_graph`, and `search` options via a minimal observer pattern
- **Graph construction** — creating entities and relations with
  `ManageGraph`, `EntityCreate`, and `RelationCreate`
- **Graph queries** — full graph retrieval, subgraph BFS from a starting
  entity, and root-entity filtering (`roots_only=True`)
- **Keyword search** — always available, no API key required
- **Hybrid/vector search** — semantic search when `OPENAI_API_KEY` is set,
  with graceful fallback to keyword mode when unavailable
- **System prompt injection** — compact graph context summary for LLM agents

## Key Concepts

### Search Modes

- **keyword** — substring matching against entity names and descriptions
- **hybrid** — combines keyword results with embedding-based cosine
  similarity; falls back to keyword-only without an OpenAI API key

### Root Entities

Entities marked `is_root=True` serve as primary entry points in the graph.
The `roots_only=True` query filter and system prompt summary highlight these.

### Graceful Degradation

Without `OPENAI_API_KEY`, hybrid search automatically falls back to keyword
mode. The example demonstrates both paths explicitly.

## Running the Example

**Keyword-only mode** (no API key required):

```bash
uv run python packages/akgentic-tool/examples/knowledge_agent.py
```

**With hybrid/vector search** (requires OpenAI API key):

```bash
OPENAI_API_KEY=sk-... uv run python packages/akgentic-tool/examples/knowledge_agent.py
```

From the **akgentic-tool directory**:

```bash
cd packages/akgentic-tool
uv run python examples/knowledge_agent.py
```
