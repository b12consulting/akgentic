"""PostgreSQL-backed v2 :class:`EntryRepository` with a single ``catalog_entries`` table.

Structural peer of
:class:`akgentic.catalog.repositories.mongo.MongoEntryRepository` — one class,
one table, one implementation of the
:class:`akgentic.catalog.repositories.base.EntryRepository` Protocol, keyed
by the compound ``(namespace, id)`` primary key.

**Intent-preserving**: :meth:`put` writes
``entry.model_dump(mode="json")`` verbatim, so author-written ref markers
(``{"__ref__": ...}``, ``{"__type__": ...}``) round-trip byte-for-byte
without any resolver-layer expansion. The ``Catalog`` service runs
``prepare_for_write`` before reaching this repository; the repository itself
never calls ``populate_refs`` / ``reconcile_refs`` / ``prepare_for_write``.

**Transaction discipline**: every public method opens its own
``with Transaction(self._conn_string) as trn:`` block and closes it before
returning. No transaction object leaks to callers. The caller owns the DSN;
the method owns the transaction — mirroring the Mongo backend's "caller owns
the client, method owns the op" shape.

**Lazy imports**: ``nagra`` and ``psycopg`` are **not** imported at module
top level. All runtime references live inside method bodies, so
``import akgentic.catalog.repositories.postgres`` succeeds on an install
without the ``[postgres]`` extra. Type-level references use
``if TYPE_CHECKING:``.

**Find-references walker**: ``find_references`` delegates to the shared
``_payload_has_ref`` helper imported from
:mod:`akgentic.catalog.repositories.yaml` — same import the Mongo backend
uses. No parallel implementation in the ``postgres`` package; no JSONB
containment (``payload @> '…'::jsonb``) or GIN-index-backed optimisation.

Implements ADR-011 §"Single ``catalog_entries`` table" —
navigation-only reference.
"""

from __future__ import annotations

import builtins
import json
import logging
from importlib.resources import files
from typing import TYPE_CHECKING, Any

from akgentic.catalog.models.entry import Entry, EntryKind
from akgentic.catalog.models.queries import EntryQuery
from akgentic.catalog.repositories.yaml import _payload_has_cross_ns_ref, _payload_has_ref

if TYPE_CHECKING:
    # Type-only imports — not loaded at runtime. The `if TYPE_CHECKING:`
    # guard keeps the module importable without the `[postgres]` extra.
    # nagra ships no py.typed marker; its types surface as Any at the
    # call sites, which is acceptable inside the repository because the
    # surrounding Pydantic types / psycopg param bindings provide the
    # shape we rely on.
    import nagra  # type: ignore[import-untyped]  # noqa: F401

__all__ = ["PostgresEntryRepository"]

logger = logging.getLogger(__name__)

_list = builtins.list  # Alias: the repository's list() method shadows the built-in

# Column order used in every INSERT / SELECT so the two sides agree.
_COLUMNS: tuple[str, ...] = (
    "namespace",
    "id",
    "kind",
    "user_id",
    "model_type",
    "description",
    "payload",
)

# INSERT … ON CONFLICT (namespace, id) DO UPDATE SET … pinned by AC10.
_INSERT_SQL: str = (
    "INSERT INTO catalog_entries "
    "(namespace, id, kind, user_id, model_type, description, payload) "
    "VALUES (%s, %s, %s, %s, %s, %s, %s) "
    "ON CONFLICT (namespace, id) DO UPDATE SET "
    "kind = EXCLUDED.kind, "
    "user_id = EXCLUDED.user_id, "
    "model_type = EXCLUDED.model_type, "
    "description = EXCLUDED.description, "
    "payload = EXCLUDED.payload"
)

_SELECT_ALL_COLUMNS: str = (
    "SELECT namespace, id, kind, user_id, model_type, description, payload FROM catalog_entries"
)


