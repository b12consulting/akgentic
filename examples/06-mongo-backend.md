# MongoDB Backend -- MongoEventStore with Backend Portability

## What This Example Demonstrates

This example runs the **same create/stop/resume/delete lifecycle** as [example 03](03-team-manager-lifecycle.md), but replaces `YamlEventStore` with `MongoEventStore` backed by `mongomock`. The key takeaway: **only the EventStore constructor changes** -- everything else (TeamManager, TeamCard, lifecycle operations) remains identical.

## Concepts Covered

### EventStore Protocol and Structural Subtyping

`MongoEventStore` implements the same `EventStore` Protocol as `YamlEventStore`, but does **not** inherit from it. Python's structural subtyping (duck typing formalized via `typing.Protocol`) means any class that implements the required methods satisfies the protocol -- no explicit inheritance needed.

```python
# Both satisfy EventStore Protocol without inheriting from it:
class YamlEventStore:
    def save_team(self, process: Process) -> None: ...
    def load_team(self, team_id: UUID) -> Process | None: ...
    # ... all other EventStore methods

class MongoEventStore:
    def save_team(self, process: Process) -> None: ...
    def load_team(self, team_id: UUID) -> Process | None: ...
    # ... all other EventStore methods
```

### Backend Portability

`TeamManager` accepts any object satisfying the `EventStore` protocol. Swapping backends requires changing **only** the EventStore constructor:

```python
# YAML backend:
event_store = YamlEventStore(Path(tmp_dir))

# MongoDB backend:
client = mongomock.MongoClient()
db = client["akgentic_example"]
event_store = MongoEventStore(db)

# TeamManager works identically with either:
team_manager = TeamManager(actor_system=actor_system, event_store=event_store)
```

### Using mongomock for Zero-Infrastructure Testing

`mongomock` is an in-memory mock of `pymongo` that requires no MongoDB server. It implements the same pymongo API, making it ideal for:

- Unit and integration tests
- Examples and demos
- CI pipelines without MongoDB service containers

```python
import mongomock
client = mongomock.MongoClient()
db = client["test_db"]
store = MongoEventStore(db)
```

### MongoDB Collection Layout

MongoEventStore uses three collections:

| Collection | Purpose | Key Strategy |
|---|---|---|
| `teams` | One document per team (Process metadata) | Upsert by `team_id` |
| `events` | One document per event (append-only) | Indexed by `(team_id, sequence)` |
| `agent_states` | One document per agent per team | Unique index on `(team_id, agent_id)` |

## Key API Patterns

### MongoEventStore Constructor

Single argument -- a pymongo `Database` instance:

```python
MongoEventStore(db)  # db is a pymongo.database.Database
```

Collections and indexes are created automatically on initialization.

### Index Strategy

- `events`: compound index on `(team_id, sequence)` for efficient ordered retrieval
- `agent_states`: unique compound index on `(team_id, agent_id)` for upsert-by-identity

### Serialization

- Pydantic models are serialized via `model_dump()` for writes
- Documents are deserialized via `model_validate()` for reads
- UUIDs are stored as strings in MongoDB documents
- `_id` fields (added by MongoDB) are stripped before Pydantic validation

### Delete Cleanup

`delete_team(team_id)` removes documents from **all three collections**, leaving no orphaned data.

## Common Pitfalls

1. **Missing pymongo dependency**: `MongoEventStore` requires `pymongo` at runtime. Install via:
   ```bash
   pip install akgentic-team[mongo]
   ```
   To run this example, you also need `mongomock` (included in the `[dev]` extra).

2. **mongomock is dev-only**: `mongomock` is a test/dev dependency. Production deployments use real `pymongo` with a MongoDB server.

3. **MongoDB `_id` fields**: MongoDB automatically adds `_id` to every document. The MongoEventStore implementation strips these before Pydantic validation to avoid schema mismatches.

4. **The `[mongo]` extra only provides MongoEventStore**: TeamManager, TeamFactory, TeamRestorer, and all other components work with **any** EventStore implementation. The optional extra only adds the MongoDB-specific backend.

## Cross-References

- [Example 03: Team Manager Lifecycle](03-team-manager-lifecycle.md) -- Same lifecycle operations with YamlEventStore
- [Example 05: Crash Recovery](05-crash-recovery.md) -- Crash recovery works with any EventStore backend
