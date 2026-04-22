# Team Factory -- Building a Running Team

## Concepts Covered

- **TeamFactory as static builder**: `TeamFactory.build()` is a static method that takes a `TeamCard`, an `ActorSystem`, and optional subscribers, then spawns all actors and returns a `TeamRuntime` handle. No instance of `TeamFactory` is needed.

- **TeamRuntime persistent vs ephemeral fields**: `TeamRuntime` stores persistent actor addresses (`id`, `orchestrator_addr`, `entry_addr`, `addrs`, `supervisor_addrs`) that survive serialization. Ephemeral fields (proxies like `_orchestrator_proxy`, `_entry_proxy`) are rebuilt automatically in `model_post_init` from the persistent addresses.

- **ActorSystem ownership**: The `ActorSystem` hosts all actor threads. It is passed to `TeamFactory.build()` and stored in `TeamRuntime` (excluded from serialization). The caller owns the `ActorSystem` and must call `shutdown()` to clean up.

- **Subscriber list**: `subscribers` are `EventSubscriber` instances registered with the orchestrator during build. They receive team events for logging, persistence, or monitoring.

- **Rollback on partial failure**: If any agent spawn fails during `build()`, all already-spawned actors are torn down before the exception is re-raised. This prevents orphaned actors.

## Key API Patterns

```python
from akgentic.core.actor_system_impl import ActorSystem
from akgentic.team.factory import TeamFactory

# Build a running team
actor_system = ActorSystem()
try:
    runtime = TeamFactory.build(
        team_card=team_card,
        actor_system=actor_system,
        subscribers=[],
    )

    # Inspect the runtime
    print(runtime.id)                  # UUID
    print(runtime.entry_addr)          # ActorAddress of entry point
    print(runtime.addrs)               # dict[str, ActorAddress]

    # Broadcast to supervisors (agents with subordinates)
    runtime.send("Hello team!")

    # Directed message to a specific agent by name
    runtime.send_to("worker", "Task for you!")

finally:
    actor_system.shutdown()
```

## Common Pitfalls

- **Entry point must have `headcount=1`**: `TeamFactory.build()` raises `ValueError` if `entry_point.headcount != 1`. The entry point is the single external interface to the team.

- **`ActorSystem.shutdown()` must be called**: Actors run on background threads. Forgetting to shut down the `ActorSystem` leaves threads running. Always use `try/finally`.

- **TeamRuntime proxies are ephemeral**: The proxy objects (`_orchestrator_proxy`, `_entry_proxy`, etc.) are `PrivateAttr` fields rebuilt from addresses in `model_post_init`. After deserialization, they are automatically reconstructed -- but only if an `ActorSystem` with the same actors is available.

- **Messages are processed asynchronously**: `runtime.send()` returns immediately. Use `time.sleep()` if you need to see output before shutdown.

## See Also

- [Example 01: Team Definition](./01-team-definition.md) -- constructing the TeamCard used here.
- Example 03: TeamManager Lifecycle (coming soon) -- managing team create/stop/resume with persistence.