class PostgresEntryRepository:
    """Single-table, compound-PK v2 ``EntryRepository`` on PostgreSQL.

    Satisfies the ``akgentic.catalog.repositories.base.EntryRepository``
    structural protocol. Construction stores the DSN and loads the Nagra
    schema in-process; it does **not** issue DDL. Use :func:`init_db` to
    create the table — see ADR-011's explicit ``init_db`` decoupling.
    """

    def __init__(self, conn_string: str) -> None:
        """Store the DSN and load the Nagra schema in-process.

        Loading the Nagra schema parses the packaged ``schema.toml``; it
        does **not** open a connection, issue DDL, or query
        ``information_schema``. The schema object is retained as a
        reference but not used for DML — the DML path emits raw SQL via
        ``Transaction.execute`` to pin the exact shape ADR-011 prescribes.

        Args:
            conn_string: libpq-style Postgres DSN
                (``postgresql://user:pw@host:port/db``).
        """
        from nagra import Schema

        self._conn_string = conn_string
        schema_toml = files(__package__).joinpath("schema.toml").read_text(encoding="utf-8")
        self._schema = Schema.from_toml(schema_toml)
        logger.info("PostgresEntryRepository initialized")

    # --- Document-shape helpers ---

    def _to_params(self, entry: Entry) -> tuple[Any, ...]:
        """Serialise ``entry`` into the bound-parameter tuple for ``_INSERT_SQL``.

        Emits a 7-tuple in the exact column order of :data:`_COLUMNS`. The
        ``payload`` element is ``json.dumps(...)`` of the verbatim
        ``entry.model_dump(mode="json")["payload"]`` dict — no resolver
        expansion, no ``exclude_unset``, no sibling-key mutation.
        psycopg will pass the string through unchanged; Postgres will cast
        it to ``jsonb`` because the column's declared type is ``jsonb``.
        """
        dumped = entry.model_dump(mode="json")
        payload_json = json.dumps(dumped["payload"])
        return (
            dumped["namespace"],
            dumped["id"],
            dumped["kind"],
            dumped["user_id"],
            dumped["model_type"],
            dumped["description"],
            payload_json,
        )

    def _row_to_entry(self, row: tuple[Any, ...]) -> Entry:
        """Reconstruct an ``Entry`` from a 7-column SELECT tuple.

        The ``payload`` element comes back from psycopg as a Python dict
        (psycopg auto-decodes JSONB to dict); ``Entry.model_validate``
        accepts it directly. For defensiveness, if psycopg ever returns a
        string (e.g. via a future driver flag or non-``jsonb`` column),
        ``json.loads`` is applied to decode it.
        """
        data: dict[str, Any] = dict(zip(_COLUMNS, row, strict=True))
        payload = data["payload"]
        if isinstance(payload, str):
            data["payload"] = json.loads(payload)
        return Entry.model_validate(data)

    # --- Write operations ---

    def put(self, entry: Entry) -> Entry:
        """Upsert ``entry`` keyed by ``(namespace, id)``; return the stored entry.

        Opens its own ``Transaction`` and issues one ``INSERT … ON CONFLICT
        (namespace, id) DO UPDATE SET …`` — the second write wins. ``kind``
        is metadata and not part of the PK, so an upsert may change
        ``kind`` between calls. Returns ``entry`` unchanged so callers may
        chain or assert.
        """
        from nagra import Transaction

        params = self._to_params(entry)
        with Transaction(self._conn_string) as trn:
            trn.execute(_INSERT_SQL, params)
        logger.debug("Upserted entry ns=%s id=%s kind=%s", entry.namespace, entry.id, entry.kind)
        return entry

    def delete(self, namespace: str, id: str) -> None:
        """Remove the entry at ``(namespace, id)``; no-op when absent.

        Opens its own ``Transaction`` and issues
        ``DELETE FROM catalog_entries WHERE namespace = %s AND id = %s``.
        Ignores the row count — the repository accepts idempotency.
        Parity with Mongo's ``delete_one``.
        """
        from nagra import Transaction

        with Transaction(self._conn_string) as trn:
            trn.execute(
                "DELETE FROM catalog_entries WHERE namespace = %s AND id = %s",
                (namespace, id),
            )
        logger.debug("Deleted entry ns=%s id=%s (idempotent on miss)", namespace, id)

    # --- Read operations ---

    def get(self, namespace: str, id: str) -> Entry | None:
        """Return the entry at ``(namespace, id)`` or ``None`` if absent.

        Both values are bound as psycopg ``%s`` parameters — never
        interpolated into SQL text. Namespace isolation is enforced by the
        ``WHERE`` clause.
        """
        from nagra import Transaction

        sql = f"{_SELECT_ALL_COLUMNS} WHERE namespace = %s AND id = %s"
        with Transaction(self._conn_string) as trn:
            row = trn.execute(sql, (namespace, id)).fetchone()
        if row is None:
            return None
        return self._row_to_entry(row)

    def list_by_namespace(self, namespace: str) -> _list[Entry]:
        """Return every entry in ``namespace``; empty namespace → ``[]``."""
        from nagra import Transaction

        sql = f"{_SELECT_ALL_COLUMNS} WHERE namespace = %s"
        with Transaction(self._conn_string) as trn:
            rows = trn.execute(sql, (namespace,)).fetchall()
        return [self._row_to_entry(row) for row in rows]

    def get_by_kind(self, namespace: str, kind: EntryKind) -> Entry | None:
        """Return a single entry of ``kind`` in ``namespace`` or ``None``.

        Uses ``ORDER BY id ASC LIMIT 1`` so that, in the (forbidden but
        tolerated) duplicate case where two rows share ``(namespace, kind)``,
        the alphabetically-first ``id`` wins — deterministic tie-break,
        parity with Mongo's ``sort=[("_id.id", ASCENDING)]``. The
        repository does not raise on duplicates; the singleton invariant
        is owned by ``Catalog.create`` (Story 15.5).
        """
        from nagra import Transaction

        sql = f"{_SELECT_ALL_COLUMNS} WHERE namespace = %s AND kind = %s ORDER BY id ASC LIMIT 1"
        with Transaction(self._conn_string) as trn:
            row = trn.execute(sql, (namespace, kind)).fetchone()
        if row is None:
            return None
        return self._row_to_entry(row)

    def find_references(self, namespace: str, target_id: str) -> _list[Entry]:
        """Return entries in ``namespace`` whose payload references ``target_id``.

        One ``SELECT`` via :meth:`list_by_namespace`, then filter in Python
        using the shared :func:`_payload_has_ref` helper (imported from
        the YAML backend). No JSONB containment query; no GIN index.
        Namespace isolation is strict.
        """
        return [
            entry
            for entry in self.list_by_namespace(namespace)
            if _payload_has_ref(entry.payload, target_id)
        ]

    def find_references_global(self, namespace: str, target_id: str) -> _list[Entry]:
        """Return entries across every namespace carrying a cross-ns ref.

        Issues one unfiltered ``SELECT … FROM catalog_entries`` and applies
        the shared :func:`_payload_has_cross_ns_ref` walker per row
        (ADR-008 §D2 as updated 2026-05-08). No JSONB containment /
        wildcard payload index — parity with the existing
        :meth:`find_references` walker.
        """
        from nagra import Transaction

        sql = _SELECT_ALL_COLUMNS
        with Transaction(self._conn_string) as trn:
            rows = trn.execute(sql, ()).fetchall()
        return [
            entry
            for entry in (self._row_to_entry(row) for row in rows)
            if _payload_has_cross_ns_ref(entry.payload, namespace, target_id)
        ]

    def list(self, query: EntryQuery) -> _list[Entry]:
        """Return entries matching ``query`` with AND semantics over set fields.

        Delegates the WHERE-clause build to :func:`_build_where`, then
        emits one ``SELECT`` (bare, without a ``WHERE`` clause, when the
        query is empty). Every user-supplied value is bound as a psycopg
        ``%s`` parameter. No ``ORDER BY`` / ``LIMIT`` / ``OFFSET`` at this
        layer (AC22) — callers that need ordering must sort client-side.
        """
        from nagra import Transaction

        where_fragment, params = _build_where(query)
        if where_fragment:
            sql = f"{_SELECT_ALL_COLUMNS} WHERE {where_fragment}"
        else:
            sql = _SELECT_ALL_COLUMNS
        with Transaction(self._conn_string) as trn:
            rows = trn.execute(sql, tuple(params)).fetchall()
        return [self._row_to_entry(row) for row in rows]


