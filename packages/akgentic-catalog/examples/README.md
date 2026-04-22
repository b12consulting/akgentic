# akgentic-catalog Examples

Progressive examples demonstrating the akgentic-catalog module capabilities.
Each example is a self-contained script paired with a companion `.md` file
explaining concepts.

## Running the Examples

From the **project root**:

```bash
uv run python packages/akgentic-catalog/examples/01_catalog_entries.py
```

From the **akgentic-catalog directory**:

```bash
cd packages/akgentic-catalog
uv run python examples/01_catalog_entries.py
```

## Available Examples

### [01_catalog_entries.py](01_catalog_entries.py) — Template & Tool Entry Basics

First contact with the catalog API: create, retrieve, and list `TemplateEntry`
and `ToolEntry` — the two leaf entity types with no cross-catalog dependencies.
Demonstrates placeholder auto-parsing, FQCN class resolution, and duplicate-ID
error handling.

Companion docs: [01-catalog-entries.md](01-catalog-entries.md)

### [02_agent_entries.py](02_agent_entries.py) — Agent Entries & Cross-Validation

How `AgentCatalog` validates cross-catalog references (`tool_ids`,
`@`-template, `routes_to`) at registration time, and how `resolve_tools()` /
`resolve_template()` bridge catalog-form to runtime objects.

Companion docs: [02-agent-entries.md](02-agent-entries.md)

### [03_team_entries.py](03_team_entries.py) — Team Composition, Member Trees & Profiles

All four catalogs wired together. `TeamEntry` with nested `TeamMemberSpec`
hierarchies, `entry_point` validation, `headcount`, members vs profiles,
`message_types` FQCN validation, and recursive `TeamQuery` search.

Companion docs: [03-team-entries.md](03-team-entries.md)

### [04_yaml_persistence.py](04_yaml_persistence.py) — YAML Repository Round-Trip

YAML persistence layer mechanics: write-to-disk / read-back round-trip with
full type fidelity for all four entity types, file layout inspection, lazy
caching and `reload()`, multi-entry YAML files, and duplicate ID detection
across files. Uses repositories directly (not service-layer catalogs).

Companion docs: [04-yaml-persistence.md](04-yaml-persistence.md)

### [05_catalog_wiring.py](05_catalog_wiring.py) — Full Catalog Wiring, Delete Protection & Env Vars

Service-layer catalogs with bidirectional wiring: construction-time upstream
refs for create/update validation, post-construction downstream back-refs for
delete protection across every boundary (tool→agent, template→agent,
agent→team, agent→agent routes_to). Also covers correct deletion order,
update re-validation, `EntryNotFoundError`, and `resolve_env_vars()` for
`${VAR}` substitution.

Companion docs: [05-catalog-wiring.md](05-catalog-wiring.md)

### [06_search_and_query.py](06_search_and_query.py) — Compound Queries & Cross-Catalog Search

All four query models (`TemplateQuery`, `ToolQuery`, `AgentQuery`,
`TeamQuery`) with field-specific match semantics (exact, substring,
membership, set overlap, recursive tree walk), AND composition, cross-catalog
search chaining (agents by skill → teams containing those agents), and empty
result handling.

Companion docs: [06-search-and-query.md](06-search-and-query.md)

### [07_python_first.py](07_python_first.py) — Python-First Workflows

The D10 Python-first principle in action: build and register every catalog
entry type entirely in Python using Pydantic constructors — no YAML files
authored.  Covers three workflows: prototyping (create/update cycle), testing
(fixture-style with temp directories), and programmatic generation (loop
over a roles list to build agents dynamically).

Companion docs: [07-python-first.md](07-python-first.md)

### [08_custom_types.py](08_custom_types.py) — Custom Types & FQCN Round-Trip

Build real custom types — a ToolCard with callable tools, a custom AgentConfig,
a custom message type, and an Akgent subclass — then register them in the
catalog and prove FQCN resolution preserves full type fidelity.  Covers all
four ToolCard method contracts, `_extract_config_type()` MRO walk,
`resolve_message_types()` FQCN validation, and ToolFactory aggregation.

Companion docs: [08-custom-types.md](08-custom-types.md)

## Prerequisites

All examples require:

- Python 3.12+
- `akgentic` (akgentic-core)
- `akgentic-agent`
- `akgentic-llm`
- `akgentic-tool`

Install with:

```bash
uv sync
```
