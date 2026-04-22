"""Nagra-backed ``EventStore`` implementation.

Implements the nine :class:`~akgentic.team.ports.EventStore` Protocol methods
against PostgreSQL using Nagra's :class:`~nagra.Transaction` wrapper. Each
public method opens its own transaction (per-method ownership);
:meth:`NagraEventStore.delete_team` is the one exception that spans a single
transaction across three ``DELETE`` statements for atomic cascade semantics.

The class satisfies the ``EventStore`` Protocol via structural subtyping — it
does NOT inherit from the Protocol explicitly (mirrors ``YamlEventStore`` and
``MongoEventStore``).

Implements ADR-15 Nagra-based PostgreSQL EventStore §§2, 3, 4, 8, 9, 10.
"""

from __future__ import annotations

import json
import uuid

from nagra import Transaction  # type: ignore[import-untyped]

from akgentic.team.models import AgentStateSnapshot, PersistedEvent, Process
from akgentic.team.repositories.postgres._queries import decode_jsonb_column


class NagraEventStore:
    """Nagra-backed ``EventStore`` implementation.

    Constructor calls :func:`_ensure_schema_loaded` so instances are always
    safe to build, but does NOT call :func:`init_db` — operators must
    invoke ``python -m akgentic.team.scripts.init_db`` once per deployment.

    Args:
        conn_string: Nagra-compatible Postgres connection string used to
            open per-method transactions.
    """

    def __init__(self, conn_string: str) -> None:
        # Local import keeps the constructor cheap and avoids a circular
        # import on package initialisation (``__init__`` re-exports this
        # class after defining ``_ensure_schema_loaded``).
        from akgentic.team.repositories.postgres import _ensure_schema_loaded

        _ensure_schema_loaded()
        self._conn_string = conn_string

    # --- team process (team_process_entries) -------------------------------

    def save_team(self, process: Process) -> None:
        """Upsert a team process snapshot keyed by ``team_id``."""
        data = json.dumps(process.model_dump())
        with Transaction(self._conn_string) as trn:
            trn.execute(
                "INSERT INTO team_process_entries (id, data) VALUES (%s, %s) "
                "ON CONFLICT (id) DO UPDATE SET data = EXCLUDED.data",
                (str(process.team_id), data),
            )

    def load_team(self, team_id: uuid.UUID) -> Process | None:
        """Load a team process snapshot by id; return ``None`` if absent."""
        with Transaction(self._conn_string) as trn:
            cursor = trn.execute(
                "SELECT data FROM team_process_entries WHERE id = %s",
                (str(team_id),),
            )
            row = cursor.fetchone()
        if row is None:
            return None
        return Process.model_validate(decode_jsonb_column(row[0]))

    def list_teams(self) -> list[Process]:
        """Return every persisted team process. Order is unspecified."""
        with Transaction(self._conn_string) as trn:
            cursor = trn.execute("SELECT data FROM team_process_entries")
            rows = cursor.fetchall()
        return [Process.model_validate(decode_jsonb_column(r[0])) for r in rows]

    def delete_team(self, team_id: uuid.UUID) -> None:
        """Cascade-delete a team across all three tables in ONE transaction.

        Order (dependency-safe): ``agent_state_entries`` → ``event_entries``
        → ``team_process_entries``. Idempotent: calling twice for the same
        id is a no-op on the second call (matches YAML / Mongo semantics).
        """
        tid = str(team_id)
        with Transaction(self._conn_string) as trn:
            trn.execute(
                "DELETE FROM agent_state_entries WHERE team_id = %s", (tid,)
            )
            trn.execute("DELETE FROM event_entries WHERE team_id = %s", (tid,))
            trn.execute(
                "DELETE FROM team_process_entries WHERE id = %s", (tid,)
            )

    # --- events (event_entries) -------------------------------------------

    def save_event(self, event: PersistedEvent) -> None:
        """Append a single event. No upsert — events are immutable."""
        data = json.dumps(event.model_dump())
        with Transaction(self._conn_string) as trn:
            trn.execute(
                "INSERT INTO event_entries (team_id, sequence, data) "
                "VALUES (%s, %s, %s)",
                (str(event.team_id), event.sequence, data),
            )

    def load_events(self, team_id: uuid.UUID) -> list[PersistedEvent]:
        """Return all events for a team ordered by ``sequence`` ASC."""
        with Transaction(self._conn_string) as trn:
            cursor = trn.execute(
                "SELECT data FROM event_entries WHERE team_id = %s "
                "ORDER BY sequence ASC",
                (str(team_id),),
            )
            rows = cursor.fetchall()
        return [PersistedEvent.model_validate(decode_jsonb_column(r[0])) for r in rows]

    def get_max_sequence(self, team_id: uuid.UUID) -> int:
        """Return the largest sequence for a team, or ``0`` if empty.

        Uses ``COALESCE(MAX(sequence), 0)`` so the empty-team case is a
        single round-trip and the method always returns an ``int`` (never
        ``None``).
        """
        with Transaction(self._conn_string) as trn:
            cursor = trn.execute(
                "SELECT COALESCE(MAX(sequence), 0) FROM event_entries "
                "WHERE team_id = %s",
                (str(team_id),),
            )
            row = cursor.fetchone()
        assert row is not None  # aggregate always returns one row
        result: int = row[0]
        return result

    # --- agent states (agent_state_entries) --------------------------------

    def save_agent_state(self, snapshot: AgentStateSnapshot) -> None:
        """Upsert an agent state snapshot keyed by ``(team_id, agent_id)``."""
        data = json.dumps(snapshot.model_dump())
        with Transaction(self._conn_string) as trn:
            trn.execute(
                "INSERT INTO agent_state_entries (team_id, agent_id, data) "
                "VALUES (%s, %s, %s) "
                "ON CONFLICT (team_id, agent_id) DO UPDATE SET data = EXCLUDED.data",
                (str(snapshot.team_id), snapshot.agent_id, data),
            )

    def load_agent_states(self, team_id: uuid.UUID) -> list[AgentStateSnapshot]:
        """Return every agent-state snapshot for a team. Order unspecified."""
        with Transaction(self._conn_string) as trn:
            cursor = trn.execute(
                "SELECT data FROM agent_state_entries WHERE team_id = %s",
                (str(team_id),),
            )
            rows = cursor.fetchall()
        return [
            AgentStateSnapshot.model_validate(decode_jsonb_column(r[0]))
            for r in rows
        ]