# --- EntryQuery → SQL translator (module-level pure helpers for unit testability) ---


_EXACT_MATCH_FIELDS: tuple[tuple[str, str], ...] = (
    # (EntryQuery attribute, SQL column)
    ("namespace", "namespace"),
    ("kind", "kind"),
    ("id", "id"),
)


def _escape_ilike(value: str) -> str:
    """Escape LIKE metacharacters in ``value`` for safe ILIKE binding.

    Order matters: escape the backslash first, then ``%``, then ``_`` —
    otherwise later passes double-escape the backslashes introduced by the
    earlier ones. Mirrors the ILIKE-escape helper landed on ``master`` in
    commit 6155a93 (the tool-predicate search surface).

    The result is wrapped by the caller into ``"%<escaped>%"`` and bound
    as a psycopg parameter — never concatenated into SQL text.
    """
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _apply_user_id_clauses(
    query: EntryQuery,
    clauses: _list[str],
    params: _list[Any],
) -> None:
    """Append the user_id clause (if any) for ``query`` to ``clauses`` / ``params``.

    Mirrors :meth:`MongoEntryRepository._apply_user_id_clauses` line-for-
    line. Six cases:

    * both unset → no clause.
    * ``user_id`` only → ``user_id = %s``.
    * ``user_id_set=True`` only → ``user_id != 'anonymous'``.
    * ``user_id_set=False`` only → ``user_id = 'anonymous'``.
    * ``user_id`` set AND ``user_id_set=True`` → exact-match clause (the
      value is already non-``"anonymous"``; the single equality satisfies
      both).
    * ``user_id`` set AND ``user_id_set=False`` → unsatisfiable (a non-
      ``"anonymous"`` value cannot also equal ``"anonymous"``); emit
      ``1 = 0`` so the query matches zero rows — parity with Mongo's
      ``{"$in": []}`` and YAML's ``_matches`` short-circuit.

    The column-level NULL constraint on ``user_id`` is unchanged at the
    schema layer (``user_id TEXT`` stays nullable). The Pydantic non-null
    guarantee on :class:`Entry.user_id` is the application-layer contract;
    no equality-against-``"anonymous"`` clause needs to special-case NULL
    rows because the catalog never writes them.
    """
    # query.user_id is the EntryQuery exact-match filter (still tri-state:
    # a falsy / None value means "no filter"). NonEmptyStr ensures the only
    # falsy value is None.
    q_user_id = query.user_id
    if not q_user_id and query.user_id_set is None:
        return
    if query.user_id_set is None:
        clauses.append("user_id = %s")
        params.append(q_user_id)
        return
    if not q_user_id:
        clauses.append("user_id != %s" if query.user_id_set else "user_id = %s")
        params.append("anonymous")
        return
    if query.user_id_set:
        clauses.append("user_id = %s")
        params.append(q_user_id)
        return
    # user_id set AND user_id_set=False → contradiction; match zero rows.
    clauses.append("1 = 0")


