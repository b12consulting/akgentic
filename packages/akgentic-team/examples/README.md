# akgentic-team Examples

A progressive tutorial that walks through the akgentic-team package, from
static team definitions to full lifecycle management with persistence and
crash recovery.

## Learning Path

The examples are designed to be followed in order. Each one builds on
concepts from the previous:

```
01 Team Definition     → What is a TeamCard? How do member trees work?
        ↓
02 Team Factory        → How do you turn a TeamCard into live actors?
        ↓
03 Manager Lifecycle   → How do you create, stop, resume, and delete teams?
        ↓
04 Event Sourcing      → What gets persisted? How does the event log work?
        ↓
05 Crash Recovery      → How does TeamRestorer rebuild a team from events?
        ↓
06 MongoDB Backend     → How do you swap storage backends?
```

## Prerequisites

From the workspace root, install all packages:

```bash
uv sync --all-packages --all-extras
```

This installs akgentic-core, akgentic-team, and all optional extras
(MongoDB, CLI, dev tools).

## Running

From the workspace root:

```bash
uv run python packages/akgentic-team/examples/01_team_definition.py
```

Or from the package directory:

```bash
cd packages/akgentic-team
uv run python examples/01_team_definition.py
```

## Examples

### 01 — Team Definition

**File:** [`01_team_definition.py`](01_team_definition.py) |
**Guide:** [`01-team-definition.md`](01-team-definition.md)

Build `TeamCard` and `TeamCardMember` hierarchies. Inspect the flat
`agent_cards` index and `supervisors` discovery. Verify Pydantic
serialization round-trip.

**Key types:** `TeamCard`, `TeamCardMember`, `AgentCard`, `BaseConfig`

### 02 — Team Factory

**File:** [`02_team_factory.py`](02_team_factory.py) |
**Guide:** [`02-team-factory.md`](02-team-factory.md)

Use `TeamFactory.build()` to create live Pykka actors from a TeamCard.
Inspect the `TeamRuntime` (addresses, proxies), send messages, and
perform clean actor teardown.

**Key types:** `TeamFactory`, `TeamRuntime`, `ActorSystem`

### 03 — Team Manager Lifecycle

**File:** [`03_team_manager_lifecycle.py`](03_team_manager_lifecycle.py) |
**Guide:** [`03-team-manager-lifecycle.md`](03-team-manager-lifecycle.md)

Full lifecycle management via `TeamManager`: create, stop, resume, delete.
Exercise the state machine transitions and error paths (resume RUNNING,
delete RUNNING, resume DELETED).

**Key types:** `TeamManager`, `YamlEventStore`, `TeamStatus`

### 04 — Event Sourcing

**File:** [`04_event_sourcing.py`](04_event_sourcing.py) |
**Guide:** [`04-event-sourcing.md`](04-event-sourcing.md)

See what `PersistenceSubscriber` captures: append-only events in
`events.yaml`, agent state snapshots in `states/`. Inspect the YAML file
layout on disk and understand the difference between append-only events
and overwrite state snapshots.

**Key types:** `PersistenceSubscriber`, `YamlEventStore`, `PersistedEvent`,
`AgentStateSnapshot`

### 05 — Crash Recovery

**File:** [`05_crash_recovery.py`](05_crash_recovery.py) |
**Guide:** [`05-crash-recovery.md`](05-crash-recovery.md)

Create a team, interact with it, stop it, inspect the persisted data, then
resume. Verify that state is fully restored and new events continue
persisting after recovery. Observe that Pykka addresses change on resume
(new actors) while the team_id stays the same.

**Key types:** `TeamRestorer`, `TeamManager.resume_team()`

### 06 — MongoDB Backend

**File:** [`06_mongo_backend.py`](06_mongo_backend.py) |
**Guide:** [`06-mongo-backend.md`](06-mongo-backend.md)

Run the same create/stop/resume/delete lifecycle with `MongoEventStore`
backed by mongomock (no MongoDB server required). Inspect collection
contents. The code diff from YAML is minimal — only the EventStore
constructor changes.

**Key types:** `MongoEventStore`

**Extra dependencies:** `mongomock` (included in `[dev]` extra),
`pymongo` (included in `[mongo]` extra)

## Companion Guides

Each `.py` example has a matching `.md` guide that explains:

- **Concepts** — the design patterns and architecture behind the code
- **Key API patterns** — important method signatures and usage
- **Pitfalls** — common mistakes and how to avoid them
- **Cross-references** — links to related examples and source code
