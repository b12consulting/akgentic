"""MongoDB-backed v2 catalog module: connection config + entry repository.

This module is the shard-10 final home for the two v2-alive MongoDB surfaces:

* :class:`MongoCatalogConfig` — validated connection + collection-naming
  configuration. Holds the connection string, database name, and the unified
  ``catalog_entries`` collection name. Exposes ``create_client``,
  ``get_database`` and ``get_collection`` helpers. Does NOT manage a live
  ``MongoClient`` — callers create one via ``create_client()`` and own its
  lifecycle.
* :class:`MongoEntryRepository` — single-collection, compound-``_id`` v2
  :class:`~akgentic.catalog.repositories.base.EntryRepository` implementation.

Layout: one ``catalog_entries`` collection keyed by a compound ``_id`` of the
form ``{"namespace": <ns>, "id": <id>}``. ``(namespace, id)`` is the v2
uniqueness key; ``kind`` is metadata. Mongo's primary-key uniqueness on the
compound ``_id`` structurally prevents duplicate ids within a single namespace,
so the duplicate-id reconciliation the YAML backend performs is unnecessary
here.

The repository is intent-preserving: ``put`` writes
``entry.model_dump(mode="json")`` verbatim, so author-written ref markers
(``{"__ref__": ...}``, ``{"__type__": ...}``) round-trip byte-for-byte without
any resolver-layer expansion. The ``Catalog`` service runs
``prepare_for_write`` before reaching this repository; the repository itself
never calls ``populate_refs`` / ``reconcile_refs`` / ``prepare_for_write``.

``pymongo`` is NOT imported at module top level — the only typing surface that
references ``pymongo`` lives under ``if TYPE_CHECKING:``. Runtime code imports
``pymongo`` (and ``pymongo.ASCENDING``) inside the method bodies that need it,
so importing this module without ``pymongo`` installed succeeds. The caller
only pays the ``pymongo`` tax when they actually invoke
:meth:`MongoCatalogConfig.create_client` or hand a live ``Collection`` to
:class:`MongoEntryRepository`. This lazy-import discipline is load-bearing
for the YAML-only install path (``akgentic-catalog`` without the ``[mongo]``
extra).

A secondary index ``(namespace, kind)`` is created lazily on the first
``put``. Read-only repositories never touch the index surface. Under
``mongomock``, ``create_index`` is a no-op that still records the call so
tests can verify the production code path.

Implements the storage layer described in architecture shard 04
(``_bmad-output/akgentic-catalog/architecture/04-repositories.md``) under the
unified-entry contract of ADR-007.
"""

from __future__ import annotations

import builtins
import logging
import re
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field, field_validator

from akgentic.catalog.models.entry import Entry, EntryKind
from akgentic.catalog.models.queries import EntryQuery
from akgentic.catalog.repositories.yaml import _payload_has_cross_ns_ref, _payload_has_ref

if TYPE_CHECKING:
    import pymongo
    import pymongo.collection
    import pymongo.database

__all__ = ["MongoCatalogConfig", "MongoEntryRepository"]

logger = logging.getLogger(__name__)

_list = builtins.list  # Alias: the repository's list() method shadows the built-in


