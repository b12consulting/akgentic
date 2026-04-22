# akgentic-team

[![CI](https://github.com/b12consulting/akgentic-team/actions/workflows/ci.yml/badge.svg)](https://github.com/b12consulting/akgentic-team/actions/workflows/ci.yml)
[![Coverage](https://img.shields.io/endpoint?url=https://gist.githubusercontent.com/jltournay/708bb547b8679308d083be7beaf4448a/raw/coverage.json)](https://github.com/b12consulting/akgentic-team/actions/workflows/ci.yml)

Team lifecycle management for the [Akgentic](https://github.com/b12consulting/akgentic-quick-start)
multi-agent framework. Create, resume, stop, and delete multi-agent teams
with event-sourced persistence and crash recovery.

## Table of Contents

- [Overview](#overview)
- [Installation](#installation)
- [Quick Start](#quick-start)
- [Architecture](#architecture)
- [Team Definitions](#team-definitions)
- [Lifecycle Management](#lifecycle-management)
- [Persistence](#persistence)
- [CLI](#cli)
- [Examples](#examples)
- [Development](#development)
- [License](#license)

## Overview

`akgentic-team` provides the runtime lifecycle layer for Akgentic agent
teams. It sits between static team definitions and the running actor system,
providing:

- **Declarative team definitions** via `TeamCard` / `TeamCardMember` models
  with hierarchical member trees
- **One-call team building** via `TeamFactory` — from a `TeamCard` to live
  Pykka actors with routing wired
- **Full lifecycle management** via `TeamManager` — create, stop, resume,
  delete with state machine enforcement
- **Event-sourced persistence** via `PersistenceSubscriber` — every message
  is captured for crash recovery
- **Crash recovery** via `TeamRestorer` — 3-phase restore protocol rebuilds
  teams from persisted events
- **Three storage backends** (YAML files, MongoDB, and PostgreSQL via Nagra)
  behind a common `EventStore` protocol
- **CLI** (`ak-team`) for managing team instances from the command line

```
TeamCard ──▶ TeamFactory.build() ──▶ TeamRuntime (live actors)
                                          │
                              TeamManager  │  PersistenceSubscriber
                              ┌────────────┤  ┌──────────────────┐
                              │ create     │  │ save_event()     │
                              │ stop       │  │ save_agent_state │
                              │ resume ◀───┼──│ (event sourcing) │
                              │ delete     │  └──────────────────┘
                              └────────────┘
                                    │
                              EventStore Protocol
                              ┌─────┬──────┬──────────────┐
                              │     │      │              │
                         YamlEventStore  MongoEventStore  NagraEventStore
                                                          (PostgreSQL via Nagra)
```

## Installation

### Workspace Installation (Recommended)

This package is designed for use within the Akgentic monorepo workspace:

```bash
git clone git@github.com:b12consulting/akgentic-quick-start.git
cd akgentic-quick-start
git submodule update --init --recursive

uv venv
source .venv/bin/activate
uv sync --all-packages --all-extras
```

All dependencies (`akgentic-core`) resolve automatically via workspace
configuration.

### Optional Extras

```bash
# CLI (Typer + Rich)
uv sync --extra cli

# MongoDB backend
uv sync --extra mongo

# PostgreSQL backend (Nagra)
uv sync --extra postgres

# Everything
uv sync --all-extras
```

## Quick Start

Create a team, send a message, stop it, and resume it:

```python
from pathlib import Path
from akgentic.core import ActorSystem, AgentCard, BaseConfig, BaseState
from akgentic.core.akgent import Akgent
from akgentic.team import (
    TeamCard, TeamCardMember, TeamManager, YamlEventStore,
)

# Define a simple agent
class EchoAgent(Akgent):
    def receiveMsg_UserMessage(self, msg):
        print(f"Echo: {msg.content}")

# Build a team definition
card = AgentCard(
    role="Echo", description="Echoes messages",
    skills=["echo"], agent_class=EchoAgent,
    config=BaseConfig(name="@Echo", role="Echo"),
)
team_card = TeamCard(
    name="echo-team", description="Simple echo team",
    entry_point=TeamCardMember(card=card),
    members=[TeamCardMember(card=card)],
)

# Create and manage a team
actor_system = ActorSystem()
event_store = YamlEventStore(Path("./data"))
manager = TeamManager(actor_system=actor_system, event_store=event_store)

runtime = manager.create_team(team_card)
runtime.send("Hello!")  # → Echo: Hello!

# Stop and resume
manager.stop_team(runtime.id)
resumed = manager.resume_team(runtime.id)  # full state restored
resumed.send("Back!")   # → Echo: Back!

# Clean up
manager.stop_team(resumed.id)
manager.delete_team(resumed.id)
```

## Architecture

The package follows a layered architecture with strict upward dependency
flow:

```
┌──────────────────────────────────────────────┐
│  Interfaces: CLI (ak-team), Python API       │
├──────────────────────────────────────────────┤
│  TeamManager (lifecycle facade)              │
│  TeamFactory / TeamRestorer                  │
│  PersistenceSubscriber                       │
├──────────────────────────────────────────────┤
│  Models: TeamCard, TeamRuntime, Process      │
│  Ports:  EventStore, ServiceRegistry         │
├──────────────────────────────────────────────┤
│  Repositories: YamlEventStore, MongoEventStore,│
│                NagraEventStore (PostgreSQL)    │
└──────────────────────────────────────────────┘
```

### Layer Responsibilities

| Layer | Role |
|---|---|
| **Models** | Pydantic models for team definitions, runtime state, and persistence |
| **Ports** | Protocol-based abstractions for storage and service discovery |
| **Services** | TeamFactory (build), TeamManager (lifecycle), TeamRestorer (recovery) |
| **Repositories** | EventStore implementations — YAML and MongoDB |
| **Interfaces** | CLI commands and direct Python imports |

### State Machine

Teams follow a strict lifecycle:

```
    create_team()        stop_team()         delete_team()
  ──────────────▶ RUNNING ──────────▶ STOPPED ──────────▶ DELETED
                    ▲                    │
                    └────────────────────┘
                       resume_team()
```

## Team Definitions

### TeamCard

Declarative team structure with hierarchical member trees:

```python
team_card = TeamCard(
    name="research-team",
    description="A research team with lead and workers",
    entry_point=TeamCardMember(card=lead_card),
    members=[
        TeamCardMember(card=lead_card, members=[
            TeamCardMember(card=researcher_card, headcount=3),
            TeamCardMember(card=reviewer_card),
        ]),
    ],
)

team_card.agent_cards    # flat index of all AgentCards by name
team_card.supervisors    # AgentCards with subordinates
```

### TeamRuntime

Live handle to a running team, returned by `create_team()` and
`resume_team()`:

```python
runtime.send("Hello!")                    # send to entry point
runtime.send_to("@Reviewer", message)    # directed messaging
runtime.id                                # team UUID
runtime.addrs                             # agent name → ActorAddress
```

## Persistence

### Event Sourcing

Every message flowing through the orchestrator is captured by
`PersistenceSubscriber` as an append-only event. Agent state snapshots
are saved on `StateChangedMessage`.

### Storage Backends

**YAML (default)** — zero infrastructure, per-team directory layout:

```
data/{team-uuid}/
  team.yaml              # Process metadata (overwrite)
  events.yaml            # All events (append-only)
  states/{agent-id}.yaml # Agent state snapshots (overwrite)
```

**MongoDB** — install the `[mongo]` extra:

```python
from akgentic.team import MongoEventStore
import pymongo

db = pymongo.MongoClient("mongodb://localhost:27017")["akgentic"]
event_store = MongoEventStore(db)
# Collections: teams, events, agent_states
```

**PostgreSQL (Nagra)** — install the `[postgres]` extra:

```bash
uv sync --extra postgres
# or: uv add "akgentic-team[postgres]"
```

The PostgreSQL backend is built on [Nagra](https://pypi.org/project/nagra/)
and stores team state across three tables with promoted query keys plus a
`data JSONB` payload (the payload is authoritative — promoted columns are
indexes, not the source of truth):

| Table | Natural key | Purpose |
|---|---|---|
| `team_process_entries` | `id` | One row per team — `Process` snapshot |
| `event_entries` | `(team_id, sequence)` | Append-only event log |
| `agent_state_entries` | `(team_id, agent_id)` | Agent state snapshots |

Each public `NagraEventStore` method opens its own `Transaction`. The one
exception is `delete_team`, which spans a single transaction across the
three tables (ordered: `agent_state_entries` → `event_entries` →
`team_process_entries`) so cascade deletion is atomic. `save_event`
propagates the raw `psycopg`/Nagra `UniqueViolation` on duplicate
`(team_id, sequence)` — matching the Mongo backend's raw
`DuplicateKeyError` propagation.

**Environment variables.** The backend follows the V1 Akgentic conventions
so existing operator `.env` files work unchanged:

| Variable | Purpose |
|---|---|
| `POSTGRES_SERVER` | Database host |
| `POSTGRES_PORT` | Database port (typically `5432`) |
| `POSTGRES_USER` | Database user |
| `POSTGRES_PASSWORD` | Database password |
| `POSTGRES_DB` | Database name |
| `DB_CONN_STRING_PERSISTENCE` | Full libpq URL; what `NagraEventStore` receives as `conn_string` |

`DB_CONN_STRING_PERSISTENCE` is **shared verbatim with `akgentic-catalog`** —
both modules target the same database. Their tables are disjoint by design:
the catalog owns `template_entries`, `tool_entries`, `agent_entries`, and
`team_entries`; this package owns `team_process_entries` (renamed from
`team_entries` to prevent collision), `event_entries`, and
`agent_state_entries`. A single Postgres instance can serve both modules.

`NagraEventStore.__init__` takes `conn_string` directly as a positional
argument — env-var reading happens at the wiring layer (application
startup / infra code), **not** inside the event store. This keeps the
storage layer decoupled from process-level configuration.

**Schema initialisation.** Call `init_db(conn_string)` once per deployment
(at application startup or as a deploy hook). The call is idempotent —
it creates any missing tables and is safe to re-run. `NagraEventStore`'s
constructor does **not** call `init_db` implicitly.

```python
from akgentic.team.repositories.postgres import NagraEventStore, init_db

conn_string = "postgresql://akgentic:akgentic@localhost:5432/akgentic"

# One-time (idempotent) schema creation — run at deploy time.
init_db(conn_string)

# Construct the event store with the same conn_string.
event_store = NagraEventStore(conn_string)
```

Schema evolution is handled as a redeploy concern — the backend does not
adopt a migration framework. Drop-and-recreate semantics or manual
`ALTER TABLE` statements are the expected evolution path.

#### Database initialization (init container)

For Kubernetes / Nomad deployments, run the schema-creation hook as a
dedicated init container before the main team-runtime process starts:

```bash
python -m akgentic.team.scripts.init_db
```

The script reads `DB_CONN_STRING_PERSISTENCE` and exits with one of:

| Exit code | Meaning |
|---|---|
| `0` | Success — tables created or already present |
| `2` | `DB_CONN_STRING_PERSISTENCE` not set |
| `1` | Any other failure (nagra not installed, connection refused, `init_db` raised) |

Catalog and team can share a single init step — both modules expose the
same entry-point shape (`python -m akgentic.<module>.scripts.init_db`) and
read the same `DB_CONN_STRING_PERSISTENCE` env var.

**Kubernetes initContainer** snippet:

```yaml
spec:
  initContainers:
    - name: akgentic-team-init-db
      image: ghcr.io/b12consulting/akgentic-team:latest
      command: ["python", "-m", "akgentic.team.scripts.init_db"]
      env:
        - name: DB_CONN_STRING_PERSISTENCE
          valueFrom:
            secretKeyRef:
              name: akgentic-postgres
              key: conn-string
```

**Nomad prestart task** snippet:

```hcl
task "init-db" {
  driver = "docker"
  lifecycle {
    hook    = "prestart"
    sidecar = false
  }
  config {
    image   = "ghcr.io/b12consulting/akgentic-team:latest"
    command = "python"
    args    = ["-m", "akgentic.team.scripts.init_db"]
  }
  template {
    destination = "secrets/db.env"
    env         = true
    data        = <<EOF
DB_CONN_STRING_PERSISTENCE={{ with secret "kv/akgentic" }}{{ .Data.data.conn_string }}{{ end }}
EOF
  }
}
```

#### Out of scope: enterprise wiring

Wiring `NagraEventStore` into `akgentic-infra-enterprise`'s server +
worker bootstrap (opt-in via `AKGENTIC_EVENT_STORE = "postgres"` or
equivalent) is a **follow-up story tracked in `akgentic-infra-enterprise`**.
This package only ships the backend implementation, the `[postgres]`
extra, the deployment hook, and the documentation. The enterprise
deployment project owns the application-level switch.

### Crash Recovery

`TeamRestorer` executes a 3-phase protocol:
1. **Load** persisted events and agent state snapshots
2. **Rebuild** agents from the event log (Orchestrator first, then others)
3. **Replay** all events to reconstruct full state including LLM context

## CLI

The `ak-team` command is available when the `[cli]` extra is installed.
See the [CLI README](src/akgentic/team/cli/README.md) for full documentation.

```bash
# List all teams
ak-team list
ak-team list --status running

# Inspect a team
ak-team inspect <team-id>

# Create a team from a TeamCard YAML file (interactive — Ctrl+C to stop)
ak-team create team-card.yaml

# Resume a stopped team
ak-team resume <team-id>

# Delete a stopped team
ak-team delete <team-id>

# Use MongoDB backend
ak-team --backend mongodb --mongo-uri mongodb://localhost:27017 --mongo-db akgentic list
```

## Examples

Six progressive, self-contained examples in the [examples/](examples/)
directory. See the [Examples README](examples/README.md) for full
descriptions and learning path. Each includes a runnable `.py` script
and a companion `.md` explaining concepts and pitfalls.

```bash
uv run python examples/01_team_definition.py
```

| # | Script | Topic |
|---|---|---|
| 01 | `01_team_definition.py` | TeamCard & TeamCardMember hierarchies |
| 02 | `02_team_factory.py` | TeamFactory.build() & TeamRuntime |
| 03 | `03_team_manager_lifecycle.py` | Full lifecycle: create, stop, resume, delete |
| 04 | `04_event_sourcing.py` | PersistenceSubscriber & YamlEventStore |
| 05 | `05_crash_recovery.py` | TeamRestorer & crash recovery |
| 06 | `06_mongo_backend.py` | MongoEventStore & backend portability |

## Development

### Prerequisites

- Python 3.12+
- [uv](https://docs.astral.sh/uv/) package manager

### Setup

```bash
uv sync --all-extras
```

### Commands

```bash
# Run tests
uv run pytest packages/akgentic-team/tests/

# Run tests with coverage
uv run pytest packages/akgentic-team/tests/ --cov=akgentic.team --cov-fail-under=80

# Lint
uv run ruff check packages/akgentic-team/src/

# Format
uv run ruff format packages/akgentic-team/src/

# Type check
uv run mypy packages/akgentic-team/src/
```

### Project Structure

```
src/akgentic/team/
    __init__.py          # Public API (17 exports)
    models.py            # TeamCard, TeamRuntime, Process, TeamStatus, persistence models
    ports.py             # EventStore, ServiceRegistry protocols, NullServiceRegistry
    factory.py           # TeamFactory — static builder
    manager.py           # TeamManager — lifecycle facade
    restorer.py          # TeamRestorer — crash recovery
    subscriber.py        # PersistenceSubscriber — event sourcing bridge
    repositories/        # YamlEventStore, MongoEventStore
    cli/                 # ak-team CLI (Typer)
examples/                # 6 progressive examples with companion docs
tests/                   # 196 tests organized by domain
```

## License

See the repository root for license information.
