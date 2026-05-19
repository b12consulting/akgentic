"""Abstract repository interface for catalog v2 backends.

The ``EntryRepository`` ``typing.Protocol`` is the single contract concrete
backends satisfy (YAML in ``yaml.py``, Mongo in ``mongo.py``). It is a
structural protocol — implementations match by shape, not by inheritance.
"""

from __future__ import annotations

import builtins
from typing import Protocol

from akgentic.catalog.models.entry import Entry, EntryKind
from akgentic.catalog.models.queries import EntryQuery

__all__ = ["EntryRepository"]

_list = builtins.list  # Alias: the repository's list() method shadows the built-in


class EntryRepository(Protocol):
    """Type contract for concrete v2 backends storing unified ``Entry`` rows.

    Structural protocol (no runtime-checkable decorator) — concrete
    implementations in Stories 15.3 (YAML) and 15.4 (Mongo) satisfy it by
    shape, not by inheritance.
    """

    def get(self, namespace: str, id: str) -> Entry | None:
        """Fetch a single entry identified by (namespace, id); return None if absent."""

    def put(self, entry: Entry) -> Entry:
        """Insert or replace ``entry`` keyed by (namespace, id); return the stored entry."""

    def delete(self, namespace: str, id: str) -> None:
        """Remove the entry identified by (namespace, id)."""

    def list(self, query: EntryQuery) -> _list[Entry]:
        """Return entries matching ``query`` (AND semantics over set fields)."""

    def list_by_namespace(self, namespace: str) -> _list[Entry]:
        """Return every entry in ``namespace`` regardless of kind."""

    def get_by_kind(self, namespace: str, kind: EntryKind) -> Entry | None:
        """Return a single entry of ``kind`` in ``namespace`` if one exists."""

    def find_references(self, namespace: str, target_id: str) -> _list[Entry]:
        """Return entries in ``namespace`` whose payload references ``target_id``."""

    def find_references_global(self, namespace: str, target_id: str) -> _list[Entry]:
        """Return entries whose payload carries a cross-ns ref to ``(namespace, target_id)``.

        Implements ADR-008 §D2 (updated 2026-05-08 rev 2) — the global-scope
        referrer walker that widens the namespace-bounded delete guard to
        a cross-tenant guard for shareable namespaces (target's ``_meta``
        has ``payload["shareable"] is True``, strict-bool). The match shape
        recognises
        both the canonical sentinel (``__namespace__ == namespace`` AND
        ``__ref__ == target_id``) and the shorthand form (``__ref__ ==
        "<namespace>.<target_id>"``).

        Implementations enumerate every namespace persisted in the
        repository (no caller-supplied scope, no empty-scope short-
        circuit) and apply the in-memory walker per entry. Documented as
        O(N) over total entries.

        Args:
            namespace: Target namespace of the (potential) cross-ns ref.
            target_id: Target id of the (potential) cross-ns ref.

        Returns:
            The list of referring entries across every namespace in the
            repository. Empty when no referrer exists.
        """
