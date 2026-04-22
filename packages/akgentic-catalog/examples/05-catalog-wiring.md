# Example 05 — Full Catalog Wiring, Delete Protection & Env Vars

## Concepts Covered

### Bidirectional Catalog Wiring

Catalog services use a two-phase wiring pattern. **Phase 1 (construction-time
upstream refs)** lets `AgentCatalog` validate that `tool_ids`, `@template`
references, and `routes_to` targets exist when you call `create()` or
`update()`. **Phase 2 (post-construction downstream back-refs)** lets
`TemplateCatalog`, `ToolCatalog`, and `AgentCatalog` check whether any
downstream consumer still depends on an entry before allowing `delete()`.

Without Phase 2, delete protection is silently disabled — catalogs allow
deletes without checking downstream dependencies. This is by design for
simpler use cases (e.g. tests) where full wiring isn't needed.

### Delete Protection Across Every Boundary

Four boundaries are protected:

- **Tool → Agent:** `ToolCatalog.validate_delete()` checks if any agent has
  the tool in `tool_ids`.
- **Template → Agent:** `TemplateCatalog.validate_delete()` checks if any
  agent's `config.prompt.template` is an `@`-reference resolving to this
  template ID.
- **Agent → Team (members):** `AgentCatalog.validate_delete()` checks if
  any team references the agent in its member tree (recursive walk).
- **Agent → Agent (routes_to):** `AgentCatalog.validate_delete()` checks
  if any other agent's `routes_to` contains this agent's `config.name`.

Note that `routes_to` uses **agent names** (e.g. `"@Reviewer"`) not catalog
entry IDs (e.g. `"reviewer"`). The delete check resolves the agent being
deleted to its `config.name` and searches other agents' `routes_to` lists.

### Correct Deletion Order

Entries must be deleted in **reverse dependency order**: teams first (no
downstream dependents), then agents, then tools and templates. Attempting
to delete in the wrong order raises `CatalogValidationError`.

### Update Re-Validation

`agent_catalog.update()` runs the same cross-catalog validation as
`create()`. If the updated agent references a non-existent tool or template,
`CatalogValidationError` is raised and the update is rejected.

### Environment Variable Substitution

`resolve_env_vars()` is a standalone utility function that replaces `${VAR}`
patterns in a string with the corresponding environment variable value. If
the variable is not set, `OSError` is raised. The catalog stores `"${VAR}"`
as a literal string — resolution happens at instantiation time (when a tool
is actually used at runtime), not at YAML load time. This keeps catalog
entries portable and secret-free.

### CatalogValidationError vs EntryNotFoundError

Two distinct error types serve different failure modes:

- **`CatalogValidationError`** — business rule violations: duplicate IDs,
  broken cross-references, delete protection violations. Access details via
  the `.errors` attribute (`list[str]`).
- **`EntryNotFoundError`** — simple lookup failure: the entry doesn't exist.
  Raised by `update()` and `delete()` when the target ID is not found.

## Key API Patterns

### Two-Phase Wiring

```python
# Phase 1 — upstream refs (create/update validation)
template_catalog = TemplateCatalog(template_repo)
tool_catalog = ToolCatalog(tool_repo)
agent_catalog = AgentCatalog(agent_repo, template_catalog, tool_catalog)
team_catalog = TeamCatalog(team_repo, agent_catalog)

# Phase 2 — downstream back-refs (delete protection)
template_catalog.agent_catalog = agent_catalog
tool_catalog.agent_catalog = agent_catalog
agent_catalog.team_catalog = team_catalog
```

### Delete Protection Checks

```python
try:
    tool_catalog.delete("web-search")  # agent references this tool
except CatalogValidationError as e:
    for error in e.errors:
        print(error)
```

### resolve_env_vars() Usage

```python
import os
from akgentic.catalog import resolve_env_vars

os.environ["API_KEY"] = "sk-secret"
resolved = resolve_env_vars("Bearer ${API_KEY}")  # → "Bearer sk-secret"

resolve_env_vars("${MISSING}")  # raises OSError
```

## Common Pitfalls

- **Must wire back-refs after construction for delete protection.** Without
  setting `template_catalog.agent_catalog = agent_catalog` (and the other
  two back-ref assignments), delete protection is silently disabled and
  `delete()` calls succeed even when downstream consumers depend on the
  entry.

- **Deletion order matters — reverse dependency graph.** Delete teams before
  agents, agents before tools and templates. Attempting to delete a tool
  while an agent still references it raises `CatalogValidationError`.

- **`${VAR}` stays as a literal in YAML until `resolve_env_vars()` is
  called.** The catalog intentionally stores environment variable references
  as plain strings. Resolution is a separate, explicit step — typically
  done at runtime when instantiating a tool, not when loading from the
  catalog.

- **`CatalogValidationError` vs `EntryNotFoundError` — different exception
  types.** Don't catch a generic `Exception`. Use `CatalogValidationError`
  for business rule violations (broken references, delete protection) and
  `EntryNotFoundError` for missing entries.

- **`routes_to` uses agent names, not catalog IDs.** Agent names follow the
  `"@Name"` convention (from `config.name`). If you check `routes_to` for
  an ID like `"reviewer"` you'll miss the match — it contains `"@Reviewer"`.

## Related Examples

- [Example 04 — YAML Repository Round-Trip](04-yaml-persistence.md):
  Repository-level persistence mechanics, file layout, caching, and reload
- [Example 06 — Compound Queries & Cross-Catalog Search](06-search-and-query.md):
  Query composition, match semantics, and cross-catalog search chaining