def _build_where(query: EntryQuery) -> tuple[str, _list[Any]]:
    """Translate an ``EntryQuery`` into a ``(where_fragment, params)`` pair.

    The fragment is the body of the ``WHERE`` clause (no leading
    ``WHERE`` keyword); an empty query yields an empty string so the
    caller can emit a bare ``SELECT … FROM catalog_entries``. ``params``
    is the ordered list of bound values matching the ``%s`` placeholders
    in the fragment.

    Clauses:

    * Exact-match fields (``namespace``, ``kind``, ``id``) → ``<column> = %s``.
    * ``user_id`` / ``user_id_set`` → see :func:`_apply_user_id_clauses`.
    * ``description_contains`` → ``description ILIKE %s`` with the bound
      value ``f"%{_escape_ilike(value)}%"``. Case-insensitive; the
      LIKE-metacharacters ``\\``, ``%``, ``_`` in the input are escaped
      before the wildcard wrap.

    Clauses join with ``AND`` — parity with Mongo's AND-over-fields and
    YAML's conjunctive ``_matches``.
    """
    clauses: _list[str] = []
    params: _list[Any] = []
    for attr, column in _EXACT_MATCH_FIELDS:
        value = getattr(query, attr)
        if value is not None:
            clauses.append(f"{column} = %s")
            params.append(value)
    _apply_user_id_clauses(query, clauses, params)
    if query.description_contains is not None:
        clauses.append("description ILIKE %s")
        params.append(f"%{_escape_ilike(query.description_contains)}%")
    return " AND ".join(clauses), params


# --- Protocol conformance pin (type-check-time only) ---


if TYPE_CHECKING:
    # Pin structural Protocol conformance under `mypy --strict`. A
    # runtime assignment would require constructing the repository; the
    # TYPE_CHECKING guard keeps the pin as a type-time invariant only.
    from akgentic.catalog.repositories.base import EntryRepository

    _x: EntryRepository = PostgresEntryRepository("postgresql://x/y")
