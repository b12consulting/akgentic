# Example 01 — Template & Tool Entry Basics

Your first contact with the catalog API: learn the two simplest entity types before
encountering cross-catalog dependencies in later examples.

## Concepts Covered

### Creating catalogs backed by YAML repositories

Each catalog type (`TemplateCatalog`, `ToolCatalog`) wraps a repository that persists
entries as individual YAML files in a directory.  You create a repository pointing at
a directory path, then wrap it in the corresponding service:

```python
template_repo = YamlTemplateCatalogRepository(Path(tmpdir) / "templates")
template_catalog = TemplateCatalog(repository=template_repo)
```

The **service** is the correct entry point — not the raw repository.  Services enforce
business rules (duplicate-ID checks, FQCN validation) that repositories do not.

### Registering, retrieving, and listing entries

- `catalog.create(entry)` — Validates and persists a new entry, returning its ID.
- `catalog.get(id)` — Returns the entry by exact ID, or `None` if not found.
- `catalog.list()` — Returns all entries in the catalog.

### TemplateEntry placeholder auto-parsing

`TemplateEntry` has a `placeholders` computed field that uses Python's
`string.Formatter().parse()` to extract `{placeholder}` names from the template
string.  The field is read-only and always derived — never stored:

```python
entry = TemplateEntry(id="research-prompt", template="You are a {role} researching {topic}")
entry.placeholders  # → ["role", "topic"]
```

### ToolEntry FQCN class resolution

`ToolEntry` carries a `tool_class` string (a fully qualified class name) and a `tool`
dict.  A `model_validator(mode="before")` dynamically imports the class at `tool_class`,
then validates the raw dict against that class's Pydantic schema.  This is exactly how
YAML deserialization works:

```python
entry = ToolEntry(
    id="web-search",
    tool_class="akgentic.tool.search.SearchTool",
    tool={"name": "Web Search", "description": "...", "web_search": {"max_results": 5}},
)
```

After construction, `entry.tool` is a fully validated `SearchTool` instance — not a dict.

### Duplicate-ID error handling

Attempting to `create()` an entry with an ID that already exists in the catalog raises
`CatalogValidationError`.  The exception carries an `errors` list of human-readable
message strings:

```python
try:
    tool_catalog.create(duplicate_entry)
except CatalogValidationError as e:
    for error in e.errors:
        print(f"  Validation error: {error}")
```

## Key API Patterns

### TemplateCatalog / ToolCatalog construction

Both catalog services follow the same construction pattern — repository first, then
service:

```python
from akgentic.catalog import (
    TemplateCatalog, ToolCatalog,
    YamlTemplateCatalogRepository, YamlToolCatalogRepository,
)

template_catalog = TemplateCatalog(repository=YamlTemplateCatalogRepository(path))
tool_catalog = ToolCatalog(repository=YamlToolCatalogRepository(path))
```

### Create / Get / List operations

All catalog services share the same CRUD interface:

| Method | Returns | Raises |
|---|---|---|
| `create(entry)` | `str` (entry ID) | `CatalogValidationError` on duplicate ID |
| `get(id)` | `Entry \| None` | — |
| `list()` | `list[Entry]` | — |

### CatalogValidationError handling

`CatalogValidationError` is distinct from Pydantic's `ValidationError`.  It represents
a business-rule violation (duplicate ID, missing reference, etc.) rather than a schema
validation failure.  Access `e.errors` for a list of error message strings.

## Common Pitfalls

- **FQCN must be fully qualified.** `SearchTool` is not enough — you need
  `akgentic.tool.search.SearchTool` (the subpackage path, then the class name).

- **Duplicate IDs are caught at `create()` time, not later.** The service checks for
  existing entries with the same ID before persisting.  You won't get a silent overwrite.

- **Use the service layer, not raw repositories.** Repositories provide storage only;
  services provide validation.  Going directly to the repository skips duplicate checks
  and FQCN resolution validation.

- **`TemplateEntry.placeholders` is read-only.**  You cannot set it — it is always
  derived from the `template` string.

## Related Examples

- **[Example 02 — Agent Entries & Cross-Validation](02-agent-entries.md):** Introduces
  `AgentEntry`, which references templates and tools by ID — adding cross-catalog
  dependency validation.
