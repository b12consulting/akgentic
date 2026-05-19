# ak-catalog CLI Usage Guide

The `ak-catalog` command manages configuration **entries** for the unified
v2 catalog. Every verb dispatches into the
`akgentic.catalog.catalog.Catalog` service; no business logic lives in the
CLI. The command supports YAML and MongoDB backends, three output formats,
kind-scoped CRUD (`team`, `agent`, `tool`, `prompt`, `model`, ...), and
namespace-scoped operations (export/import bundles, validate, resolve,
load a team, inspect schemas).

## Table of Contents

- [Installation](#installation)
- [Global Options](#global-options)
- [Backend Selection](#backend-selection)
- [Output Formats](#output-formats)
- [Kind-Scoped Commands](#kind-scoped-commands)
- [Namespace Commands](#namespace-commands)
- [Schema & Introspection](#schema--introspection)
- [YAML File Format](#yaml-file-format)
- [Error Handling](#error-handling)

## Installation

Install the CLI extra:

```bash
# From workspace root
uv sync --extra cli

# Or with all extras
uv sync --all-extras
```

Verify:

```bash
ak-catalog --help
```

## Global Options

Every command inherits these options (they go **before** the subcommand):

| Option              | Default      | Description                                         |
|---------------------|--------------|-----------------------------------------------------|
| `--backend TEXT`    | `yaml`       | Storage backend: `yaml` or `mongo`.                 |
| `--root PATH`       | `./catalog`  | Root directory for YAML entries (yaml backend only).|
| `--uri TEXT`        | _(unset)_    | MongoDB connection URI (required for `mongo`).      |
| `--db TEXT`         | _(unset)_    | MongoDB database name (required for `mongo`).      |
| `--format TEXT`     | `table`      | Output format: `table`, `json`, or `yaml`.          |

```bash
ak-catalog --root ./catalog --format json team list --namespace tenant-42
```

## Backend Selection

### YAML Backend (default)

One file per entry, laid out under `<root>/<namespace>/<kind>/<id>.yaml`:

```
my-catalog/
  tenant-42/
    team/research-team.yaml
    agent/lead-agent.yaml
    tool/web-search.yaml
```

```bash
ak-catalog --root ./my-catalog team list --namespace tenant-42
```

### MongoDB Backend

Switch with `--backend mongo` and pass the URI and DB:

```bash
ak-catalog --backend mongo \
  --uri mongodb://localhost:27017 \
  --db akgentic \
  team list --namespace tenant-42
```

**Validation rules** (enforced by the CLI callback):

- `--uri` must start with `mongodb://` or `mongodb+srv://`.
- Both `--uri` and `--db` are required when `--backend=mongo`.

Entries live in a single Mongo collection indexed by the compound key
`(kind, namespace, id)`.

## Output Formats

Control the `--format` flag:

- `table` — Rich-rendered table. Ideal for interactive inspection.
- `json` — pretty-printed JSON. Stable output for piping / scripting.
- `yaml` — YAML document matching the on-disk format.

```bash
ak-catalog --format json team get --namespace tenant-42 research-team
ak-catalog --format yaml agent get --namespace tenant-42 lead-agent
```

`export` always emits a canonical YAML bundle regardless of `--format`
(the bundle is the round-trippable authored form; other renderings would
break `export > bundle.yaml; import bundle.yaml`).

## Kind-Scoped Commands

Each built-in kind (`team`, `agent`, `tool`, `prompt`, `model`) exposes
the same CRUD verbs under `ak-catalog <kind> <verb>`:

| Verb     | Description                                                       |
|----------|-------------------------------------------------------------------|
| `list`   | List entries of this kind matching provided filters.              |
| `get`    | Fetch a single entry by `(namespace, id)`.                        |
| `create` | Create an entry from a single-entry YAML file.                    |
| `update` | Update an existing entry from a single-entry YAML file.           |
| `delete` | Delete an entry; surfaces inbound referrers on failure.           |
| `search` | Structured search across every `EntryQuery` filter.               |

### list

```bash
ak-catalog team list --namespace tenant-42
ak-catalog agent list --user-id u1
ak-catalog tool list --parent-namespace tenant-42 --parent-id research-team
```

Supported filters (all optional, AND-combined): `--namespace`,
`--user-id`, `--user-id-set`, `--parent-namespace`, `--parent-id`.

### get / create / update / delete

```bash
ak-catalog agent get --namespace tenant-42 lead-agent
ak-catalog agent create ./lead-agent.yaml
ak-catalog agent update ./lead-agent.yaml
ak-catalog agent delete --namespace tenant-42 lead-agent
```

`create` / `update` read a single-entry YAML file shaped like the
`Entry` model (see [YAML File Format](#yaml-file-format)).

Delete protection: if any other entry in the same namespace references
the target via a `{"__ref__": "<id>"}` sentinel, `delete` fails with a
`CatalogValidationError` that enumerates the referrers.

### search

```bash
ak-catalog agent search --namespace tenant-42
```

`search` accepts the full `EntryQuery` surface (the same filter set as
`list`); the split exists so `list` can be invoked quickly on a single
namespace while `search` takes the full query body.

## Namespace Commands

Top-level verbs operate on a whole namespace (team + sub-entries).

### export

Emit the namespace as a single YAML bundle on stdout. Round-trips with
`import`.

```bash
ak-catalog --root ./catalog export --namespace tenant-42 > tenant-42.yaml
```

### import

Import a bundle YAML file; the team entry is created/updated first, then
every sub-entry.

```bash
ak-catalog --root ./catalog import ./tenant-42.yaml
```

### validate

Validate a namespace — either the persisted state or a dry-run bundle —
and return the structured validation report.

```bash
# Validate the persisted namespace.
ak-catalog validate --namespace tenant-42

# Validate a bundle file without persisting (dry run).
ak-catalog validate ./tenant-42.yaml
```

Exit code `0` when `report.ok` is true, `1` otherwise. Usage errors
(zero-or-both arguments, missing bundle file, malformed YAML) exit `2`.

### clone

Deep-copy an entry tree into a destination namespace, applying
ADR-007 ownership semantics.

```bash
ak-catalog clone --namespace tenant-42 research-team \
  --dst-namespace tenant-43 --user-id u2
```

### references

List entries in a namespace that reference a given id via sentinel refs.

```bash
ak-catalog references --namespace tenant-42 lead-agent
```

### resolve / load-team

`resolve` materializes a single entry (with refs populated) as its
runtime Pydantic object. `load-team` is the namespace-wide variant —
resolves the team entry into a fully-populated `TeamCard`.

```bash
ak-catalog resolve --namespace tenant-42 lead-agent
ak-catalog load-team --namespace tenant-42
```

## Schema & Introspection

### schema

Print the JSON Schema for an allowlisted Pydantic model class:

```bash
ak-catalog schema akgentic.core.AgentCard
ak-catalog schema akgentic.team.models.TeamCard
```

The model name must resolve through the `akgentic.*` allowlist (same
gate used by `model_type` payload validation). `--format table` falls
through to JSON rendering because JSON Schema is inherently nested.

### model-types

List every allowlisted Pydantic class currently imported in the process:

```bash
ak-catalog model-types
```

## YAML File Format

### Single-entry file (used by `create` / `update`)

```yaml
id: lead-agent
kind: agent
namespace: tenant-42
user_id: u1
model_type: akgentic.core.AgentCard
payload:
  role: Lead
  description: Coordinates the team
```

### Team entry with embedded refs

```yaml
id: research-team
kind: team
namespace: tenant-42
user_id: u1
model_type: akgentic.team.models.TeamCard
payload:
  name: Research Team
  entry_point:
    __ref__: lead-agent
    __type__: akgentic.core.AgentCard
  members:
    - __ref__: lead-agent
      __type__: akgentic.core.AgentCard
```

Sentinel refs (`__ref__` + `__type__`) are replaced at resolve time by
the target entry's payload cast into the requested `__type__`.

### Namespace bundle (used by `export` / `import`)

```yaml
namespace: tenant-42
team:
  id: research-team
  kind: team
  user_id: u1
  model_type: akgentic.team.models.TeamCard
  payload:
    name: Research Team
    entry_point: {__ref__: lead-agent, __type__: akgentic.core.AgentCard}
    members:
      - {__ref__: lead-agent, __type__: akgentic.core.AgentCard}
entries:
  - id: lead-agent
    kind: agent
    user_id: u1
    model_type: akgentic.core.AgentCard
    payload:
      role: Lead
      description: Coordinates the team
```

Environment-variable substitution is supported inside payloads via
`${VAR}` — handled by `akgentic.catalog.env.resolve_env_vars` on read.

## Error Handling

The CLI maps catalog exceptions to human-readable messages and a non-zero
exit code. Python tracebacks are not shown.

| Exception                   | Exit | Message shape                                     |
|-----------------------------|------|--------------------------------------------------|
| `EntryNotFoundError`        | 1    | `Not found: ...`                                 |
| `CatalogValidationError`    | 1    | `Validation error: - ...`                         |
| Pydantic `ValidationError`  | 1    | `Invalid YAML payload: - ...`                     |
| Usage error                 | 2    | stderr diagnostic (exit code per Typer / Click)  |

All successful commands exit `0`.
