"""Nagra/Postgres-backed repository for template catalog entries.

Implements ADR-006 §2 (per-method Transaction ownership), §3 (two-column
JSONB table shape), §4 (predicate table — Template rows), and §8 (error
handling parity with the Mongo backend). All SQL text is assembled from the
fragment builders in :mod:`_queries`; raw user input is never interpolated
into SQL text — every variable is passed through as a psycopg ``%s`` bound
parameter.
"""

from __future__ import annotations

import builtins
import json
import logging
from typing import TYPE_CHECKING

from nagra import Transaction  # type: ignore[import-untyped]
from psycopg.errors import UniqueViolation

from akgentic.catalog.models.errors import CatalogValidationError, EntryNotFoundError
from akgentic.catalog.models.template import TemplateEntry
from akgentic.catalog.repositories.base import TemplateCatalogRepository
from akgentic.catalog.repositories.postgres._queries import (
    build_template_where,
    decode_jsonb_column,
)

if TYPE_CHECKING:
    from akgentic.catalog.models.queries import TemplateQuery

logger = logging.getLogger(__name__)

_list = builtins.list  # repository's list() method shadows the built-in


class NagraTemplateCatalogRepository(TemplateCatalogRepository):
    """Nagra/Postgres-backed template catalog repository.

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
        logger.info("NagraTemplateCatalogRepository initialised")

    def create(self, template_entry: TemplateEntry) -> str:
        """Persist a new template entry.

        Inserts a row into ``template_entries`` with ``id = template_entry.id``
        and ``data = template_entry.model_dump()``. Translates psycopg's
        ``UniqueViolation`` to :class:`CatalogValidationError` so callers see
        the same exception type as the YAML and Mongo backends emit on a
        duplicate id.

        Args:
            template_entry: The template entry to create.

        Returns:
            The id of the created entry.

        Raises:
            CatalogValidationError: If an entry with the same id already exists.
        """
        data_json = json.dumps(template_entry.model_dump())
        try:
            with Transaction(self._conn_string) as trn:
                trn.execute(
                    "INSERT INTO template_entries (id, data) VALUES (%s, %s)",
                    (template_entry.id, data_json),
                )
        except UniqueViolation:
            raise CatalogValidationError(
                [f"Entry with id '{template_entry.id}' already exists"]
            )
        logger.debug("Created template entry with id=%s", template_entry.id)
        return template_entry.id

    def get(self, id: str) -> TemplateEntry | None:
        """Retrieve a template entry by id.

        Args:
            id: The template entry id.

        Returns:
            The template entry, or ``None`` if no row matches.
        """
        with Transaction(self._conn_string) as trn:
            cursor = trn.execute(
                "SELECT data FROM template_entries WHERE id = %s",
                (id,),
            )
            row = cursor.fetchone()
        if row is None:
            logger.debug("Template entry not found: id=%s", id)
            return None
        return TemplateEntry.model_validate(decode_jsonb_column(row[0]))

    def list(self) -> _list[TemplateEntry]:
        """Return every template entry (order unspecified)."""
        with Transaction(self._conn_string) as trn:
            cursor = trn.execute("SELECT data FROM template_entries")
            rows = cursor.fetchall()
        entries = [TemplateEntry.model_validate(decode_jsonb_column(r[0])) for r in rows]
        logger.debug("Listed %d template entries", len(entries))
        return entries

    def search(self, query: TemplateQuery) -> _list[TemplateEntry]:
        """Filter templates by AND-ing all non-None query fields.

        Delegates predicate construction to
        :func:`_queries.build_template_where`. When the helper signals "no
        filter" (an empty query), issues a bare ``SELECT`` — equivalent to
        :meth:`list`.

        Args:
            query: Query with optional filter fields.

        Returns:
            Matching template entries (order unspecified).
        """
        where_sql, params = build_template_where(query)
        if where_sql is None:
            sql = "SELECT data FROM template_entries"
        else:
            sql = f"SELECT data FROM template_entries WHERE {where_sql}"
        with Transaction(self._conn_string) as trn:
            cursor = trn.execute(sql, tuple(params))
            rows = cursor.fetchall()
        results = [TemplateEntry.model_validate(decode_jsonb_column(r[0])) for r in rows]
        logger.debug("Search returned %d template entries", len(results))
        return results

    def update(self, id: str, template_entry: TemplateEntry) -> None:
        """Update an existing template entry.

        Args:
            id: The id of the entry to update.
            template_entry: The new entry data.

        Raises:
            CatalogValidationError: If ``template_entry.id`` does not match ``id``.
            EntryNotFoundError: If no entry with the given id exists.
        """
        if template_entry.id != id:
            raise CatalogValidationError(
                [f"Entry id mismatch: expected '{id}', got '{template_entry.id}'"]
            )
        data_json = json.dumps(template_entry.model_dump())
        with Transaction(self._conn_string) as trn:
            cursor = trn.execute(
                "UPDATE template_entries SET data = %s WHERE id = %s",
                (data_json, id),
            )
            row_count = cursor.native_cursor.rowcount
        if row_count == 0:
            raise EntryNotFoundError(f"Entry with id '{id}' not found")
        logger.debug("Updated template entry with id=%s", id)

    def delete(self, id: str) -> None:
        """Delete a template entry by id.

        Args:
            id: The id of the entry to delete.

        Raises:
            EntryNotFoundError: If no entry with the given id exists.
        """
        with Transaction(self._conn_string) as trn:
            cursor = trn.execute(
                "DELETE FROM template_entries WHERE id = %s",
                (id,),
            )
            row_count = cursor.native_cursor.rowcount
        if row_count == 0:
            raise EntryNotFoundError(f"Entry with id '{id}' not found")
        logger.debug("Deleted template entry with id=%s", id)
