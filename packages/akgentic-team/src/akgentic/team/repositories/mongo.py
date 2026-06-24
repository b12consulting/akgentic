"""MongoEventStore: MongoDB-backed EventStore via [mongo] optional extra.

Persists team data to MongoDB collections using pymongo. Satisfies the
EventStore protocol via structural subtyping (no explicit inheritance).

Collection layout::

    teams              # One document per team (Process metadata) -- upsert by team_id
    events             # One document per event -- append-only, indexed by (team_id, sequence)
    agent_states       # One document per agent per team -- upsert by (team_id, agent_id)

Each collection name defaults to the value shown above but can be overridden
via an environment variable so several deployments can share one MongoDB
database without colliding:

    teams         -> MONGO_TEAMS_COLLECTION
    events        -> MONGO_EVENTS_COLLECTION
    agent_states  -> MONGO_AGENT_STATES_COLLECTION

An env var that is unset or empty falls back to the default name shown above.
"""

from __future__ import annotations

import logging
import os
import uuid
from typing import TYPE_CHECKING

try:
    import pymongo  # noqa: F401
except ImportError as exc:
    raise ImportError(
        "pymongo is required for MongoEventStore. "
        "Install with: pip install akgentic-team[mongo]"
    ) from exc

from akgentic.team.models import AgentStateSnapshot, PersistedEvent, Process

if TYPE_CHECKING:
    import pymongo.collection
    import pymongo.database

logger = logging.getLogger(__name__)

# Environment variables that override the MongoDB collection names, each
# falling back to the hardcoded default below when unset or empty. Letting
# each collection be renamed independently lets multiple akgentic-team
# deployments share a single MongoDB database without colliding.
_TEAMS_COLLECTION_ENV = "MONGO_TEAMS_COLLECTION"
_EVENTS_COLLECTION_ENV = "MONGO_EVENTS_COLLECTION"
_AGENT_STATES_COLLECTION_ENV = "MONGO_AGENT_STATES_COLLECTION"

_DEFAULT_TEAMS_COLLECTION = "teams"
_DEFAULT_EVENTS_COLLECTION = "events"
_DEFAULT_AGENT_STATES_COLLECTION = "agent_states"


