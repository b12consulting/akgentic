"""Nagra/Postgres-backed repository for agent catalog entries.

Implements ADR-006 §2 (per-method Transaction ownership), §3 (two-column
JSONB table shape), §4 (predicate table — Agent rows), and §8 (error
handling parity with the Mongo backend). All SQL predicate construction is
delegated to :mod:`_queries`; raw user input is never interpolated into SQL
text — every variable is passed through as a psycopg ``%s`` bound parameter.
"""

from __future__ import annotations

import builtins
import json
import logging
from typing import TYPE_CHECKING

from nagra import Transaction  # type: ignore[import-untyped]
from psycopg.errors import UniqueViolation

from akgentic.catalog.models.agent import AgentEntry
from akgentic.catalog.models.errors import CatalogValidationError, EntryNotFoundError
from akgentic.catalog.repositories.base import AgentCatalogRepository
from akgentic.catalog.repositories.postgres._queries import (
    build_agent_where,
    decode_jsonb_column,
)

if TYPE_CHECKING:
    from akgentic.catalog.models.queries import AgentQuery

logger = logging.getLogger(__name__)

_list = builtins.list  # repository's list() method shadows the built-in


class NagraAgentCatalogRepository(AgentCatalogRepository):
    """Nagra/Postgres-backed agent catalog repository.

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
        logger.info("NagraAgentCatalogRepository initialised")

    def create(self, agent_entry: AgentEntry) -> str:
        """Persist a new agent entry.

        Inserts a row into ``agent_entries`` with ``id = agent_entry.id`` and
        ``data = agent_entry.model_dump()``. Translates psycopg's
        ``UniqueViolation`` to :class:`CatalogValidationError` so callers see
        the same exception type as the YAML and Mongo backends emit on a
        duplicate id.

        Args:
            agent_entry: The agent entry to create.

        Returns:
            The id of the created entry.

        Raises:
            CatalogValidationError: If an entry with the same id already exists.
        """
        data_json = json.dumps(agent_entry.model_dump())
        try:
            with Transaction(self._conn_string) as trn:
                trn.execute(
                    "INSERT INTO agent_entries (id, data) VALUES (%s, %s)",
                    (agent_entry.id, data_json),
                )
        except UniqueViolation:
            raise CatalogValidationError(
                [f"Entry with id '{agent_entry.id}' already exists"]
            )
        logger.debug("Created agent entry with id=%s", agent_entry.id)
        return agent_entry.id

    def get(self, id: str) -> AgentEntry | None:
        """Retrieve an agent entry by id.

        Args:
            id: The agent entry id.

        Returns:
            The agent entry, or ``None`` if no row matches.
        """
        with Transaction(self._conn_string) as trn:
            cursor = trn.execute(
                "SELECT data FROM agent_entries WHERE id = %s",
                (id,),
            )
            row = cursor.fetchone()
        if row is None:
            logger.debug("Agent entry not found: id=%s", id)
            return None
        return AgentEntry.model_validate(decode_jsonb_column(row[0]))

    def list(self) -> _list[AgentEntry]:
        """Return every agent entry (order unspecified)."""
        with Transaction(self._conn_string) as trn:
            cursor = trn.execute("SELECT data FROM agent_entries")
            rows = cursor.fetchall()
        entries = [AgentEntry.model_validate(decode_jsonb_column(r[0])) for r in rows]
        logger.debug("Listed %d agent entries", len(entries))
        return entries

    def search(self, query: AgentQuery) -> _list[AgentEntry]:
        """Filter agents by AND-ing all non-None query fields.

        Delegates predicate construction to :func:`_queries.build_agent_where`.
        When the helper signals "no filter" (an empty query), issues a bare
        ``SELECT`` — equivalent to :meth:`list`.

        Args:
            query: Query with optional filter fields.

        Returns:
            Matching agent entries (order unspecified).
        """
        where_sql, params = build_agent_where(query)
        if where_sql is None:
            sql = "SELECT data FROM agent_entries"
        else:
            sql = f"SELECT data FROM agent_entries WHERE {where_sql}"
        with Transaction(self._conn_string) as trn:
            cursor = trn.execute(sql, tuple(params))
            rows = cursor.fetchall()
        results = [AgentEntry.model_validate(decode_jsonb_column(r[0])) for r in rows]
        logger.debug("Search returned %d agent entries", len(results))
        return results

    def update(self, id: str, agent_entry: AgentEntry) -> None:
        """Update an existing agent entry.

        Args:
            id: The id of the entry to update.
            agent_entry: The new entry data.

        Raises:
            CatalogValidationError: If ``agent_entry.id`` does not match ``id``.
            EntryNotFoundError: If no entry with the given id exists.
        """
        if agent_entry.id != id:
            raise CatalogValidationError(
                [f"Entry id mismatch: expected '{id}', got '{agent_entry.id}'"]
            )
        data_json = json.dumps(agent_entry.model_dump())
        with Transaction(self._conn_string) as trn:
            cursor = trn.execute(
                "UPDATE agent_entries SET data = %s WHERE id = %s",
                (data_json, id),
            )
            row_count = cursor.native_cursor.rowcount
        if row_count == 0:
            raise EntryNotFoundError(f"Entry with id '{id}' not found")
        logger.debug("Updated agent entry with id=%s", id)

    def delete(self, id: str) -> None:
        """Delete an agent entry by id.

        Args:
            id: The id of the entry to delete.

        Raises:
            EntryNotFoundError: If no entry with the given id exists.
        """
        with Transaction(self._conn_string) as trn:
            cursor = trn.execute(
                "DELETE FROM agent_entries WHERE id = %s",
                (id,),
            )
            row_count = cursor.native_cursor.rowcount
        if row_count == 0:
            raise EntryNotFoundError(f"Entry with id '{id}' not found")
        logger.debug("Deleted agent entry with id=%s", id)
