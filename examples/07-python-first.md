# Example 07 — Python-First Workflows

## Concepts Covered

### D10: Python-First Principle

The catalog service layer accepts Pydantic models directly — YAML is just
one persistence interface.  There is no difference in validation whether an
entry comes from YAML, REST API, CLI, or Python code.  This example is the
canonical demonstration of that principle.

### Three Workflows

1. **Prototyping workflow** — build entries in Python, register via
   `catalog.create()`, iterate with `catalog.update()`.  No YAML editing
   required at any point.

2. **Testing workflow** — create entries inside a `tempfile.TemporaryDirectory`
   context manager, exercise `get` / `list` / `search`, then let Python
   clean up automatically.  No YAML fixture files needed.

3. **Programmatic generation** — build agent entries in a loop from a roles
   list (same pattern as `agent_team.py`), register each via
   `catalog.create()`, and assert all appear in `catalog.list()`.

## What You'll Learn

- `catalog.create()` accepts Pydantic models directly — no YAML authoring
  step required.
- After `create()`, YAML files appear on disk as a side effect.  The
  developer never writes them.
- `catalog.update(id, entry)` re-validates the modified entry through the
  same pipeline as `create()`.
- The validation pipeline (cross-catalog reference checks, FQCN validation,
  duplicate ID detection) is identical regardless of entry origin.
- `tempfile.TemporaryDirectory` provides complete test isolation with
  automatic cleanup.

## Key API Patterns

### Direct Model Construction

```python
template = TemplateEntry(
    id="researcher-prompt",
    template="You are a {role} researching {topic}.",
)
template_catalog.create(template)
```

### Create / Update Cycle

```python
# Modify the entry in Python
updated = TemplateEntry(
    id="researcher-prompt",
    template="You are a senior {role} researching {topic}. {instructions}",
)
template_catalog.update("researcher-prompt", updated)

# Verify the change persists
refreshed = template_catalog.get("researcher-prompt")
assert "senior" in refreshed.template
```

### Programmatic Generation Loop

```python
roles: list[tuple[str, str, list[str], str]] = [
    ("planner", "Planner", ["planning"], "Plans tasks"),
    ("coder", "Coder", ["coding"], "Writes code"),
]

for agent_id, role, skills, description in roles:
    agent = make_agent(agent_id, role, skills, description)
    agent_catalog.create(agent)

# All generated agents appear in catalog
generated_ids = {r[0] for r in roles}
catalog_ids = {a.id for a in agent_catalog.list()}
assert generated_ids.issubset(catalog_ids)
```

## Common Pitfalls

- **Forgetting upstream wiring order.**  Catalogs must be wired in
  dependency order: `TemplateCatalog` and `ToolCatalog` first, then
  `AgentCatalog` (which needs both), then `TeamCatalog` (which needs
  `AgentCatalog`).  Creating them out of order means cross-catalog
  validation cannot verify references.

- **Assuming YAML must exist first.**  A common misconception is that you
  need to write YAML files before the catalog can work.  The service layer
  accepts Pydantic models directly — YAML files are written by the
  repository as a persistence side effect.

- **Not using temp directories for isolation.**  Without
  `tempfile.TemporaryDirectory`, test runs accumulate YAML files on disk.
  Always wrap catalog repositories in temp directories for tests and
  prototyping to ensure clean state.

- **Forgetting that `update()` re-validates.**  `catalog.update()` runs the
  full validation pipeline, including cross-catalog reference checks.  If
  you update an agent to reference a tool that does not exist, the update
  will fail with `CatalogValidationError`.

- **Placeholders are sorted alphabetically.**  `TemplateEntry.placeholders`
  returns placeholder names in sorted order, not in template occurrence
  order.  Assert with `set()` comparison or sorted lists.

## Related Examples

- [Example 05 — Full Catalog Wiring, Delete Protection & Env Vars](05-catalog-wiring.md):
  Service-layer catalogs with bidirectional wiring and delete protection
- [Example 06 — Compound Queries & Cross-Catalog Search](06-search-and-query.md):
  Query models, match semantics, and cross-catalog search chaining
- [Example 08 — Custom Types & FQCN Round-Trip](08-custom-types.md):
  Build custom ToolCard, AgentConfig, Agent, and message types with FQCN
  round-trip through the catalog