class MongoEventStore:
    """MongoDB-backed EventStore using pymongo collections.

    Satisfies the ``EventStore`` protocol via structural subtyping without
    inheriting from it. Uses three collections: ``teams`` (upsert by team_id),
    ``events`` (append-only, indexed by team_id + sequence), and
    ``agent_states`` (upsert by team_id + agent_id).

    Each collection name defaults to the value shown above but can be
    overridden via an environment variable — ``MONGO_TEAMS_COLLECTION``,
    ``MONGO_EVENTS_COLLECTION``, and ``MONGO_AGENT_STATES_COLLECTION`` — so
    several deployments can share one MongoDB database. An unset or empty
    variable falls back to its default. Indexes are created against the
    resolved collections, so renamed collections are indexed identically.

    Args:
        db: A pymongo Database instance connected to the target MongoDB server.
    """

    def __init__(self, db: pymongo.database.Database) -> None:  # type: ignore[type-arg]
        self._db = db
        teams_name = os.environ.get(_TEAMS_COLLECTION_ENV) or _DEFAULT_TEAMS_COLLECTION
        events_name = os.environ.get(_EVENTS_COLLECTION_ENV) or _DEFAULT_EVENTS_COLLECTION
        agent_states_name = (
            os.environ.get(_AGENT_STATES_COLLECTION_ENV) or _DEFAULT_AGENT_STATES_COLLECTION
        )
        self._teams: pymongo.collection.Collection = db[teams_name]  # type: ignore[type-arg]
        self._events: pymongo.collection.Collection = db[events_name]  # type: ignore[type-arg]
        self._agent_states: pymongo.collection.Collection = db[agent_states_name]  # type: ignore[type-arg]

        # Create indexes for efficient queries
        self._events.create_index([("team_id", 1), ("sequence", 1)])
        self._agent_states.create_index(
            [("team_id", 1), ("agent_id", 1)], unique=True
        )
        # ADR-16 §5: B-tree index backing the ``list_teams(user_id=...)`` push-down.
        # ``create_index`` is idempotent — re-running the constructor against the
        # same database returns silently when an index with the same key spec
        # already exists, so this is safe across redeploys.
        self._teams.create_index("user_id", name="teams_user_id_idx")
        logger.debug("Initialized MongoEventStore with database '%s'", db.name)

    def save_team(self, process: Process) -> None:
        """Persist a team process snapshot via upsert.

        Serializes the Process with ``model_dump()`` and upserts into the
        ``teams`` collection keyed by ``team_id``.

        Args:
            process: The team process snapshot to persist.
        """
        doc = process.model_dump()
        self._teams.replace_one(
            {"team_id": str(process.team_id)},
            doc,
            upsert=True,
        )
        logger.debug("Saved team %s", process.team_id)

    def load_team(self, team_id: uuid.UUID) -> Process | None:
        """Load a team process snapshot by ID.

        Queries the ``teams`` collection by ``team_id``. Returns None if no
        document is found or if the stored document is corrupted.

        Args:
            team_id: Unique identifier of the team.

        Returns:
            The deserialized Process, or None if not found.
        """
        doc = self._teams.find_one({"team_id": str(team_id)})
        if doc is None:
            return None
        doc.pop("_id", None)
        try:
            process = Process.model_validate(doc)
        except (ValueError, TypeError) as exc:
            logger.error("Corrupted team document for team %s: %s", team_id, exc)
            return None
        logger.debug("Loaded team %s", team_id)
        return process

    def list_teams(self, user_id: str | None = None) -> list[Process]:
        """Load team process snapshots from the teams collection.

        When ``user_id`` is provided, the filter is pushed down into the
        Mongo ``find`` call as ``{"user_id": user_id}`` and runs in MongoDB
        against the ``teams_user_id_idx`` B-tree index (created in
        ``__init__``) — not in Python after hydration. When ``user_id`` is
        ``None``, the call issues ``find({})`` and returns every team.
        Corrupted documents are skipped with a warning. See ADR-16 §5.

        Args:
            user_id: If provided, return only snapshots whose
                ``Process.user_id`` matches via a Mongo backend filter.
                If ``None`` (default), return all snapshots. See ADR-16 §1.

        Returns:
            List of loadable Process snapshots (filtered by ``user_id``
            at the database level when provided).
        """
        query: dict[str, str] = {"user_id": user_id} if user_id is not None else {}
        teams: list[Process] = []
        for doc in self._teams.find(query):
            doc.pop("_id", None)
            try:
                teams.append(Process.model_validate(doc))
            except (ValueError, TypeError) as exc:
                logger.warning("Skipping corrupted team document: %s", exc)
        logger.debug("Listed %d teams", len(teams))
        return teams

    def save_event(self, event: PersistedEvent) -> None:
        """Persist a single domain event (append-only).

        Serializes the PersistedEvent with ``model_dump()`` and inserts into
        the ``events`` collection. Never upserts -- events are immutable.

        Args:
            event: The event to persist.
        """
        doc = event.model_dump()
        self._events.insert_one(doc)
        logger.debug("Saved event seq=%d for team %s", event.sequence, event.team_id)

    def load_events(self, team_id: uuid.UUID) -> list[PersistedEvent]:
        """Load all persisted events for a team, ordered by sequence.

        Queries the ``events`` collection by ``team_id`` and sorts by
        ``sequence`` ascending.

        Args:
            team_id: Unique identifier of the team.

        Returns:
            List of PersistedEvent ordered by sequence, or empty list if none.
        """
        cursor = self._events.find({"team_id": str(team_id)}).sort("sequence", 1)
        events: list[PersistedEvent] = []
        for doc in cursor:
            doc.pop("_id", None)
            try:
                events.append(PersistedEvent.model_validate(doc))
            except (ValueError, TypeError) as exc:
                logger.warning(
                    "Skipping corrupted event for team %s: %s", team_id, exc
                )
        logger.debug("Loaded %d events for team %s", len(events), team_id)
        return events

    def get_max_sequence(self, team_id: uuid.UUID) -> int:
        """Return the highest event sequence number for a team, or 0.

        Uses an efficient MongoDB query (sort + limit) to avoid loading
        all events into memory.

        Args:
            team_id: Unique identifier of the team.

        Returns:
            The highest sequence number, or 0 if no events exist.
        """
        doc = self._events.find_one(
            {"team_id": str(team_id)},
            sort=[("sequence", -1)],
            projection={"sequence": 1, "_id": 0},
        )
        if doc is None:
            return 0
        result: int = doc["sequence"]
        return result

    def save_agent_state(self, snapshot: AgentStateSnapshot) -> None:
        """Persist an agent state snapshot via upsert.

        Serializes the AgentStateSnapshot with ``model_dump()`` and upserts
        into the ``agent_states`` collection keyed by ``team_id`` + ``agent_id``.

        Args:
            snapshot: The agent state snapshot to persist.
        """
        doc = snapshot.model_dump()
        self._agent_states.replace_one(
            {"team_id": str(snapshot.team_id), "agent_id": snapshot.agent_id},
            doc,
            upsert=True,
        )
        logger.debug(
            "Saved agent state %s for team %s", snapshot.agent_id, snapshot.team_id
        )

    def load_agent_states(self, team_id: uuid.UUID) -> list[AgentStateSnapshot]:
        """Load all agent state snapshots for a team.

        Queries the ``agent_states`` collection by ``team_id``.

        Args:
            team_id: Unique identifier of the team.

        Returns:
            List of AgentStateSnapshot, or empty list if none.
        """
        cursor = self._agent_states.find({"team_id": str(team_id)})
        snapshots: list[AgentStateSnapshot] = []
        for doc in cursor:
            doc.pop("_id", None)
            try:
                snapshots.append(AgentStateSnapshot.model_validate(doc))
            except (ValueError, TypeError) as exc:
                logger.warning(
                    "Skipping corrupted agent state for team %s: %s", team_id, exc
                )
        logger.debug("Loaded %d agent states for team %s", len(snapshots), team_id)
        return snapshots

    def delete_team(self, team_id: uuid.UUID) -> None:
        """Delete all persisted data for a team from all three collections.

        Removes documents from ``teams``, ``events``, and ``agent_states``
        matching the given ``team_id``. If no documents exist, this is a no-op.

        Args:
            team_id: Unique identifier of the team to delete.
        """
        team_id_str = str(team_id)
        self._teams.delete_many({"team_id": team_id_str})
        self._events.delete_many({"team_id": team_id_str})
        self._agent_states.delete_many({"team_id": team_id_str})
        logger.debug("Deleted all data for team %s", team_id)
