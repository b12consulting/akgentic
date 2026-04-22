"""Test fixtures for team service tests."""

from __future__ import annotations

import logging
import uuid
from typing import Any

from akgentic.team.models import AgentStateSnapshot, PersistedEvent, Process

logger = logging.getLogger(__name__)


class InMemoryEventStore:
    """Dict-backed EventStore for test isolation.

    Satisfies the EventStore protocol via structural subtyping.

    Events are round-trip serialized (model_dump/model_validate) to match
    real persistence behaviour: ActorAddressImpl is serialized to dict on
    save, then deserialized as ActorAddressProxy on load, preventing stale
    weak-ref errors when actors are stopped between save and load.
    """

    def __init__(self) -> None:
        self.events: list[PersistedEvent] = []
        self._event_dicts: list[dict[str, Any]] = []
        self.teams: dict[uuid.UUID, Process] = {}
        self.agent_states: dict[tuple[uuid.UUID, str], AgentStateSnapshot] = {}

    def save_event(self, event: PersistedEvent) -> None:
        """Persist a single domain event.

        Stores both the live object (for subscriber tests that inspect
        ``store.events`` directly) and a serialized dict (for load_events
        round-trip, matching real persistence behaviour).
        """
        self.events.append(event)
        try:
            self._event_dicts.append(event.model_dump())
        except Exception:
            # Subscriber tests may use mock senders that fail serialization;
            # those tests never call load_events, so a missing dict is safe.
            logger.debug("Event serialization skipped (mock sender): %s", type(event.event))

    def load_events(self, team_id: uuid.UUID) -> list[PersistedEvent]:
        """Load all persisted events for a team (deserialized from dicts).

        Round-trips through model_dump/model_validate so that
        ActorAddressImpl becomes ActorAddressProxy, matching real
        persistence backends (YAML, MongoDB).
        """
        tid = str(team_id)
        return [PersistedEvent.model_validate(d) for d in self._event_dicts if d["team_id"] == tid]

    def save_team(self, process: Process) -> None:
        """Persist team process snapshot."""
        self.teams[process.team_id] = process

    def load_team(self, team_id: uuid.UUID) -> Process | None:
        """Load a team process snapshot by ID."""
        return self.teams.get(team_id)

    def delete_team(self, team_id: uuid.UUID) -> None:
        """Delete all persisted data for a team."""
        self.teams.pop(team_id, None)
        self.events = [e for e in self.events if e.team_id != team_id]
        tid = str(team_id)
        self._event_dicts = [d for d in self._event_dicts if d["team_id"] != tid]
        self.agent_states = {k: v for k, v in self.agent_states.items() if k[0] != team_id}

    def save_agent_state(self, snapshot: AgentStateSnapshot) -> None:
        """Persist an agent state snapshot."""
        self.agent_states[(snapshot.team_id, snapshot.agent_id)] = snapshot

    def list_teams(self) -> list[Process]:
        """Load all team process snapshots."""
        return list(self.teams.values())

    def get_max_sequence(self, team_id: uuid.UUID) -> int:
        """Return the highest event sequence number for a team, or 0."""
        events = self.load_events(team_id)
        return max((e.sequence for e in events), default=0)

    def load_agent_states(self, team_id: uuid.UUID) -> list[AgentStateSnapshot]:
        """Load all agent state snapshots for a team."""
        return [v for k, v in self.agent_states.items() if k[0] == team_id]
