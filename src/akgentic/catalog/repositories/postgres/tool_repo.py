"""Nagra/Postgres-backed repository for tool catalog entries.

Implements ADR-006 §2 (per-method Transaction ownership), §3 (two-column
JSONB table shape), §4 (predicate table — Tool rows, adjusted for the nested
``tool`` JSON path), and §8 (error handling parity with the Mongo backend).
"""

from __future__ import annotations

import builtins
import json
import logging
from typing import TYPE_CHECKING

from nagra import Transaction  # type: ignore[import-untyped]
from psycopg.errors import UniqueViolation

from akgentic.catalog.models.errors import CatalogValidationError, EntryNotFoundError
from akgentic.catalog.models.tool import ToolEntry
from akgentic.catalog.repositories.base import ToolCatalogRepository
from akgentic.catalog.repositories.postgres._queries import (
    build_tool_where,
    decode_jsonb_column,
)

if TYPE_CHECKING:
    from akgentic.catalog.models.queries import ToolQuery

logger = logging.getLogger(__name__)

_list = builtins.list  # repository's list() method shadows the built-in


class NagraToolCatalogRepository(ToolCatalogRepository):
    """Nagra/Postgres-backed tool catalog repository.

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
        logger.info("NagraToolCatalogRepository initialised")

    def create(self, tool_entry: ToolEntry) -> str:
        """Persist a new tool entry.

        Translates psycopg's ``UniqueViolation`` to :class:`CatalogValidationError`
        so callers see the same exception type as the YAML and Mongo backends
        emit on a duplicate id.

        Args:
            tool_entry: The tool entry to create.

        Returns:
            The id of the created entry.

        Raises:
            CatalogValidationError: If an entry with the same id already exists.
        """
        data_json = json.dumps(tool_entry.model_dump())
        try:
            with Transaction(self._conn_string) as trn:
                trn.execute(
                    "INSERT INTO tool_entries (id, data) VALUES (%s, %s)",
                    (tool_entry.id, data_json),
                )
        except UniqueViolation:
            raise CatalogValidationError(
                [f"Entry with id '{tool_entry.id}' already exists"]
            )
        logger.debug("Created tool entry with id=%s", tool_entry.id)
        return tool_entry.id

    def get(self, id: str) -> ToolEntry | None:
        """Retrieve a tool entry by id.

        Args:
            id: The tool entry id.

        Returns:
            The tool entry, or ``None`` if no row matches.
        """
        with Transaction(self._conn_string) as trn:
            cursor = trn.execute(
                "SELECT data FROM tool_entries WHERE id = %s",
                (id,),
            )
            row = cursor.fetchone()
        if row is None:
            logger.debug("Tool entry not found: id=%s", id)
            return None
        return ToolEntry.model_validate(decode_jsonb_column(row[0]))

    def list(self) -> _list[ToolEntry]:
        """Return every tool entry (order unspecified)."""
        with Transaction(self._conn_string) as trn:
            cursor = trn.execute("SELECT data FROM tool_entries")
            rows = cursor.fetchall()
        entries = [ToolEntry.model_validate(decode_jsonb_column(r[0])) for r in rows]
        logger.debug("Listed %d tool entries", len(entries))
        return entries

    def search(self, query: ToolQuery) -> _list[ToolEntry]:
        """Filter tools by AND-ing all non-None query fields.

        Delegates predicate construction to :func:`_queries.build_tool_where`.
        When the helper signals "no filter" (an empty query), issues a bare
        ``SELECT`` — equivalent to :meth:`list`.

        Args:
            query: Query with optional filter fields.

        Returns:
            Matching tool entries (order unspecified).
        """
        where_sql, params = build_tool_where(query)
        if where_sql is None:
            sql = "SELECT data FROM tool_entries"
        else:
            sql = f"SELECT data FROM tool_entries WHERE {where_sql}"
        with Transaction(self._conn_string) as trn:
            cursor = trn.execute(sql, tuple(params))
            rows = cursor.fetchall()
        results = [ToolEntry.model_validate(decode_jsonb_column(r[0])) for r in rows]
        logger.debug("Search returned %d tool entries", len(results))
        return results

    def update(self, id: str, tool_entry: ToolEntry) -> None:
        """Update an existing tool entry.

        Args:
            id: The id of the entry to update.
            tool_entry: The new entry data.

        Raises:
            CatalogValidationError: If ``tool_entry.id`` does not match ``id``.
            EntryNotFoundError: If no entry with the given id exists.
        """
        if tool_entry.id != id:
            raise CatalogValidationError(
                [f"Entry id mismatch: expected '{id}', got '{tool_entry.id}'"]
            )
        data_json = json.dumps(tool_entry.model_dump())
        with Transaction(self._conn_string) as trn:
            cursor = trn.execute(
                "UPDATE tool_entries SET data = %s WHERE id = %s",
                (data_json, id),
            )
            row_count = cursor.native_cursor.rowcount
        if row_count == 0:
            raise EntryNotFoundError(f"Entry with id '{id}' not found")
        logger.debug("Updated tool entry with id=%s", id)

    def delete(self, id: str) -> None:
        """Delete a tool entry by id.

        Args:
            id: The id of the entry to delete.

        Raises:
            EntryNotFoundError: If no entry with the given id exists.
        """
        with Transaction(self._conn_string) as trn:
            cursor = trn.execute(
                "DELETE FROM tool_entries WHERE id = %s",
                (id,),
            )
            row_count = cursor.native_cursor.rowcount
        if row_count == 0:
            raise EntryNotFoundError(f"Entry with id '{id}' not found")
        logger.debug("Deleted tool entry with id=%s", id)
