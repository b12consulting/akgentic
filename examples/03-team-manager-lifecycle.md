# Team Manager Lifecycle -- Create, Stop, Resume, Delete

## Concepts Covered

### TeamManager as Facade

`TeamManager` is the primary API facade for team lifecycle management. It composes:

- **TeamFactory** -- builds running teams from TeamCard definitions
- **TeamRestorer** -- rebuilds stopped teams from persisted EventStore data
- **PersistenceSubscriber** -- bridges orchestrator events to the EventStore
- **EventStore** -- persistence backend (YamlEventStore or MongoEventStore)

You interact with `TeamManager` rather than these components directly.

### The State Machine

Teams follow a strict lifecycle state machine:

```
RUNNING  ──stop──▶  STOPPED  ──delete──▶  DELETED
   ▲                    │
   └────resume──────────┘
```

- **RUNNING**: Actors are alive, messages can be sent and processed.
- **STOPPED**: Actors are torn down, but all persisted data (Process metadata, events, agent states) is retained for resume.
- **DELETED**: All persisted data is purged. The team cannot be restored.

### Lifecycle Operations

- **`create_team(team_card, user_id)`** -- Builds actors via TeamFactory, persists a Process record with RUNNING status, registers with ServiceRegistry. Returns a `TeamRuntime` handle.
- **`stop_team(team_id)`** -- Unsubscribes event subscribers, tears down actors (orchestrator first, then agents), persists Process with STOPPED status. Idempotent on already-stopped teams.
- **`resume_team(team_id)`** -- Loads Process and events from EventStore, rebuilds actors via TeamRestorer, replays events, returns a fresh `TeamRuntime`. The team_id is preserved but actor addresses are new.
- **`delete_team(team_id)`** -- Purges all persisted data for the team. Only allowed on STOPPED teams.
- **`get_team(team_id)`** -- Returns `Process` metadata (status, timestamps, user info) or `None` if not found.

## Key API Patterns

```python
from akgentic.core.actor_system_impl import ActorSystem
from akgentic.team.manager import TeamManager
from akgentic.team.repositories.yaml import YamlEventStore

# Minimal constructor for single-process mode
team_manager = TeamManager(actor_system=actor_system, event_store=event_store)

# Create a team -- returns TeamRuntime
runtime = team_manager.create_team(team_card, user_id="demo")

# Query team metadata -- returns Process | None
process = team_manager.get_team(team_id)

# Stop a running team (idempotent -- no-op on already-stopped)
team_manager.stop_team(team_id)

# Resume a stopped team -- returns fresh TeamRuntime
# IMPORTANT: resume requires StartMessage events in the EventStore.
# See "Common Pitfalls" below for the seeding requirement.
new_runtime = team_manager.resume_team(team_id)

# Delete a stopped team -- purges all data
team_manager.delete_team(team_id)
```

## Common Pitfalls

- **Must call `ActorSystem.shutdown()` in a finally block.** Actors are threads that leak if not cleaned up. Always use try/finally around your team operations.

- **`resume_team` returns a NEW runtime with different actor addresses.** Pykka assigns fresh addresses when actors are rebuilt. Do not cache old addresses across stop/resume cycles.

- **Cannot resume a RUNNING team.** You must stop it first. Attempting to resume a running team raises `ValueError`.

- **Cannot delete a RUNNING team.** You must stop it first. Attempting to delete a running team raises `ValueError`.

- **Cannot resume a DELETED team.** Once deleted, all data is purged. Attempting to resume raises `ValueError`.

- **`stop_team` is idempotent.** Calling it on an already-stopped team is a no-op. Safe to call in cleanup paths without checking state first.

- **`time.sleep()` needed after `runtime.send()`.** Actors process messages on background threads. A short sleep (e.g., 0.5s) after sending gives actors time to process before the main thread inspects results or shuts down.

- **StartMessage events must be seeded for resume.** In the current framework, agents are spawned without an orchestrator reference, so their StartMessages are not automatically persisted through the PersistenceSubscriber. Before stopping a team you intend to resume, seed the event store with StartMessage events for the orchestrator and all agents. See `seed_start_events()` in the example for the pattern.

## Links

- **Previous**: [Example 02 -- Team Factory](./02-team-factory.md) covers `TeamFactory.build()`, `TeamRuntime` inspection, and `runtime.send()`.
- **Next**: Example 04 -- Event Sourcing and Crash Recovery (coming soon) will cover the event log, agent state snapshots, and how TeamRestorer rebuilds teams from persisted data.
