# Example 04 — YAML Repository Round-Trip

## Concepts Covered

### YAML File Layout Convention

When `repo.create(entry)` is called, the repository writes the entry to
`{catalog_dir}/{entry_id}.yaml`. Each file contains a YAML list (even for
a single entry). The entry is serialized via `model_dump(mode="json")`,
which correctly handles enums, nested Pydantic models, and sets.

### Serialization and Deserialization with Type Fidelity

`ToolEntry` stores a `tool_class` FQCN string (e.g.
`"akgentic.tool.search.SearchTool"`). On deserialization, a
`model_validator(mode="before")` resolves the FQCN to the concrete class
and validates the `tool` dict against it. This means `SearchTool` config
fields like `web_search.max_results` survive the round-trip with full type
fidelity — no manual reconstruction needed.

### Lazy Caching and reload()

The repository maintains an in-memory cache (`_entries`) that is `None` on
init. Entries are loaded lazily on first access via `_ensure_loaded()`.
Write operations (`create()`, `update()`, `delete()`) automatically
invalidate the cache by setting `_entries = None`. However, external file
modifications (e.g. another process editing YAML on disk) are **not**
detected. Call `repo.reload()` explicitly to invalidate the cache and pick
up external changes on the next access.

### Multi-Entry YAML Files

A single YAML file can contain a list of entries. The `_load_all()` scan
reads every `*.yaml` file in the catalog directory and processes each item
in the list. This is useful for bundling related entries (e.g. a set of
prompt templates) into one file, though the convention is one entry per
file.

### Duplicate ID Detection Across Files

During `_load_all()`, the repository tracks `seen_ids: dict[str, Path]`.
If the same entry ID appears in two different files, a
`CatalogValidationError` is raised identifying both files. This detection
happens at load time (when `list()` or `get()` triggers `_load_all()`),
not at write time.

## Key API Patterns

### YamlRepository Construction

```python
from akgentic.catalog import YamlTemplateCatalogRepository
template_repo = YamlTemplateCatalogRepository(Path("/path/to/catalog/dir"))
```

Repositories are constructed with a `Path` to the catalog directory. No
service wiring is needed — repositories handle persistence only.

### create() Write Path

```python
template_repo.create(TemplateEntry(id="my-prompt", template="Hello {name}"))
```

`create()` checks for duplicate IDs against existing entries, writes the
entry to `{catalog_dir}/{entry_id}.yaml`, and invalidates the cache.

### Fresh Instance Round-Trip

```python
# Write with one repo instance
original_repo = YamlToolCatalogRepository(Path(tool_dir))
original_repo.create(tool_entry)

# Read back with a completely new instance
fresh_repo = YamlToolCatalogRepository(Path(tool_dir))
loaded = fresh_repo.list()  # Reads from disk
```

Creating a fresh repository instance pointing at the same directory proves
that entries are fully persisted and can be reconstructed from YAML alone.

### reload() Cache Invalidation

```python
# External process modifies a YAML file on disk
repo.list()     # Returns stale cached data
repo.reload()   # Invalidates cache
repo.list()     # Re-reads from disk, returns updated data
```

## Common Pitfalls

- **Cache is not auto-invalidated on external changes.** If another process
  (or manual edit) modifies YAML files on disk, the repository's in-memory
  cache remains stale. Always call `repo.reload()` after external
  modifications.

- **Duplicate IDs are detected at load time, not write time.** If you
  manually create two YAML files with the same entry ID, the error only
  surfaces when `_load_all()` runs (triggered by `list()`, `get()`, or
  `create()`). The `create()` method always checks for duplicates against
  existing entries (loading from disk if needed), but entries written
  directly to YAML files bypass this check — duplicates across manually
  created files are only detected when `_load_all()` next runs.

- **reload() must be called after external file modifications.** This is by
  design — the repository prioritizes performance (avoiding disk scans on
  every read) over automatic consistency with external changes.

- **Multi-entry files work, but one-per-file is the convention.** While
  `_load_all()` supports lists of entries in a single YAML file, the
  `create()` method always writes to `{entry_id}.yaml` — one file per
  entry. Multi-entry files are useful for bulk imports or manual bundling.

## Related Examples

- [Example 03 — Team Composition, Member Trees & Profiles](03-team-entries.md):
  TeamEntry hierarchies, entry_point validation, members vs profiles
- [Example 05 — Full Catalog Wiring, Delete Protection & Env Vars](05-catalog-wiring.md):
  Service-layer catalogs with bidirectional wiring, delete protection across
  every boundary, update re-validation, and environment variable substitution
