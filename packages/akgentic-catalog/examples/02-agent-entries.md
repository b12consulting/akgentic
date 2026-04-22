# Example 02 — Agent Entries & Cross-Validation

Learn how `AgentCatalog` validates cross-catalog references at registration
time, and how `resolve_tools()` / `resolve_template()` bridge catalog-form
configuration to runtime objects.

## Concepts Covered

### AgentCatalog wiring to ToolCatalog and TemplateCatalog

Unlike leaf catalogs (`TemplateCatalog`, `ToolCatalog`), `AgentCatalog` requires
upstream catalogs as constructor dependencies.  This *construction-time wiring*
enables cross-validation on every `create()` call:

```python
agent_catalog = AgentCatalog(
    repository=agent_repo,
    template_catalog=template_catalog,
    tool_catalog=tool_catalog,
)
```

Without this wiring, the catalog cannot verify that an agent's `tool_ids`,
`@`-template references, or `routes_to` targets actually exist.

### AgentEntry with tool_ids, @-template reference, and routes_to

`AgentEntry` carries three kinds of cross-catalog references:

- **`tool_ids`** — A list of `ToolEntry.id` strings referencing tools in the
  `ToolCatalog`.  These are catalog IDs, not `ToolCard` instances.
- **`card.config.prompt.template`** — When prefixed with `@`, this is a catalog
  reference to a `TemplateEntry.id` in the `TemplateCatalog`.
- **`card.routes_to`** — A list of agent routing names (`config.name` values)
  for agents already registered in the `AgentCatalog`.

All three are validated during `create()` — if any reference doesn't resolve,
`CatalogValidationError` is raised before the entry is persisted.

### agent_class FQCN resolution

The `agent_class` field on `AgentCard` stores the fully qualified Python class
name as a string.  For the framework's built-in agent:

```python
agent_class="akgentic.agent.BaseAgent"
```

This FQCN is validated during entry construction to ensure the class is
importable.

### Cross-catalog validation errors

`AgentCatalog.create()` checks all cross-references before persisting:

| Reference type | What is checked | Error on failure |
|---|---|---|
| `tool_ids` | Each ID exists in `ToolCatalog` | `"Tool 'x' not found in ToolCatalog"` |
| `@template` | Template ID exists in `TemplateCatalog` | `"Template '@x' not found in TemplateCatalog"` |
| `routes_to` | Each name matches an agent's `config.name` | `"Route target '@x' not found in AgentCatalog"` |

### resolve_tools() and resolve_template()

These methods on `AgentEntry` bridge catalog-form to runtime-form:

```python
entry = agent_catalog.get("researcher")
tools = entry.resolve_tools(tool_catalog)           # -> list[ToolCard]
prompt = entry.resolve_template(template_catalog)   # -> PromptTemplate | None
```

- `resolve_tools()` looks up each `tool_ids` entry and returns hydrated
  `ToolCard` instances.
- `resolve_template()` resolves `@`-prefixed template references, returning a
  `PromptTemplate` with the actual template string and the original params.

## Key API Patterns

### AgentCatalog construction with upstream catalogs

```python
from akgentic.catalog import (
    AgentCatalog, TemplateCatalog, ToolCatalog,
    YamlAgentCatalogRepository, YamlTemplateCatalogRepository,
    YamlToolCatalogRepository,
)

template_catalog = TemplateCatalog(repository=template_repo)
tool_catalog = ToolCatalog(repository=tool_repo)
agent_catalog = AgentCatalog(
    repository=agent_repo,
    template_catalog=template_catalog,
    tool_catalog=tool_catalog,
)
```

### Cross-validation on create

When you call `agent_catalog.create(entry)`, the service validates all
cross-references *before* persisting.  This means prerequisite entries (templates
and tools) must exist in their respective catalogs first.

### resolve_tools / resolve_template usage

After retrieving an `AgentEntry`, call these methods to get runtime objects:

```python
entry = agent_catalog.get("researcher")

# Catalog-form: tool_ids=["web-search"] (string IDs)
# Runtime-form: list[ToolCard] (hydrated Pydantic models)
tools = entry.resolve_tools(tool_catalog)

# Catalog-form: prompt.template="@research-prompt" (catalog reference)
# Runtime-form: PromptTemplate with resolved template string
prompt = entry.resolve_template(template_catalog)
```

## Common Pitfalls

- **`@`-reference applies only to `prompt.template`, not `config.name`.**
  `"@Researcher"` in `config.name` is an agent routing name.
  `"@research-prompt"` in `prompt.template` is a catalog reference to a
  `TemplateEntry`.  These are two unrelated uses of `@`.

- **`tool_ids` are catalog IDs, not `ToolCard` instances.**  You store string
  IDs like `["web-search"]` in the entry.  Use `resolve_tools()` to get the
  actual `ToolCard` objects at runtime.

- **`routes_to` uses agent names, not catalog IDs.**
  `routes_to=["@Reviewer"]` references `config.name="@Reviewer"`, not
  `AgentEntry.id="reviewer"`.  The `@` prefix is part of the routing name
  convention.

- **Import `AgentConfig` from `akgentic.agent.config`, not `akgentic.core`.**
  The core package has a `AgentConfig` alias that only exposes `name`, `role`,
  `squad_id`.  The agent package version adds `prompt`, `model_cfg`, and
  `tools` fields.  Using the wrong import silently drops fields.

## Related Examples

- **[Example 01 — Template & Tool Entry Basics](01-catalog-entries.md):**
  Covers `TemplateEntry` and `ToolEntry` — the leaf entries that agents
  reference.
- **[Example 03 — Team Composition, Member Trees & Profiles](03-team-entries.md):**
  `TeamEntry` with nested `TeamMemberSpec`, entry_point validation, and
  recursive `TeamQuery` search.