class MongoCatalogConfig(BaseModel):
    """Validated configuration for the MongoDB catalog backend.

    Holds connection parameters and the unified ``catalog_entries`` collection
    name. Does not manage a live ``MongoClient`` — callers use
    :meth:`create_client` and :meth:`get_database` / :meth:`get_collection` to
    obtain database objects.
    """

    connection_string: str = Field(description="MongoDB connection URI")
    database: str = Field(description="Name of the MongoDB database")
    catalog_entries_collection: str = Field(
        default="catalog_entries",
        description="Collection name for the unified v2 catalog entries",
    )

    @field_validator("connection_string")
    @classmethod
    def _validate_connection_string(cls, v: str) -> str:
        """Ensure connection string uses a valid MongoDB URI scheme."""
        if not v.startswith(("mongodb://", "mongodb+srv://")):
            msg = f"connection_string must start with 'mongodb://' or 'mongodb+srv://'; got: {v!r}"
            raise ValueError(msg)
        return v

    def create_client(self) -> pymongo.MongoClient:  # type: ignore[type-arg]
        """Create a new ``MongoClient`` from the configured connection string.

        Returns:
            A ``pymongo.MongoClient`` instance connected to the configured URI.
        """
        import pymongo

        logger.info("Creating MongoClient for database %s", self.database)
        return pymongo.MongoClient(self.connection_string)

    def get_database(self, client: pymongo.MongoClient) -> pymongo.database.Database:  # type: ignore[type-arg]
        """Obtain the configured database from a ``MongoClient``.

        Args:
            client: An active ``MongoClient`` instance.

        Returns:
            The ``pymongo.database.Database`` for the configured database name.
        """
        return client[self.database]

    def get_collection(
        self,
        client: pymongo.MongoClient,  # type: ignore[type-arg]
        collection_name: str,
    ) -> pymongo.collection.Collection:  # type: ignore[type-arg]
        """Obtain a named collection from the configured database.

        Args:
            client: An active ``MongoClient`` instance.
            collection_name: Name of the collection to retrieve.

        Returns:
            The ``pymongo.collection.Collection`` object.
        """
        return self.get_database(client)[collection_name]


