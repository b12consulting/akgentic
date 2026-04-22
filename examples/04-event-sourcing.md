# Event Sourcing -- Live Persistence & Inspection

Companion guide for `04_event_sourcing.py`. Covers the persistence model,
PersistenceSubscriber, YamlEventStore, and the YAML file layout.

## Concepts

### PersistenceSubscriber

`PersistenceSubscriber` is the bridge between `EventSubscriber` (akgentic-core)
and `EventStore` (akgentic-team). It is registered with the orchestrator and
intercepts every message flowing through it:

- **Every message** is persisted as a `PersistedEvent` (append-only event log)
- **`StateChangedMessage`** additionally triggers an `AgentStateSnapshot` write

TeamManager creates and registers a PersistenceSubscriber automatically during
`create_team()`. The subscriber is always the first in the subscriber list.

### What Gets Persisted

| Data Type              | Model               | Strategy    | File                    |
|------------------------|----------------------|-------------|-------------------------|
| Team metadata          | `Process`            | Overwrite   | `team.yaml`             |
| Message events         | `PersistedEvent`     | Append-only | `events.yaml`           |
| Agent state snapshots  | `AgentStateSnapshot` | Overwrite   | `states/{agent_id}.yaml`|

- **PersistedEvent**: `team_id` (UUID), `sequence` (int), `event` (Message), `timestamp` (datetime)
- **AgentStateSnapshot**: `team_id` (UUID), `agent_id` (str), `state` (BaseState), `updated_at` (datetime)
- **Process**: `team_id`, `team_card`, `status`, `user_id`, `user_email`, `created_at`, `updated_at`

### YAML File Layout

```
{data_dir}/
  {team_uuid}/
    team.yaml           # Process metadata (overwrite on status change)
    events.yaml         # Append-only event log (multi-document YAML)
    states/
      {agent_name}.yaml # Latest agent state snapshot (overwrite on change)
```

### Append-Only Events vs Overwrite State Snapshots

- **Events** (`events.yaml`): Every message is appended. The file grows
  monotonically. Events are separated by `---` (YAML multi-document format).
  Sequence numbers and timestamps provide ordering.

- **State snapshots** (`states/*.yaml`): Each agent has at most one snapshot
  file. When `notify_state_change()` fires a `StateChangedMessage`, the
  snapshot is overwritten with the latest state. The number of snapshot files
  equals the number of agents that called `notify_state_change()`.

- **LLM context** is NOT persisted separately. It is reconstructed from
  event replay during resume (see example 05).

### Sequence Numbers and Timestamps

Each `PersistedEvent` has:
- `sequence`: Monotonically increasing counter within the PersistenceSubscriber
- `timestamp`: UTC datetime when the event was persisted

These enable ordered replay during team restoration.

## Key API Patterns

```python
# Load all events for a team (sorted by sequence)
events: list[PersistedEvent] = event_store.load_events(team_id)

# Load all agent state snapshots for a team
snapshots: list[AgentStateSnapshot] = event_store.load_agent_states(team_id)

# PersistenceSubscriber receives events from the orchestrator
subscriber = PersistenceSubscriber(team_id, event_store)
subscriber.on_message(msg)  # persists event + optional state snapshot

# Trigger state persistence from an agent
self.state.count += 1
self.state.notify_state_change()  # -> StateChangedMessage -> orchestrator -> subscriber
```

## Common Pitfalls

1. **Events grow unboundedly.** The append-only event log has no built-in
   compaction or truncation. For long-running teams, `events.yaml` can grow
   large.

2. **State snapshots require explicit `notify_state_change()`.** If an agent
   mutates its state without calling `notify_state_change()`, the persisted
   snapshot will be stale. The snapshot only updates when `StateChangedMessage`
   flows through the subscriber.

3. **StartMessage events must be seeded manually.** In the current framework,
   agents spawned by `TeamFactory` / `TeamManager` do not have an orchestrator
   reference, so their `StartMessage` events are not automatically routed
   through `PersistenceSubscriber`. The `seed_start_events()` helper persists
   them explicitly. This is required for `resume_team()` to work.

4. **The `_restoring` flag prevents duplicate writes.** During event replay
   (resume), `PersistenceSubscriber.set_restoring(True)` suppresses all writes
   so replayed events are not re-persisted.

## See Also

- [Example 03: TeamManager Lifecycle](03-team-manager-lifecycle.md) -- covers
  create, stop, resume, delete with the `seed_start_events` pattern
- [Example 05: Crash Recovery](05-crash-recovery.md) -- demonstrates stop,
  inspect persisted data, resume, and verify state restoration
