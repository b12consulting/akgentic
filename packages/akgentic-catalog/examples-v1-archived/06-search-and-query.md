# Example 06 — Compound Queries & Cross-Catalog Search

## Concepts Covered

### Four Query Model Types

Each catalog type has a dedicated query model:

- **`TemplateQuery`** — filter templates by `id` (exact) or `placeholder`
  (membership check against the template's placeholder set).
- **`ToolQuery`** — filter tools by `id` (exact), `name` (substring),
  `description` (substring), or `tool_class` (exact FQCN).
- **`AgentQuery`** — filter agents by `id` (exact), `role` (exact), `skills`
  (set overlap), or `description` (substring).
- **`TeamQuery`** — filter teams by `id` (exact), `name` (substring),
  `description` (substring), or `agent_id` (recursive tree walk through
  nested `TeamMemberSpec` hierarchy).

### Field-Specific Match Semantics

Different fields use different matching strategies based on their data type
and intended use:

- **Exact match** — used for identifiers and class paths where partial
  matching would be misleading. Case-sensitive.
- **Substring match** — used for human-readable text fields (`name`,
  `description`) where flexible discovery is valuable. Case-insensitive.
- **Membership check** — used for `TemplateQuery.placeholder`. Tests
  whether the queried placeholder name exists in the template's
  `placeholders` list.
- **Set overlap** — used for `AgentQuery.skills`. Returns entries where
  *any* queried skill appears in the agent's skill list. This is union
  semantics (any overlap counts), not exact set equality.
- **Recursive tree walk** — used for `TeamQuery.agent_id`. Descends
  through the entire nested `TeamMemberSpec` hierarchy via
  `_agent_in_members()` to find the agent at any depth.

### Match Semantics Reference Table

| Query Model | Field | Strategy | Case Sensitivity | Detail |
|---|---|---|---|---|
| `TemplateQuery` | `id` | Exact | Case-sensitive | `entry.id == query.id` |
| `TemplateQuery` | `placeholder` | Membership | Case-sensitive | `query.placeholder in entry.placeholders` |
| `ToolQuery` | `id` | Exact | Case-sensitive | `entry.id == query.id` |
| `ToolQuery` | `name` | Substring | Case-insensitive | `query.name.lower() in entry.tool.name.lower()` |
| `ToolQuery` | `description` | Substring | Case-insensitive | `query.description.lower() in entry.tool.description.lower()` |
| `ToolQuery` | `tool_class` | Exact | Case-sensitive | `entry.tool_class == query.tool_class` |
| `AgentQuery` | `id` | Exact | Case-sensitive | `entry.id == query.id` |
| `AgentQuery` | `role` | Exact | Case-sensitive | `entry.card.role == query.role` |
| `AgentQuery` | `skills` | Set overlap | Case-sensitive | `set(query.skills) & set(entry.card.skills)` — any overlap = match |
| `AgentQuery` | `description` | Substring | Case-insensitive | `query.description.lower() in entry.card.description.lower()` |
| `TeamQuery` | `id` | Exact | Case-sensitive | `entry.id == query.id` |
| `TeamQuery` | `name` | Substring | Case-insensitive | `query.name.lower() in entry.name.lower()` |
| `TeamQuery` | `description` | Substring | Case-insensitive | `query.description.lower() in entry.description.lower()` |
| `TeamQuery` | `agent_id` | Recursive tree walk | Case-sensitive | `_agent_in_members(agent_id, entry.members)` — walks entire nested tree |

### AND Composition

When multiple fields are set (non-`None`) on a single query object, **all
conditions must match** for an entry to be included in results. This is
implicit AND semantics — there is no OR operator.

```python
# Both conditions must match: name contains "search" AND description contains "web"
ToolQuery(name="search", description="web")
```

### Cross-Catalog Search Chaining

Queries from one catalog's search can feed into another catalog's query.
This enables complex discovery patterns like "find all teams containing
agents with a specific skill":

```python
# Step 1: Find agents with "research" skill
research_agents = agent_catalog.search(AgentQuery(skills=["research"]))

# Step 2: For each agent, find teams containing them
for agent in research_agents:
    teams = team_catalog.search(TeamQuery(agent_id=agent.id))
```

### Empty Result Handling

Queries with no matches return an empty list — they never raise exceptions.
This makes it safe to chain queries without defensive error handling.

## Key API Patterns

### Query Construction

All query fields default to `None`. Only set the fields you want to filter
on — unset fields are ignored (they don't filter anything).

```python
# Filter by one field
TemplateQuery(placeholder="role")

# Filter by multiple fields (AND)
AgentQuery(role="Manager", skills=["coordination"])
```

### search() Usage

Every catalog service exposes `search(query)` which delegates to the
repository layer:

```python
results = agent_catalog.search(AgentQuery(skills=["research"]))
# Returns: list[AgentEntry]
```

### Cross-Catalog Chaining Pattern

```python
# Find agents by skill, then find teams containing those agents
research_agents = agent_catalog.search(AgentQuery(skills=["research"]))
for agent in research_agents:
    teams = team_catalog.search(TeamQuery(agent_id=agent.id))
    print(f"Agent '{agent.id}' is in teams: {[t.id for t in teams]}")
```

## Common Pitfalls

- **`skills` uses set overlap, not exact match.** `AgentQuery(skills=["research"])`
  matches any agent that has "research" as *one of* their skills — it does
  not require the agent's skill list to be exactly `["research"]`. An agent
  with `skills=["research", "analysis"]` matches.

- **`agent_id` in `TeamQuery` does a recursive tree walk.** It searches
  through the entire nested `TeamMemberSpec` hierarchy, not just top-level
  members. An agent nested three levels deep in the member tree will still
  be found.

- **All non-`None` fields are AND-ed.** There is no way to express OR
  semantics in a single query. If you need OR, run multiple queries and
  merge the results.

- **`role` and `tool_class` are exact match (case-sensitive).**
  `AgentQuery(role="manager")` will NOT match an agent with
  `role="Manager"`. Use the exact string.

- **`name` and `description` are substring match (case-insensitive).**
  `ToolQuery(name="SEARCH")` will match a tool named "Web Search" because
  the comparison is lowercased on both sides.

- **`placeholder` is a membership check, not substring.** It checks whether
  the exact placeholder name exists in the template's placeholder list.
  `TemplateQuery(placeholder="rol")` will NOT match a template with
  placeholder `"role"`.

## Related Examples

- [Example 05 — Full Catalog Wiring, Delete Protection & Env Vars](05-catalog-wiring.md):
  Service-layer catalogs with bidirectional wiring, delete protection, and
  environment variable substitution
- [Example 07 — Python-First Workflows](07-python-first.md):
  Build and register entries entirely in Python via prototyping, testing,
  and programmatic generation workflows