class MongoEntryRepository:
    """Single-collection, compound-``_id`` v2 ``EntryRepository`` on MongoDB.

    Satisfies the ``akgentic.catalog.repositories.base.EntryRepository``
    structural protocol. Construction stores the passed ``Collection`` and does
    not perform any I/O — indexes are created lazily on the first ``put``.

    The document shape is:

    ``{
        "_id": {"namespace": <ns>, "id": <id>},
        "namespace": <ns>,
        "id": <id>,
        "kind": <kind>,
        "user_id": <str>,
        "model_type": <akgentic.* dotted path>,
        "description": <str>,
        "payload": {...},
    }``

    ``namespace`` and ``id`` are duplicated at the top level so secondary index
    queries do not need to destructure ``_id`` and so
    ``Entry.model_validate(doc_minus_underscore_id)`` reconstructs the entry
    without having to re-inject ``id`` from ``_id``. This diverges from the v1
    ``repositories/mongo/_helpers.py::to_document`` /``from_document`` helpers
    (which store a plain-string ``_id`` equal to ``entry.id``); v2 uses its own
    shape conversion and does NOT reuse those helpers.

    The repository is intent-preserving — see module docstring. Callers that
    want ref markers expanded must call the resolver pipeline in
    ``akgentic.catalog.resolver`` before / after the repository round-trip;
    this class does not mutate payload content.
    """

    def __init__(self, collection: pymongo.collection.Collection) -> None:  # type: ignore[type-arg]
        """Store the collection; do not touch the server.

        Args:
            collection: A ``pymongo.collection.Collection`` (or mongomock
                equivalent) holding v2 ``Entry`` documents. The repository
                does NOT own the client, the database, or the collection
                lifecycle — the caller wires the ``client → db → collection``
                chain and hands the collection to the constructor.
        """
        self._collection = collection
        self._indexes_created: bool = False
        logger.info("MongoEntryRepository initialized")

    # --- Compound-id and document-shape helpers ---

    def _compound_id(self, namespace: str, id: str) -> dict[str, str]:
        """Return the compound-``_id`` dict ``{"namespace": ns, "id": id}``.

        This is the only form of ``_id`` the v2 collection recognises. The
        dict is constructed fresh on every call — Mongo compares ``_id``
        values by deep equality, so a dict literal is safe to pass to
        ``find_one`` / ``delete_one`` / ``replace_one``.
        """
        return {"namespace": namespace, "id": id}

    def _to_document(self, entry: Entry) -> dict[str, Any]:
        """Serialise ``entry`` to the v2 compound-``_id`` document shape.

        Starts from ``entry.model_dump(mode="json")`` (which includes every
        field of ``Entry`` — ``id``, ``namespace``, ``kind``, … and ``payload``
        verbatim) and adds a compound ``_id`` composed of ``namespace`` + ``id``.
        The top-level ``id`` / ``namespace`` fields are kept so a full read can
        call ``Entry.model_validate`` after popping ``_id`` without having to
        destructure the compound key.

        Diverges from v1 ``_helpers.py::to_document`` — that helper pops ``id``
        to a plain-string ``_id``; v2 keeps ``id`` at the top level AND nests
        it inside the compound ``_id``. The two shapes are incompatible and
        share no code.
        """
        doc = entry.model_dump(mode="json")
        doc["_id"] = self._compound_id(entry.namespace, entry.id)
        return doc

    def _from_document(self, doc: dict[str, Any]) -> Entry:
        """Reconstruct an ``Entry`` from a v2 compound-``_id`` document.

        Takes a shallow copy so the caller's dict is not mutated (some driver
        versions reuse the document object passed to ``replace_one`` or mutate
        cursor results). Pops ``_id`` — the top-level ``namespace`` + ``id``
        fields already carry the same values — and delegates the remaining
        fields to ``Entry.model_validate``.

        Diverges from v1 ``_helpers.py::from_document`` — that helper pops
        ``_id`` into the ``id`` field; v2 discards ``_id`` outright since the
        top-level ``id`` field is authoritative.
        """
        data = dict(doc)
        data.pop("_id", None)
        return Entry.model_validate(data)

    # --- Lazy index creation ---

    def _ensure_indexes(self) -> None:
        """Create the v2 secondary index on the first call; no-op after.

        Declared index:

        * ``(namespace, kind)`` — the standard listing path
          (``list_by_namespace`` + ``get_by_kind``).

        No index is created on ``_id`` — Mongo enforces primary-key uniqueness
        on ``_id`` by default. No index is created for the payload walk
        ``find_references`` uses (v2 acceptance — no wildcard payload index).

        The one-shot ``self._indexes_created`` flag keeps subsequent ``put``
        calls cheap. The ``ASCENDING`` constant is imported inside this method
        so the module's top-level imports stay free of ``pymongo``.
        """
        if self._indexes_created:
            return
        from pymongo import ASCENDING

        self._collection.create_index([("namespace", ASCENDING), ("kind", ASCENDING)])
        self._indexes_created = True

    # --- Write operations ---

    def put(self, entry: Entry) -> Entry:
        """Upsert ``entry`` keyed by ``(namespace, id)``; return the stored entry.

        Creates the secondary index on the first call (see
        ``_ensure_indexes``) and issues ``replace_one(filter, doc,
        upsert=True)`` with ``filter = {"_id": {"namespace": ..., "id": ...}}``.
        The "second write wins" contract (the v2 upsert semantics pinned by
        the acceptance criteria) means the collection count does not grow on
        re-writes of the same ``(namespace, id)`` pair — including when
        ``kind`` changes between writes, since ``kind`` is metadata and not
        part of the uniqueness key.

        Does NOT use ``insert_one`` + ``DuplicateKeyError`` recovery — the
        contract is unambiguous upsert and ``replace_one`` + ``upsert=True``
        is the direct path. Does NOT call ``populate_refs`` / ``reconcile_refs``
        / ``prepare_for_write`` / ``entry.model_dump(exclude_unset=True)`` —
        the repository is intent-preserving (writes what it was handed).

        Args:
            entry: The entry to persist. The ``(namespace, id)`` pair is the
                uniqueness key.

        Returns:
            The ``entry`` argument unchanged — callers treat it as the stored
            form for chaining or assertion.
        """
        self._ensure_indexes()
        doc = self._to_document(entry)
        self._collection.replace_one({"_id": doc["_id"]}, doc, upsert=True)
        logger.debug("Upserted entry ns=%s id=%s kind=%s", entry.namespace, entry.id, entry.kind)
        return entry

    def delete(self, namespace: str, id: str) -> None:
        """Remove the entry at ``(namespace, id)``; no-op when absent.

        Issues ``delete_one({"_id": {"namespace": ..., "id": ...}})`` and
        ignores ``DeleteResult.deleted_count`` — the repository accepts
        idempotency. The service layer's ``validate_delete`` is the
        authoritative guard against deleting missing entries.
        """
        self._collection.delete_one({"_id": self._compound_id(namespace, id)})
        logger.debug("Deleted entry ns=%s id=%s (idempotent on miss)", namespace, id)

    # --- Read operations ---

    def get(self, namespace: str, id: str) -> Entry | None:
        """Return the entry at ``(namespace, id)`` or ``None`` if absent.

        Issues ``find_one({"_id": {"namespace": ..., "id": ...}})``. Because
        the filter uses the compound primary key, namespace isolation is
        enforced by construction — a matching id in a different namespace
        cannot bleed across.
        """
        doc = self._collection.find_one({"_id": self._compound_id(namespace, id)})
        if doc is None:
            return None
        return self._from_document(doc)

    def list(self, query: EntryQuery) -> _list[Entry]:
        """Return entries matching ``query`` with AND semantics over set fields.

        Builds a single server-side filter dict via ``_build_filter`` and
        issues one ``find``. Every filter supported by ``EntryQuery`` is
        translated to an exact-match Mongo clause, except
        ``description_contains`` which becomes a case-sensitive
        ``{"$regex": re.escape(value)}`` clause (no ``$options: "i"`` —
        parity with the YAML backend's case-sensitive substring check).

        When ``query.namespace`` is ``None``, the filter omits the
        ``namespace`` clause and the scan spans every document subject to
        the remaining filters.
        """
        mongo_filter = self._build_filter(query)
        cursor = self._collection.find(mongo_filter)
        return [self._from_document(doc) for doc in cursor]

    def _build_filter(self, query: EntryQuery) -> dict[str, Any]:
        """Translate an ``EntryQuery`` into a server-side Mongo filter dict.

        Each non-``None`` field contributes exactly one clause. ``user_id_set``
        is tri-state (``None`` = ignore, ``True`` = ``user_id != "anonymous"``,
        ``False`` = ``user_id == "anonymous"``). ``user_id`` and ``user_id_set``
        combine with AND semantics — if both are set, the emitted ``user_id``
        clause honours both constraints jointly so the Mongo filter matches
        the YAML backend's ``_matches`` evaluation (conjunctive over both
        fields). ``description_contains`` uses ``re.escape`` to defuse regex
        metacharacters in user input.
        """
        mongo_filter: dict[str, Any] = {}
        if query.namespace is not None:
            mongo_filter["namespace"] = query.namespace
        if query.kind is not None:
            mongo_filter["kind"] = query.kind
        if query.id is not None:
            mongo_filter["id"] = query.id
        self._apply_user_id_clauses(query, mongo_filter)
        if query.description_contains is not None:
            mongo_filter["description"] = {"$regex": re.escape(query.description_contains)}
        return mongo_filter

    def _apply_user_id_clauses(
        self,
        query: EntryQuery,
        mongo_filter: dict[str, Any],
    ) -> None:
        """Write a conjunctive ``user_id`` clause into ``mongo_filter``.

        ``user_id`` and ``user_id_set`` are orthogonal knobs that combine
        with AND semantics (parity with YAML's ``_matches``). Table of
        behaviours for the four relevant combinations:

        * both unset → no ``user_id`` key is added.
        * ``user_id`` only → exact match (``{"user_id": value}``).
        * ``user_id_set`` only → comparison against the literal
          ``"anonymous"`` (``{"$ne": "anonymous"}`` for True, ``"anonymous"``
          for False).
        * both set → the joint constraint: if ``user_id_set=True`` the exact
          value already guarantees ``user_id != "anonymous"`` (because a
          caller passing the literal ``"anonymous"`` while also asserting
          ``user_id_set=True`` is a contradiction — see below); if
          ``user_id_set=False`` the combination is logically impossible
          (a non-``"anonymous"`` value cannot also equal ``"anonymous"``),
          so an unsatisfiable clause (``{"$in": []}``) is emitted so the
          query returns zero documents — which is exactly what the YAML
          backend returns for the same contradictory pair.
        """
        # query.user_id is the EntryQuery exact-match filter (still
        # tri-state: a falsy / None value means "no filter"). NonEmptyStr
        # ensures the only falsy value is None.
        q_user_id = query.user_id
        if not q_user_id and query.user_id_set is None:
            return
        if query.user_id_set is None:
            mongo_filter["user_id"] = q_user_id
            return
        if not q_user_id:
            mongo_filter["user_id"] = {"$ne": "anonymous"} if query.user_id_set else "anonymous"
            return
        if query.user_id_set:
            # Exact value is already non-"anonymous"; the exact match satisfies both.
            mongo_filter["user_id"] = q_user_id
            return
        # user_id set but user_id_set is False → contradiction; match nothing.
        mongo_filter["user_id"] = {"$in": []}

    def list_by_namespace(self, namespace: str) -> _list[Entry]:
        """Return every entry in ``namespace`` in a single ``find``.

        Issues ``find({"namespace": namespace})`` — exactly one round-trip, no
        per-kind fan-out. Missing namespace (no documents with that
        ``namespace`` value) returns ``[]`` without raising.
        """
        cursor = self._collection.find({"namespace": namespace})
        return [self._from_document(doc) for doc in cursor]

    def get_by_kind(self, namespace: str, kind: EntryKind) -> Entry | None:
        """Return a single entry of ``kind`` in ``namespace`` or ``None``.

        Issues ``find_one({"namespace": namespace, "kind": kind},
        sort=[("_id.id", ASCENDING)])`` so that, in the corruption case where
        two documents share ``(namespace, kind)`` (which the service-layer
        singleton invariant forbids but the repository tolerates), the
        deterministic alphabetically-first result by ``_id.id`` is returned.
        The repository does NOT raise on duplicates — the singleton invariant
        is owned by ``Catalog.create`` (Story 15.5).
        """
        from pymongo import ASCENDING

        doc = self._collection.find_one(
            {"namespace": namespace, "kind": kind},
            sort=[("_id.id", ASCENDING)],
        )
        if doc is None:
            return None
        return self._from_document(doc)

    def find_references(self, namespace: str, target_id: str) -> _list[Entry]:
        """Return entries in ``namespace`` whose payload references ``target_id``.

        Loads every entry in ``namespace`` via ``list_by_namespace`` (one
        server-side query), then walks each payload in memory using the
        ``_payload_has_ref`` helper shared with the YAML backend. No
        server-side wildcard payload index is used — v2 acceptance
        (``no wildcard payload index``) — the in-memory walk is cheap at
        namespace scale and keeps the two backends in lockstep with a single
        shared walker.

        Namespace isolation is strict: refs in other namespaces are not
        inspected.
        """
        return [
            entry
            for entry in self.list_by_namespace(namespace)
            if _payload_has_ref(entry.payload, target_id)
        ]

    def find_references_global(self, namespace: str, target_id: str) -> _list[Entry]:
        """Return entries across every namespace whose payload carries a cross-ns ref.

        Issues one unfiltered ``find({})`` and applies the shared
        :func:`_payload_has_cross_ns_ref` walker per document (ADR-008
        §D2 as updated 2026-05-08). No JSONB / wildcard payload index —
        same shape as the existing ``find_references`` walker.
        """
        cursor = self._collection.find({})
        return [
            entry
            for entry in (self._from_document(doc) for doc in cursor)
            if _payload_has_cross_ns_ref(entry.payload, namespace, target_id)
        ]
