"""MongoEventStore: MongoDB-backed EventStore via [mongo] optional extra.

Persists team data to MongoDB collections using pymongo. Satisfies the
EventStore protocol via structural subtyping (no explicit inheritance).

Collection layout::

    teams              # One document per team (Process metadata) -- upsert by team_id
    events             # One document per event -- append-only, indexed by (team_id, sequence)
    agent_states       # One document per agent per team -- upsert by (team_id, agent_id)
"""

from __future__ import annotations

import logging
import uuid
from typing import TYPE_CHECKING

try:
    import pymongo  # noqa: F401
except ImportError as exc:
    raise ImportError(
        "pymongo is required for MongoEventStore. "
        "Install with: pip install akgentic-team[mongo]"
    ) from exc

from pymongo.errors import PyMongoError

from akgentic.team.models import AgentStateSnapshot, PersistedEvent, Process
from akgentic.team.ports import EventNotFoundError

if TYPE_CHECKING:
    import pymongo.collection
    import pymongo.database

logger = logging.getLogger(__name__)


class MongoEventStore:
    """MongoDB-backed EventStore using pymongo collections.

    Satisfies the ``EventStore`` protocol via structural subtyping without
    inheriting from it. Uses three collections: ``teams`` (upsert by team_id),
    ``events`` (append-only, indexed by team_id + sequence), and
    ``agent_states`` (upsert by team_id + agent_id).

    Args:
        db: A pymongo Database instance connected to the target MongoDB server.
    """

    def __init__(self, db: pymongo.database.Database) -> None:  # type: ignore[type-arg]
        self._db = db
        self._teams: pymongo.collection.Collection = db["teams"]  # type: ignore[type-arg]
        self._events: pymongo.collection.Collection = db["events"]  # type: ignore[type-arg]
        self._agent_states: pymongo.collection.Collection = db["agent_states"]  # type: ignore[type-arg]

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
        # ADR-21 §5: backs the load_events(after_event_id=...) anchor lookup.
        # Single-field on the nested path, NOT compound with team_id: Cosmos for
        # MongoDB rejects compound indexes on nested paths unless the account sets
        # EnableUniqueCompoundNestedDocs, and event.id is a uuid4 — already maximally
        # selective, so leading with team_id buys nothing for this equality lookup.
        # Not unique — a unique index would turn a read-path ambiguity into a
        # write-path DuplicateKeyError on save_event.
        # Guarded: the index is a performance optimization, not a correctness
        # requirement. If the backend rejects the spec, the anchor lookup degrades
        # to a collection scan and load_events still returns the right events —
        # which beats failing construction and refusing to start the process.
        try:
            self._events.create_index("event.id", name="events_event_id_idx")
        except PyMongoError:
            logger.warning(
                "Could not create index 'events_event_id_idx' on '%s.event.id'; "
                "load_events(after_event_id=...) anchor lookups will fall back to a "
                "collection scan and degrade on large event logs. Results stay correct.",
                self._events.name,
                exc_info=True,
            )
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

    def load_events(
        self, team_id: uuid.UUID, after_event_id: uuid.UUID | None = None
    ) -> list[PersistedEvent]:
        """Load persisted events for a team, ordered by sequence.

        Both the anchor resolve and the ``sequence > N`` range filter run in
        MongoDB — the anchor via an indexed, projected ``find_one``, the range
        as a ``$gt`` clause on the find (ADR-21 §5).

        Args:
            team_id: Unique identifier of the team.
            after_event_id: If provided, return only events after the matching
                event — anchor excluded. If ``None`` (default), the full log.

        Returns:
            List of PersistedEvent ordered by sequence, or empty list if none.

        Raises:
            EventNotFoundError: If ``after_event_id`` does not resolve to an
                event of this team.
        """
        query: dict[str, object] = {"team_id": str(team_id)}
        if after_event_id is not None:
            query["sequence"] = {"$gt": self._resolve_anchor_sequence(team_id, after_event_id)}
        cursor = self._events.find(query).sort("sequence", 1)
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

    def _resolve_anchor_sequence(self, team_id: uuid.UUID, after_event_id: uuid.UUID) -> int:
        """Return the ``sequence`` of the cursor anchor, or raise if absent.

        ``event.id`` is persisted as a string, so both ids are coerced with
        ``str()``: a raw ``uuid.UUID`` would be BSON-encoded as Binary
        subtype-4 and match nothing. The projection keeps this to a single
        indexed lookup returning one integer — the document is never hydrated.

        Raises:
            EventNotFoundError: If the anchor is not an event of this team.
        """
        anchor = self._events.find_one(
            {"team_id": str(team_id), "event.id": str(after_event_id)},
            projection={"sequence": 1, "_id": 0},
        )
        if anchor is None:
            raise EventNotFoundError(f"Event {after_event_id} not found for team {team_id}")
        sequence: int = anchor["sequence"]
        return sequence

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
