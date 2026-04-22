# Crash Recovery -- Stop, Inspect, Resume, Verify

Companion guide for `05_crash_recovery.py`. Covers the recovery protocol,
TeamRestorer's 3-phase process, state restoration, and address handling.

## Concepts

### TeamRestorer's 3-Phase Protocol

When `team_manager.resume_team(team_id)` is called, `TeamRestorer` executes:

1. **Load**: Read all persisted events and agent state snapshots from EventStore.
2. **Rebuild**: Recreate the Orchestrator and agents from StartMessage/StopMessage
   pairs. Agents that were started but not stopped are rebuilt. Agent state
   snapshots are restored via `proxy.init_state(snapshot.state)`.
3. **Replay**: All persisted events are replayed through the orchestrator via
   `orchestrator.restore_message()` to reconstruct message history.

### StartMessage/StopMessage Pairs

The restorer determines which agents to rebuild by filtering the event log:

- Collect all `StartMessage` events (each represents an agent spawn)
- Collect all `StopMessage` events (each represents an agent teardown)
- Agents whose `agent_id` has a `StartMessage` but no `StopMessage` are "live"

### Agent State Restoration

For each rebuilt agent that has an `AgentStateSnapshot` in the store:

```python
proxy = actor_system.proxy_ask(addr, Akgent)
proxy.init_state(snapshot.state)
```

This restores the agent's state to its last persisted value without replaying
all events from scratch. Event replay then reconstructs the orchestrator's
message history.

### LLM Context Reconstruction

LLM conversation history is NOT stored separately. It lives in the
orchestrator's message list, which is rebuilt by replaying all persisted
events through `orchestrator.restore_message()`.

### Address Handling on Resume

The restorer preserves `agent_id` (logical identity) across stop/resume
boundaries. The same UUID is passed to `actor_system.createActor()` during
rebuild. However, the underlying Pykka `ActorRef` is a new object (new
thread), and the `ActorAddress` wrapper is a new instance.

- `team_id`: Preserved (same UUID)
- `agent_id` per agent: Preserved (restorer reuses original ID)
- Pykka `ActorRef`: New object (new thread in new actor system context)

### The `restoring=True` Flag

During rebuild, agents are created with `restoring=True`, and
`PersistenceSubscriber.set_restoring(True)` is called. This suppresses:

- Normal telemetry from agents (prevents duplicate StartMessages)
- Event persistence during replay (prevents re-persisting old events)

After replay completes, `orchestrator.end_restoration()` and
`persistence_sub.set_restoring(False)` resume normal operation.

## Key API Patterns

```python
# Resume a stopped team
new_runtime = team_manager.resume_team(team_id)

# The new runtime has the same team_id
assert new_runtime.id == team_id

# Addresses are new objects but preserve agent_id
assert new_runtime.addrs["leader"].agent_id == old_addrs["leader"].agent_id
assert new_runtime.addrs["leader"] is not old_addrs["leader"]

# Events persisted before stop continue from where they left off
events_after = event_store.load_events(team_id)
assert len(events_after) > len(events_before)

# PersistenceSubscriber restoring flag management
persistence_sub.set_restoring(True)   # suppress writes during replay
persistence_sub.set_restoring(False)  # resume normal persistence
```

## Common Pitfalls

1. **Never cache ActorAddress objects across stop/resume boundaries.** While
   `agent_id` is preserved, the `ActorAddress` wrapper and its internal
   `ActorRef` are new objects after resume. Code that holds references to
   old addresses will have stale pointers to dead Pykka actors.

2. **StartMessage seeding is required before resume.** The restorer needs
   `StartMessage` events in the store to know which agents to rebuild. If
   these are missing (because agents were spawned without orchestrator
   references), `resume_team()` will fail or rebuild an incomplete team.
   Use the `seed_start_events()` helper pattern from examples 03 and 04.

3. **State snapshots are optional.** Agents that never call
   `notify_state_change()` will not have snapshots. They still get rebuilt
   (from `StartMessage` events), but their state will be the default
   `BaseState()` after resume, not whatever they had before stop.

4. **`time.sleep()` is still needed after `runtime.send()` on resumed teams.**
   Pykka actors process messages in their own threads. Sleep gives the
   actor thread time to process before the main thread inspects state.

## See Also

- [Example 04: Event Sourcing](04-event-sourcing.md) -- covers persistence
  internals, PersistenceSubscriber, YAML file layout
- Example 06 (future): MongoDB backend for production persistence
