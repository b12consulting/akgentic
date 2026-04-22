"""Port protocols: EventStore, ServiceRegistry, and NullServiceRegistry default."""

from __future__ import annotations

import uuid
from typing import Protocol, runtime_checkable

from akgentic.team.models import (
    AgentStateSnapshot,
    PersistedEvent,
    Process,
)


class EventStore(Protocol):
    """Typed persistence surface for team event sourcing.

    Implementations provide durable storage for events, team process state,
    and agent state snapshots. Concrete implementations (e.g. YamlEventStore,
    MongoEventStore) satisfy this protocol via structural subtyping.
    """

    def save_event(self, event: PersistedEvent) -> None:
        """Persist a single domain event.

        Called by PersistenceSubscriber on each event received from the orchestrator.
        """
        ...

    def load_events(self, team_id: uuid.UUID) -> list[PersistedEvent]:
        """Load all persisted events for a team.

        Called by TeamRestorer to replay events during team resumption.
        """
        ...

    def save_team(self, process: Process) -> None:
        """Persist the current team process snapshot.

        Called by PersistenceSubscriber to checkpoint team state.
        """
        ...

    def load_team(self, team_id: uuid.UUID) -> Process | None:
        """Load a team process snapshot by ID.

        Called by TeamManager to retrieve team state. Returns None if no
        snapshot exists for the given team ID.
        """
        ...

    def delete_team(self, team_id: uuid.UUID) -> None:
        """Delete all persisted data for a team.

        Called by TeamManager during team deletion to purge stored state.
        """
        ...

    def save_agent_state(self, snapshot: AgentStateSnapshot) -> None:
        """Persist an agent state snapshot.

        Called by PersistenceSubscriber to checkpoint individual agent state.
        """
        ...

    def list_teams(self) -> list[Process]:
        """Load all team process snapshots.

        Called by the CLI to enumerate all known teams.
        """
        ...

    def get_max_sequence(self, team_id: uuid.UUID) -> int:
        """Return the highest event sequence number for a team.

        Used by TeamManager.resume_team() to initialize PersistenceSubscriber
        so that new events continue monotonically after restore. Returns 0 if
        no events exist for the given team.

        Implementations backed by a database (e.g. MongoDB) SHOULD use an
        efficient query (e.g. ``find().sort("sequence", -1).limit(1)``)
        rather than loading all events into memory.
        """
        ...

    def load_agent_states(self, team_id: uuid.UUID) -> list[AgentStateSnapshot]:
        """Load all agent state snapshots for a team.

        Called by TeamRestorer to restore agent states during team resumption.
        """
        ...


@runtime_checkable
class ServiceRegistry(Protocol):
    """Service discovery port for multi-worker deployments.

    Tracks which worker instances are active and which teams are hosted
    on each instance. Implementations satisfy this protocol via structural
    subtyping. In single-process mode, use NullServiceRegistry.
    """

    def register_instance(self, instance_id: uuid.UUID) -> None:
        """Register a worker instance as active."""
        ...

    def deregister_instance(self, instance_id: uuid.UUID) -> None:
        """Remove a worker instance from the active set."""
        ...

    def register_team(self, instance_id: uuid.UUID, team_id: uuid.UUID) -> None:
        """Associate a team with a worker instance."""
        ...

    def deregister_team(self, instance_id: uuid.UUID, team_id: uuid.UUID) -> None:
        """Disassociate a team from a worker instance."""
        ...

    def find_team(self, team_id: uuid.UUID) -> uuid.UUID | None:
        """Find the worker instance hosting a team.

        Returns the instance UUID if found, None otherwise.
        """
        ...

    def get_active_instances(self) -> list[uuid.UUID]:
        """Return all currently active worker instance IDs."""
        ...


class NullServiceRegistry:
    """No-op service registry for single-process mode.

    Satisfies the ServiceRegistry protocol via structural subtyping without
    inheriting from it. All mutating methods are no-ops; queries return
    empty results.
    """

    def register_instance(self, instance_id: uuid.UUID) -> None:
        """No-op: single-process mode has no instance tracking."""
        pass

    def deregister_instance(self, instance_id: uuid.UUID) -> None:
        """No-op: single-process mode has no instance tracking."""
        pass

    def register_team(self, instance_id: uuid.UUID, team_id: uuid.UUID) -> None:
        """No-op: single-process mode has no team-to-instance mapping."""
        pass

    def deregister_team(self, instance_id: uuid.UUID, team_id: uuid.UUID) -> None:
        """No-op: single-process mode has no team-to-instance mapping."""
        pass

    def find_team(self, team_id: uuid.UUID) -> uuid.UUID | None:
        """Always returns None: no team routing in single-process mode."""
        return None

    def get_active_instances(self) -> list[uuid.UUID]:
        """Always returns empty list: no instance tracking in single-process mode."""
        return []
