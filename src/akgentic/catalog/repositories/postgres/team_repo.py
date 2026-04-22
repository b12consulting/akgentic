"""Nagra/Postgres-backed repository for team catalog entries.

Implements ADR-006 §2 (per-method Transaction ownership), §3 (two-column
JSONB table shape), §4 (predicate table — Team rows, with the ``agent_id``
field implemented as a Python post-filter rather than a SQL predicate for
behavioural parity with the Mongo backend's recursive tree walk), and §8
(error handling parity with the Mongo backend).
"""

from __future__ import annotations

import builtins
import json
import logging
from typing import TYPE_CHECKING

from nagra import Transaction  # type: ignore[import-untyped]
from psycopg.errors import UniqueViolation

from akgentic.catalog.models.errors import CatalogValidationError, EntryNotFoundError
from akgentic.catalog.models.team import TeamEntry, agent_in_members
from akgentic.catalog.repositories.base import TeamCatalogRepository
from akgentic.catalog.repositories.postgres._queries import (
    build_team_where,
    decode_jsonb_column,
)

if TYPE_CHECKING:
    from akgentic.catalog.models.queries import TeamQuery

logger = logging.getLogger(__name__)

_list = builtins.list  # repository's list() method shadows the built-in


class NagraTeamCatalogRepository(TeamCatalogRepository):
    """Nagra/Postgres-backed team catalog repository.

    Each method opens and closes its own ``Transaction(self._conn_string)``
    so callers never see a transaction object. The constructor performs no
    I/O beyond loading the shared Nagra schema (idempotent).
    """

    def __init__(self, conn_string: str) -> None:
        """Initialise with a Nagra connection string and load the shared schema.

        Args:
            conn_string: Nagra-compatible Postgres connection string.
        """
        from akgentic.catalog.repositories.postgres import _ensure_schema_loaded

        _ensure_schema_loaded()
        self._conn_string = conn_string
        logger.info("NagraTeamCatalogRepository initialised")

    def create(self, team_entry: TeamEntry) -> str:
        """Persist a new team entry.

        Translates psycopg's ``UniqueViolation`` to :class:`CatalogValidationError`
        so callers see the same exception type as the YAML and Mongo backends
        emit on a duplicate id.

        Args:
            team_entry: The team entry to create.

        Returns:
            The id of the created entry.

        Raises:
            CatalogValidationError: If an entry with the same id already exists.
        """
        data_json = json.dumps(team_entry.model_dump())
        try:
            with Transaction(self._conn_string) as trn:
                trn.execute(
                    "INSERT INTO team_entries (id, data) VALUES (%s, %s)",
                    (team_entry.id, data_json),
                )
        except UniqueViolation:
            raise CatalogValidationError(
                [f"Entry with id '{team_entry.id}' already exists"]
            )
        logger.debug("Created team entry with id=%s", team_entry.id)
        return team_entry.id

    def get(self, id: str) -> TeamEntry | None:
        """Retrieve a team entry by id.

        Args:
            id: The team entry id.

        Returns:
            The team entry, or ``None`` if no row matches.
        """
        with Transaction(self._conn_string) as trn:
            cursor = trn.execute(
                "SELECT data FROM team_entries WHERE id = %s",
                (id,),
            )
            row = cursor.fetchone()
        if row is None:
            logger.debug("Team entry not found: id=%s", id)
            return None
        return TeamEntry.model_validate(decode_jsonb_column(row[0]))

    def list(self) -> _list[TeamEntry]:
        """Return every team entry (order unspecified)."""
        with Transaction(self._conn_string) as trn:
            cursor = trn.execute("SELECT data FROM team_entries")
            rows = cursor.fetchall()
        entries = [TeamEntry.model_validate(decode_jsonb_column(r[0])) for r in rows]
        logger.debug("Listed %d team entries", len(entries))
        return entries

    def search(self, query: TeamQuery) -> _list[TeamEntry]:
        """Filter teams by AND-ing all non-None query fields.

        Applies server-side predicates (``id``, ``name``, ``description``) via
        :func:`_queries.build_team_where`, then post-filters the hydrated
        result set on ``agent_id`` via
        :func:`akgentic.catalog.models.team.agent_in_members` so the recursive
        member tree is walked — matching the Mongo backend's behaviour.

        A top-level-only JSONB containment predicate (e.g.
        ``data->'members' @> '[{"agent_id": ...}]'::jsonb``) would miss
        ``agent_id`` values nested inside sub-team ``members`` and is therefore
        incorrect; see story 15.3 Dev Notes §"The ``agent_id`` predicate —
        ADR vs. Mongo reality".

        Args:
            query: Query with optional filter fields.

        Returns:
            Matching team entries (order unspecified).
        """
        where_sql, params = build_team_where(query)
        if where_sql is None:
            sql = "SELECT data FROM team_entries"
        else:
            sql = f"SELECT data FROM team_entries WHERE {where_sql}"
        with Transaction(self._conn_string) as trn:
            cursor = trn.execute(sql, tuple(params))
            rows = cursor.fetchall()
        results = [TeamEntry.model_validate(decode_jsonb_column(r[0])) for r in rows]

        # Python post-filter for agent_id — walks the recursive member tree.
        if query.agent_id is not None:
            results = [t for t in results if agent_in_members(query.agent_id, t.members)]

        logger.debug("Search returned %d team entries", len(results))
        return results

    def update(self, id: str, team_entry: TeamEntry) -> None:
        """Update an existing team entry.

        Args:
            id: The id of the entry to update.
            team_entry: The new entry data.

        Raises:
            CatalogValidationError: If ``team_entry.id`` does not match ``id``.
            EntryNotFoundError: If no entry with the given id exists.
        """
        if team_entry.id != id:
            raise CatalogValidationError(
                [f"Entry id mismatch: expected '{id}', got '{team_entry.id}'"]
            )
        data_json = json.dumps(team_entry.model_dump())
        with Transaction(self._conn_string) as trn:
            cursor = trn.execute(
                "UPDATE team_entries SET data = %s WHERE id = %s",
                (data_json, id),
            )
            row_count = cursor.native_cursor.rowcount
        if row_count == 0:
            raise EntryNotFoundError(f"Entry with id '{id}' not found")
        logger.debug("Updated team entry with id=%s", id)

    def delete(self, id: str) -> None:
        """Delete a team entry by id.

        Args:
            id: The id of the entry to delete.

        Raises:
            EntryNotFoundError: If no entry with the given id exists.
        """
        with Transaction(self._conn_string) as trn:
            cursor = trn.execute(
                "DELETE FROM team_entries WHERE id = %s",
                (id,),
            )
            row_count = cursor.native_cursor.rowcount
        if row_count == 0:
            raise EntryNotFoundError(f"Entry with id '{id}' not found")
        logger.debug("Deleted team entry with id=%s", id)
