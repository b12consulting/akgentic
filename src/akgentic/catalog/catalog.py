"""Unified v2 ``Catalog`` service — CRUD, clone, resolve, and load_team.

This module owns the single public service that composes an ``EntryRepository``
with the resolver pipeline (:mod:`akgentic.catalog.resolver`) to gate CRUD
semantics, own the ``clone`` deep-copy primitive, and expose ``resolve`` /
``resolve_by_id`` / ``load_team``.

Placement is deliberate — ``catalog.py`` sits at the top level of the package
(next to :mod:`resolver`, :mod:`env`) per the shard 10 package-structure plan.
The v1 ``services/`` directory (home of ``TemplateCatalog``, ``ToolCatalog``,
``AgentCatalog``, ``TeamCatalog``) is retired by Epic 19.

Invariants enforced by the service (not the repository):

* **Namespace initialization** — non-team, non-meta entries in a namespace require
  a pre-existing team OR meta entry (``_check_namespace_initialized``).
* **Ownership** — sub-entries' ``user_id`` MUST equal the team entry's
  ``user_id`` (``_check_ownership``).
* **Namespace minting** — a team entry whose ``namespace`` equals the sentinel
  :data:`UNSET_NAMESPACE` has its namespace replaced by a fresh ``uuid.uuid4()``
  string before any other pipeline step runs.
* **Clone atomicity** — ``clone`` collects every intended write in memory, then
  emits them in a single pass; partial failures leave the destination namespace
  untouched.
* **``load_team`` single-query** — exactly one ``list_by_namespace`` call reaches
  the repository; ref resolution is served by an in-memory wrapper.

The service never catches and re-wraps :class:`CatalogValidationError` from
``prepare_for_write`` — those propagate unchanged.
"""

from __future__ import annotations

import builtins
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Final

from pydantic import BaseModel

from akgentic.catalog.models.entry import Entry
from akgentic.catalog.models.errors import CatalogValidationError, EntryNotFoundError
from akgentic.catalog.models.namespace_meta import NamespaceMeta
from akgentic.catalog.models.queries import EntryQuery
from akgentic.catalog.repositories.base import EntryRepository
from akgentic.catalog.resolver import (
    NAMESPACE_KEY,
    REF_KEY,
    TYPE_KEY,
    prepare_for_write,
    validate_delete,
)
from akgentic.catalog.resolver import resolve as _resolve
from akgentic.catalog.serialization import BundleHeader, dump_namespace, load_namespace
from akgentic.catalog.validation import NamespaceValidationReport, validate_entries
from akgentic.team.models import TeamCard

# The namespace-meta `model_type` allowlist key — pinned at module load time
# so the meta-upsert path (Catalog.import_namespace_yaml) can construct a
# `_meta` Entry without redeclaring the FQCN string. ADR-008 §D1.
_NAMESPACE_META_TYPE: Final[str] = "akgentic.catalog.models.namespace_meta.NamespaceMeta"

# Reserved ref-marker keys (subset of `_RESERVED_REF_KEYS` from the resolver)
# used by the cross-ns walker to skip recursion into reserved sub-fields.
_RESERVED_REF_KEYS: Final[frozenset[str]] = frozenset({REF_KEY, TYPE_KEY, NAMESPACE_KEY})

__all__ = ["UNSET_NAMESPACE", "Catalog"]

_list = builtins.list  # Alias: Catalog.list shadows the built-in inside the class.


UNSET_NAMESPACE: Final[str] = "__MINT__"
"""Sentinel ``namespace`` value signalling "mint a fresh UUID on create".

The shard-06 "empty string" convention is rejected at the Pydantic layer
(``Entry.namespace`` is ``NonEmptyStr``), so the service owns the magic value.
Callers that want a newly-minted namespace construct
``Entry(namespace=UNSET_NAMESPACE, kind="team", ...)``; :meth:`Catalog.create`
substitutes a fresh ``uuid.uuid4()`` string before running the rest of the
write pipeline.
"""


_caller_user_id: ContextVar[str | None] = ContextVar(
    "akgentic_catalog_caller_user_id", default=None
)
"""Process-global caller identity for visibility filtering (ADR-009 §D2).

Stores the caller's ``user_id`` for the current request scope. Read by
:meth:`Catalog.list`, :meth:`Catalog.get`, and :meth:`Catalog.clone` to
filter results so non-owner callers only see entries in
``_meta.public is True`` namespaces.

* ``None`` (the default) — community tier; no caller identity is set, so
  no filtering is applied and every operation behaves identically to
  pre-Story-18.4 catalogs (un-set state preserves today's behaviour).
* Non-empty ``str`` — department / enterprise tiers; the upstream auth
  middleware sets the contextvar via :meth:`Catalog.as_caller` before
  invoking any ``Catalog.*`` method, and the service consults it for
  per-method visibility filtering.

The symbol is module-private (single underscore prefix); production code
uses :meth:`Catalog.as_caller` to set it. Tests may import the contextvar
directly for assertions only.
"""


class Catalog:
    """Unified catalog service — CRUD + clone + resolve + load_team.

    Constructed with a single ``EntryRepository`` dependency; holds no other
    state; performs no I/O in ``__init__``. All semantic concerns — namespace
    bootstrap, ownership propagation, clone dedup, ref reconciliation, delete
    guards — live here; the repository stays a narrow data plane.

    Example:
        >>> repo = YamlEntryRepository(tmp_path)
        >>> catalog = Catalog(repo)
        >>> team = Entry(id="team", kind="team", namespace=UNSET_NAMESPACE,
        ...              model_type="akgentic.team.models.TeamCard",
        ...              payload={...})
        >>> stored = catalog.create(team)  # namespace now a fresh UUID
        >>> stored.namespace
        '3fa85f64-5717-4562-b3fc-2c963f66afa6'
    """

    def __init__(self, repository: EntryRepository) -> None:
        """Store ``repository`` on ``self._repository``; no I/O.

        Args:
            repository: Any concrete ``EntryRepository`` implementation.

        Cross-namespace sharing (ADR-008 §D2 as updated 2026-05-08 rev 2)
        is data-driven: a namespace declares itself shareable through its
        own ``_meta`` entry (``payload["shareable"] is True``, a typed bool
        at the root). The catalog consults the meta entry on demand via
        :meth:`_is_namespace_shareable` and caches the answer on
        ``self._shareable_flag_cache`` for the lifetime of the instance;
        meta-entry mutations (any ``create``/``update``/``delete`` of a
        ``kind="meta"`` entry) invalidate the affected cache slot.
        """
        self._repository: EntryRepository = repository
        # Per-instance cache: namespace -> shareable boolean. Populated lazily
        # on first cross-ns ref resolution touching that namespace; invalidated
        # whenever a meta entry in that namespace is created / updated /
        # deleted (see ``_invalidate_meta_caches`` callsites in the write
        # paths).
        self._shareable_flag_cache: dict[str, bool] = {}
        # Per-instance cache: namespace -> public boolean. Mirrors
        # ``_shareable_flag_cache`` in shape and lifecycle — populated lazily
        # on first read of a namespace's ``_meta.public`` value, invalidated
        # by the same ``_invalidate_meta_caches`` helper on any meta-entry
        # mutation (ADR-009 §D2).
        self._public_flag_cache: dict[str, bool] = {}

    # --- Visibility / caller identity -----------------------------------------

    @classmethod
    @contextmanager
    def as_caller(cls, user_id: str) -> Iterator[None]:
        """Context manager setting the caller identity for visibility filtering.

        Entering sets the module-level ``_caller_user_id`` ContextVar to
        ``user_id``; exiting resets the contextvar to its previous token so
        nested ``as_caller`` calls compose correctly. The contextvar is
        process-global (not catalog-instance-scoped) — Python's
        :mod:`contextvars` semantics give each thread / asyncio Task its own
        snapshot, so concurrent requests are isolated from one another.

        Args:
            user_id: The caller's identifier. MUST be a non-empty string;
                the empty string ``""`` is rejected to mirror
                :class:`Entry.user_id`'s non-empty contract. ``None`` is not
                accepted — to clear the contextvar, simply do not enter
                ``as_caller`` (the default ``None`` value preserves
                community-tier "no filtering" behaviour).

        Raises:
            ValueError: When ``user_id`` is empty.

        Example:
            >>> with Catalog.as_caller("alice"):
            ...     entries = catalog.list(EntryQuery(kind="prompt"))
            ...     # entries are filtered to alice-owned + public-namespace.
        """
        if not user_id:
            raise ValueError("user_id must be a non-empty string")
        token = _caller_user_id.set(user_id)
        try:
            yield
        finally:
            _caller_user_id.reset(token)

    # --- Read -----------------------------------------------------------------

    def get(self, namespace: str, id: str) -> Entry:
        """Return the entry at ``(namespace, id)`` or raise ``EntryNotFoundError``.

        Visibility filter (ADR-009 §D2): when ``_caller_user_id`` is set
        and the entry is neither owned by the caller nor located in a
        ``_meta.public is True`` namespace, raise ``EntryNotFoundError``
        with the same message as the missing-target case (no information
        leakage about existence to non-owners). The meta entry itself
        (``id="_meta"``, ``kind="meta"``) is always visible when its
        namespace is public — denying access to it would create an
        unreachable invariant. Community tier (``_caller_user_id is None``)
        bypasses the filter and behaves as before.

        Args:
            namespace: The namespace of the entry.
            id: The id within ``namespace``.

        Returns:
            The stored ``Entry``.

        Raises:
            EntryNotFoundError: If the repository returns ``None`` for the
                ``(namespace, id)`` pair, or if the caller is not allowed
                to see the entry under the visibility filter.
        """
        entry = self._repository.get(namespace, id)
        if entry is None:
            raise EntryNotFoundError(f"Entry ({namespace}, {id}) not found")
        caller = _caller_user_id.get()
        if caller is not None and not self._visible_to_caller(entry, caller):
            raise EntryNotFoundError(f"Entry ({namespace}, {id}) not found")
        return entry

    def list(self, query: EntryQuery) -> _list[Entry]:
        """Return entries matching ``query`` — repository pass-through with visibility filter.

        Visibility filter (ADR-009 §D2): when ``_caller_user_id`` is set
        to a non-empty string ``<X>``, the repository's results are
        filtered so the returned list contains every entry ``e`` for
        which ``e.user_id == <X>`` OR ``self._is_namespace_public(e.namespace)``
        is ``True``. Community tier (``_caller_user_id is None``) returns
        the repository's results unfiltered — byte-identical to today's
        pass-through.
        """
        results = self._repository.list(query)
        caller = _caller_user_id.get()
        if caller is None:
            return results
        return [e for e in results if self._visible_to_caller(e, caller)]

    def _visible_to_caller(self, entry: Entry, caller: str) -> bool:
        """Return ``True`` iff ``caller`` may see ``entry`` under the visibility filter.

        The four-state rule (ADR-009 §D2):

        * Caller owns the entry (``entry.user_id == caller``) → visible.
        * Entry's namespace is public (``_meta.public is True``) → visible.
        * Otherwise → invisible.

        The meta entry of a public namespace is always visible — denying
        access to the very entry that declares the namespace public would
        create an unreachable invariant.

        Args:
            entry: The candidate entry.
            caller: The caller's ``user_id`` (non-empty by construction —
                callers reach this helper only after the ``_caller_user_id``
                contextvar test rejects ``None``).

        Returns:
            ``True`` when the entry is visible to ``caller`` under the
            four-state rule; ``False`` otherwise.
        """
        if entry.user_id == caller:
            return True
        return self._is_namespace_public(entry.namespace)

    def list_by_namespace(self, namespace: str) -> _list[Entry]:
        """Return every entry in ``namespace`` — repository pass-through."""
        return self._repository.list_by_namespace(namespace)

    def find_references(self, namespace: str, target_id: str) -> _list[Entry]:
        """Return entries in ``namespace`` referencing ``target_id`` — pass-through."""
        return self._repository.find_references(namespace, target_id)

    # --- Write ----------------------------------------------------------------

    def create(self, entry: Entry) -> Entry:
        """Persist a new entry with gating, minting, and ref reconciliation.

        Pipeline:

        1. If ``entry.kind == "team"`` and ``entry.namespace == UNSET_NAMESPACE``:
           mint a fresh ``uuid.uuid4()`` and substitute it.
        2. Reject duplicates via ``_check_duplicate`` — raises
           ``CatalogValidationError`` if ``(namespace, id)`` already exists.
        3. Reject a second ``kind="meta"`` entry in the same namespace via
           ``_check_meta_singleton`` (ADR-008 §D1). Skipped for ``kind!="meta"``.
        4. Non-team, non-meta entries: run ``_check_namespace_initialized`` then
           ``_check_ownership``. Team and meta entries skip both (they ARE anchor
           entries that bootstrap the namespace).
        5. Run ``prepare_for_write`` — ref resolution, class load, Pydantic
           validation, dump, reconcile. ``CatalogValidationError`` propagates
           unchanged.
        6. Call ``repository.put`` with the prepared entry; return it.

        Args:
            entry: Candidate entry to create. For team entries with a sentinel
                namespace, :attr:`entry.namespace` MUST equal
                :data:`UNSET_NAMESPACE`.

        Returns:
            The persisted ``Entry``, carrying the minted namespace if applicable
            and the reconciled payload from ``prepare_for_write``.

        Raises:
            CatalogValidationError: On duplicate, bootstrap, ownership, or
                ``prepare_for_write`` failure.
        """
        if entry.kind == "team" and entry.namespace == UNSET_NAMESPACE:
            entry = self._mint_team_namespace(entry)

        self._check_duplicate(entry.namespace, entry.id)
        self._check_meta_singleton(entry)

        if entry.kind not in ("team", "meta"):
            self._check_namespace_initialized(entry.namespace)
            self._check_ownership(entry)

        prepared = prepare_for_write(
            entry,
            self._repository,
            is_namespace_shareable=self._is_namespace_shareable,
        )
        self._repository.put(prepared)
        self._invalidate_meta_caches(prepared)
        return prepared

    def update(self, entry: Entry) -> Entry:
        """Update an existing entry; re-run ref-reconciliation and ownership.

        Pipeline:

        1. Existence check: ``repository.get`` MUST return non-``None``.
        2. Run ``prepare_for_write`` (may raise ``CatalogValidationError``).
        3. For non-team entries, re-run ``_check_ownership`` on the prepared
           shape so validator normalisations are honoured. Team entries are
           authoritative for their own ``user_id`` and skip this check —
           changing a team's ``user_id`` is a deliberate ownership transfer
           that leaves sub-entries inconsistent until a caller-side migration
           follows up (not this service's concern).
        4. ``repository.put`` + return.

        ``update`` NEVER mints a namespace; the empty-string / sentinel path
        is exclusive to :meth:`create`.

        Args:
            entry: The candidate entry; its ``(namespace, id)`` MUST already
                exist.

        Returns:
            The prepared, persisted ``Entry``.

        Raises:
            EntryNotFoundError: If no entry exists at ``(entry.namespace, entry.id)``.
            CatalogValidationError: From ``prepare_for_write`` or ``_check_ownership``.
        """
        existing = self._repository.get(entry.namespace, entry.id)
        if existing is None:
            raise EntryNotFoundError(f"Entry ({entry.namespace}, {entry.id}) not found")
        del existing  # Existence was the only invariant we needed.
        prepared = prepare_for_write(
            entry,
            self._repository,
            is_namespace_shareable=self._is_namespace_shareable,
        )
        if prepared.kind != "team":
            self._check_ownership(prepared)
        self._repository.put(prepared)
        self._invalidate_meta_caches(prepared)
        return prepared

    def delete(self, namespace: str, id: str) -> None:
        """Delete ``(namespace, id)``, guarded by ``validate_delete``.

        ``EntryNotFoundError`` fires for missing targets (distinguished from
        inbound-ref blockers). ``CatalogValidationError`` fires when inbound
        refs exist and carries each referring entry's id in the error message.

        Args:
            namespace: The namespace of the entry to delete.
            id: The id within ``namespace``.

        Raises:
            EntryNotFoundError: If the target does not exist.
            CatalogValidationError: If any inbound refs would be broken.
        """
        target = self._repository.get(namespace, id)
        if target is None:
            raise EntryNotFoundError(f"Entry ({namespace}, {id}) not found")
        errors = validate_delete(namespace, id, self._repository)
        # ADR-008 §D2 (updated 2026-05-08 rev 2) — when the deleted entry's
        # namespace is shareable (its `_meta` carries
        # `payload["shareable"] is True`), widen the guard to a global-scope
        # check so cross-tenant referrers also block the delete. Otherwise,
        # the shareable-flag gate would have rejected any cross-ns referrer
        # at create time, so the namespace-local check is sufficient.
        if self._is_namespace_shareable(namespace):
            global_referrers = self._repository.find_references_global(namespace, id)
            errors = errors + [
                f"Entry '{r.id}' (kind={r.kind}) in namespace '{r.namespace}' references '{id}'"
                for r in global_referrers
            ]
        if errors:
            raise CatalogValidationError(errors)
        self._repository.delete(namespace, id)
        self._invalidate_meta_caches(target)

    # --- Clone ----------------------------------------------------------------

    def clone(
        self,
        src_namespace: str,
        src_id: str,
        dst_namespace: str,
        dst_user_id: str = "anonymous",
    ) -> Entry:
        """Deep-copy an entry tree into ``dst_namespace`` with ref rewrite and dedup.

        Semantics (pinned by ADR-07 clone):

        * Id preservation — when ``dst_namespace != src_namespace`` the source
          id is reused; when equal, a numeric suffix (``-2``, ``-3``, …) is
          appended until no collision exists in the repository or in the
          intra-call dedup map.
        * Deduplication — an intra-call ``cloned`` map keyed by the source
          ``(namespace, id)`` pair guarantees every source sub-entry is cloned
          exactly once per ``clone`` call.
        * Atomicity — every ``put`` is deferred until the entire recursive
          resolution completes. A mid-resolution failure leaves
          ``dst_namespace`` untouched.
        * No ``prepare_for_write`` — source entries were validated when they
          were created; cloning is a structural copy + ref rewrite.
        * Ownership — satisfied by construction (every cloned entry's
          ``user_id`` is stamped to ``dst_user_id``), so ``_check_ownership``
          is not re-run per-write.

        Args:
            src_namespace: Source namespace containing the entry to clone.
            src_id: Id of the source entry within ``src_namespace``.
            dst_namespace: Destination namespace receiving the cloned entries.
            dst_user_id: ``user_id`` to stamp on every cloned entry. Defaults
                to ``"anonymous"`` for community-tier deployments; department
                / enterprise tiers pass the authenticated caller's identifier.

        Returns:
            The top-level cloned entry, as freshly re-read from the repository.

        Visibility filter (ADR-009 §D2): when ``_caller_user_id`` is set
        and the caller is neither the source's owner nor cloning from a
        ``_meta.public is True`` namespace, raise ``CatalogValidationError``
        with a message of the shape
        ``"Caller '<X>' may not clone source ..."``. The visibility check
        fires AFTER the existence check (so deleted sources surface
        ``EntryNotFoundError`` as before) and BEFORE the recursive
        ``_clone_one`` walk. ``dst_user_id`` is NOT cross-checked against
        the caller — destination-side stamping is RBAC, out of scope.

        Raises:
            EntryNotFoundError: If the source entry does not exist.
            CatalogValidationError: If the source graph references a missing
                entry (atomicity guarantees zero destination writes on this
                path), or if the caller is not allowed to clone the source
                under the visibility filter.
        """
        src_entry = self._repository.get(src_namespace, src_id)
        if src_entry is None:
            raise EntryNotFoundError(f"Source entry ({src_namespace}, {src_id}) not found")
        caller = _caller_user_id.get()
        if caller is not None and not self._visible_to_caller(src_entry, caller):
            raise CatalogValidationError(
                [
                    f"Caller '{caller}' may not clone source ({src_namespace}, {src_id}): "
                    f"not the owner and the source namespace is not public"
                ]
            )
        cloned: dict[tuple[str, str], str] = {}
        pending_writes: _list[Entry] = []
        top_new_id = self._clone_one(
            src_namespace=src_namespace,
            src_id=src_id,
            cloned=cloned,
            pending_writes=pending_writes,
            dst_namespace=dst_namespace,
            dst_user_id=dst_user_id,
        )
        for entry in pending_writes:
            self._repository.put(entry)
        top = self._repository.get(dst_namespace, top_new_id)
        if top is None:  # pragma: no cover — defensive; put just stored it
            raise CatalogValidationError(
                [f"Clone post-write lookup failed for ({dst_namespace}, {top_new_id})"]
            )
        return top

    # --- Resolve --------------------------------------------------------------

    def resolve(self, entry: Entry) -> BaseModel:
        """Hydrate ``entry`` into a runtime Pydantic instance — delegates to resolver."""
        return _resolve(
            entry,
            self._repository,
            is_namespace_shareable=self._is_namespace_shareable,
        )

    def resolve_by_id(self, namespace: str, id: str) -> BaseModel:
        """Convenience: ``self.resolve(self.get(namespace, id))``."""
        return self.resolve(self.get(namespace, id))

    def load_team(self, namespace: str) -> TeamCard:
        """Load and resolve the ``kind="team"`` entry in ``namespace`` into a ``TeamCard``.

        Issues exactly ONE ``list_by_namespace`` call against the real
        repository, then builds a pre-loaded in-memory wrapper to short-circuit
        every per-ref ``get`` call ``populate_refs`` would otherwise make. A
        defensive runtime ``isinstance(result, TeamCard)`` check catches
        misconfigured team entries (those whose ``model_type`` points at a
        non-``TeamCard`` class) before they leak out.

        Args:
            namespace: The namespace to load.

        Returns:
            The resolved :class:`TeamCard` for ``namespace``.

        Raises:
            CatalogValidationError: If no team entry exists in ``namespace``
                or if the team entry's ``model_type`` resolves to a class
                other than :class:`TeamCard`.

        Before returning, ``name`` / ``description`` are projected from the
        namespace's ``_meta`` entry (or the namespace identifier for ``name``;
        empty string for ``description``) when the team entry leaves them
        ``None`` or empty — see :meth:`_project_team_display`.
        """
        entries = self._repository.list_by_namespace(namespace)
        team_entries = [e for e in entries if e.kind == "team"]
        if not team_entries:
            raise CatalogValidationError([f"Namespace '{namespace}' has no team entry"])
        team_entry = team_entries[0]
        # Fall back to the live repository for cross-ns ref lookups so a team
        # payload that references entries in a shareable namespace (e.g.
        # ``global.shared-prompt``) still resolves while preserving the
        # single-query invariant for the local namespace. The fallback is
        # always wired — the shareable-flag gate (consulted via
        # ``self._is_namespace_shareable``) is the authoritative permission
        # check, not the presence of a fallback.
        in_memory = _InMemoryEntryRepository(entries, fallback=self._repository)
        result = _resolve(
            team_entry,
            in_memory,
            is_namespace_shareable=self._is_namespace_shareable,
        )
        if not isinstance(result, TeamCard):
            raise CatalogValidationError(
                [f"Team entry's model_type resolved to {type(result).__name__}, expected TeamCard"]
            )
        return self._project_team_display(result, namespace, entries)

    def _project_team_display(
        self,
        team_card: TeamCard,
        namespace: str,
        entries: _list[Entry],
    ) -> TeamCard:
        """Project ``name`` / ``description`` from ``_meta`` onto ``team_card``.

        Project ``name`` and ``description`` from the namespace's ``_meta``
        entry (or the namespace identifier as last-resort fallback for
        ``name``; empty string for ``description``) onto the resolved
        ``TeamCard`` when those fields are ``None`` or empty. Existing
        non-empty values are preserved verbatim. Mirrors the namespace-picker
        projection rule from ``_build_namespace_summary`` so the display data
        a runtime consumer reads off ``team_card.name`` matches what the
        picker shows.

        ``entries`` is read-only — the meta entry's payload is NOT mutated;
        a new ``TeamCard`` is produced via ``model_copy`` when projection
        fires. Returns ``team_card`` unchanged when neither field needs
        projecting.
        """
        meta_entry, _ = _partition_meta(entries)
        meta_payload: dict[str, Any] = {}
        if meta_entry is not None and isinstance(meta_entry.payload, dict):
            meta_payload = meta_entry.payload

        update: dict[str, str] = {}
        if not team_card.name:
            raw_meta_name = meta_payload.get("name")
            if isinstance(raw_meta_name, str) and raw_meta_name != "":
                update["name"] = raw_meta_name
            else:
                update["name"] = namespace
        if not team_card.description:
            meta_description = meta_entry.description if meta_entry is not None else ""
            update["description"] = meta_description if meta_description else ""

        if not update:
            return team_card
        return team_card.model_copy(update=update)

    # --- Namespace bundle export / import -------------------------------------

    def export_namespace_yaml(self, namespace: str) -> str:
        """Return ``namespace`` serialised as a single-bundle YAML document.

        Story 17.6 — the bundle carries six top-level keys (in declaration
        order): ``namespace``, ``user_id``, ``name``, ``description``,
        ``properties``, ``entries``. The ``name`` / ``description`` /
        ``properties`` trio is projected from the namespace's ``_meta`` entry
        when present, with team-payload fallback otherwise (per ADR-008 §D1
        and Story 17.6 AC2). The ``_meta`` entry itself is hoisted to the
        header — it does NOT appear in ``entries:``. ``entries:`` is a
        YAML mapping (dict-keyed-by-id for local entries, dict-keyed-by-
        ``<ns>.<id>`` for cross-namespace external entries; external entries
        are sorted within per-kind sections marked ``External ref, readonly``).

        Pipeline:

        1. ``entries = list_by_namespace(namespace)`` — single repository call.
        2. ``meta_entry, local_entries = _partition_meta(entries)`` —
           separate the canonical ``_meta`` entry from regular catalog
           entries; the ``_meta`` entry is hoisted to header fields.
        3. Compute ``name`` / ``description`` / ``properties`` from
           ``meta_entry.payload`` when present; fall back to
           ``(team.payload.get("name", ""), team.description, {})`` when
           absent (per AC2). When ``meta_entry.payload.name`` is empty, fall
           back to the team for ``name`` only (description and properties
           still come from the meta entry).
        4. ``external_refs = self._collect_external_refs(local_entries)`` —
           transitive cross-ns target collection (carried over from Story
           17.5; algorithm unchanged). Local entries (not the meta entry)
           are passed in so the meta payload's properties dict does not
           introduce spurious cross-ns walks.
        5. Return ``dump_namespace(local_entries, name=name,
           description=description, properties=properties,
           external_refs=external_refs)``.

        An empty namespace surfaces the bundle-must-declare-entry error
        from ``dump_namespace`` unchanged; the router maps it to HTTP 409.

        Args:
            namespace: The namespace to export.

        Returns:
            YAML string following the Story 17.6 bundle format.

        Raises:
            CatalogValidationError: If ``namespace`` has no entries, or if
                the loaded entries violate the bundle uniform-owner /
                uniform-namespace invariants (should not happen in practice
                because the Catalog service enforces ownership on write).
        """
        entries = self._repository.list_by_namespace(namespace)
        meta_entry, local_entries = _partition_meta(entries)
        name, description, properties, shareable, public = self._project_header(
            meta_entry, local_entries
        )
        external_refs = self._collect_external_refs(local_entries)
        return dump_namespace(
            local_entries,
            name=name,
            description=description,
            properties=properties,
            shareable=shareable,
            public=public,
            external_refs=external_refs,
        )

    def _project_header(
        self,
        meta_entry: Entry | None,
        local_entries: _list[Entry],
    ) -> tuple[str, str, dict[str, str], bool, bool]:
        """Project the header trio + flags for ``dump_namespace``.

        Returns ``(name, description, properties, shareable, public)``.

        When ``meta_entry`` is present, read from its payload. When the
        meta's ``payload.name`` is empty / missing, fall back to the team
        entry's payload-name for the ``name`` field only — description and
        properties still come from the meta entry (mirrors the existing
        ``list_namespaces`` graceful-degradation rule for empty meta names).

        Story 17.7 — ``shareable`` is read from
        ``meta_entry.payload.get("shareable")`` with strict-bool ``is True``
        comparison; ``False`` when the key is missing or non-bool. When
        ``meta_entry`` is absent, ``shareable`` defaults to ``False``.

        Story 18.2 — ``public`` is read from
        ``meta_entry.payload.get("public")`` with strict-bool ``is True``
        comparison; ``False`` when the key is missing or non-bool. When
        ``meta_entry`` is absent, ``public`` defaults to ``False`` (ADR-009 §D2).

        When ``meta_entry`` is absent, fall back fully to the team:
        ``(team.payload.get("name", ""), team.description, {}, False, False)``.
        When the team entry is also absent (defensive — should not happen
        in practice because the catalog service enforces a team-bootstrap
        invariant), return empty defaults.

        Args:
            meta_entry: The namespace's ``_meta`` entry, or ``None``.
            local_entries: The non-meta entries of the namespace; the team
                entry is located inside this list.

        Returns:
            ``(name, description, properties, shareable, public)`` 5-tuple —
            the header projection ready to pass to :func:`dump_namespace`.
        """
        team_entry = next((e for e in local_entries if e.kind == "team"), None)
        team_name = ""
        team_description = ""
        if team_entry is not None:
            team_payload = team_entry.payload if isinstance(team_entry.payload, dict) else {}
            raw_name = team_payload.get("name", "")
            team_name = raw_name if isinstance(raw_name, str) else ""
            team_description = team_entry.description

        if meta_entry is None:
            return team_name, team_description, {}, False, False

        payload = meta_entry.payload if isinstance(meta_entry.payload, dict) else {}
        meta_name = payload.get("name", "")
        meta_description = payload.get("description", "")
        meta_properties_raw = payload.get("properties", {})
        if not isinstance(meta_name, str):
            meta_name = ""
        if not isinstance(meta_description, str):
            meta_description = ""
        meta_properties: dict[str, str] = {}
        if isinstance(meta_properties_raw, dict):
            meta_properties = {
                str(k): str(v) for k, v in meta_properties_raw.items() if isinstance(k, str)
            }
        # Strict-bool projection — only a real ``True`` flips the flag.
        meta_shareable = payload.get("shareable") is True
        meta_public = payload.get("public") is True
        # Empty-meta-name graceful degradation — team-fallback for name only.
        name = meta_name if meta_name else team_name
        return name, meta_description, meta_properties, meta_shareable, meta_public

    def _collect_external_refs(self, entries: _list[Entry]) -> _list[Entry]:
        """Return the deduplicated, shareable-flag-filtered, transitively-reached cross-ns targets.

        Worklist algorithm carried over from Story 17.5 (unchanged):

        1. Seed the worklist with every cross-ns target reachable from
           ``entries[*].payload`` via :func:`_iter_cross_ns_targets`,
           skipping pairs whose namespace is the bundle's own namespace
           (same-namespace short-circuit even when the marker carried an
           explicit ``__namespace__`` matching the bundle's namespace).
        2. Maintain a ``visited`` set of ``(namespace, id)`` pairs already
           processed (cycle protection — same shape as the resolver's
           cycle set).
        3. For each pair popped from the worklist:
           - Skip if already in ``visited``.
           - Skip if the target namespace is not shareable (silent omission per
             ADR-008 §D2).
           - Try ``repository.get(target_ns, target_id)``; on
             ``EntryNotFoundError`` semantics (``None``), skip silently.
           - Append to the result list and mark visited.
           - Walk the target's payload via ``_iter_cross_ns_targets``;
             enqueue every produced pair whose target namespace is NOT the
             bundle's namespace AND NOT the target entry's own namespace
             (same-namespace refs inside a cross-ns target's payload do NOT
             widen the section).
        4. Sort the result by ``(namespace, kind, id)`` ascending.

        Returns an empty list when ``entries`` is empty or carries no
        cross-ns refs. The ``visited`` set IS used to short-circuit
        re-processing — repeated pops of the same pair never re-fetch the
        repository (cycle protection).
        """
        if not entries:
            return []

        bundle_namespace = entries[0].namespace
        worklist: _list[tuple[str, str]] = []
        for entry in entries:
            for target_ns, target_id in _iter_cross_ns_targets(entry.payload):
                if target_ns != bundle_namespace:
                    worklist.append((target_ns, target_id))

        visited: set[tuple[str, str]] = set()
        collected: _list[Entry] = []

        while worklist:
            target_ns, target_id = worklist.pop(0)
            key = (target_ns, target_id)
            if key in visited:
                continue
            visited.add(key)
            if not self._is_namespace_shareable(target_ns):
                continue
            target_entry = self._repository.get(target_ns, target_id)
            if target_entry is None:
                continue
            collected.append(target_entry)
            for nested_ns, nested_id in _iter_cross_ns_targets(target_entry.payload):
                # Same-ns refs inside a cross-ns target's payload do NOT
                # widen the external section (the local refs belong to the
                # target's own namespace; the frontend renders the target
                # as opaque).
                if nested_ns == target_ns:
                    continue
                # Cross-ns refs that resolve back to the bundle's own
                # namespace are not external refs.
                if nested_ns == bundle_namespace:
                    continue
                worklist.append((nested_ns, nested_id))

        # Sort by (namespace, kind, id) for stable diffs.
        collected.sort(key=lambda e: (e.namespace, e.kind, e.id))
        return collected

    def import_namespace_yaml(self, yaml_text: str) -> _list[Entry]:
        """Import a bundle YAML document as an atomic namespace replacement.

        Story 17.6 — six-step pipeline, every pre-write step raising on failure:

        1. ``parsed, header = load_namespace(yaml_text)`` — structural
           validation. Composite-keyed external entries (cross-namespace
           targets) are SKIPPED by ``load_namespace`` — they belong to other
           namespaces and are not part of this namespace's atomic-replace
           contents.
        2. Run ``prepare_for_write`` on each parsed entry. Any failure aborts
           the import with no repository writes.
        3. Bundle invariants: exactly one team entry, uniform user_id within
           the bundle (reuses the ``_check_ownership`` shape).
        4. Cross-entry ref check: every same-namespace ``__ref__`` marker
           target MUST be an id present in the bundle.
        5. Atomic replace: compute the id difference against the current
           namespace state, delete stale non-team entries then stale team,
           then put team first and non-team sorted by id.
        6. Header upsert: when ``header.present`` is True, upsert the
           namespace's ``_meta`` entry with the bundle's header fields. The
           upsert is part of the atomic sequence — it runs after the entries
           replace and uses the same overlay repository so any bundle-side
           ref-resolution invariants remain consistent. Pre-17.5 bundles
           (no header at all) skip the meta upsert and leave the existing
           ``_meta`` entry (if any) untouched.

        ``CatalogValidationError`` from any step propagates unchanged; the
        atomic-failure contract guarantees the repository stays untouched
        until every pre-write check has passed.

        Args:
            yaml_text: The full bundle YAML document body.

        Returns:
            The prepared entries in the order they were persisted — team
            first, then non-team sorted by ``id``. The hoisted ``_meta``
            entry (if upserted) is NOT in this list — it is an atomic side-
            effect of the bundle import, not a bundle entry.

        Raises:
            CatalogValidationError: On any validation-phase failure (parse,
                prepare-for-write, bundle invariants, dangling refs, meta
                upsert validation).
        """
        parsed, header = load_namespace(yaml_text)
        # Bundle-internal refs (e.g., an agent payload referring to a sibling
        # model id in the same bundle) must resolve during prepare_for_write,
        # even when those sibling entries are not yet in the repository. Stage
        # the bundle into an overlay repository so `populate_refs` can find
        # bundle-internal targets alongside current namespace state.
        overlay = _BundleOverlayRepository(self._repository, parsed)
        prepared = [
            prepare_for_write(
                e,
                overlay,
                is_namespace_shareable=self._is_namespace_shareable,
            )
            for e in parsed
        ]
        self._validate_bundle_invariants(prepared, has_header_meta=header.present)
        self._check_bundle_refs(prepared)
        namespace = prepared[0].namespace
        ordered = self._order_bundle_for_put(prepared)
        # Header upsert is part of the atomic sequence; validate the
        # meta-entry payload up-front so a header-shape failure aborts the
        # import BEFORE any repository writes (atomic-failure contract).
        meta_to_upsert: Entry | None = None
        if header.present:
            meta_to_upsert = self._build_meta_entry_for_upsert(namespace, prepared, header)
        # AC11 / AC12 — pre-17.5 bundles (no header trio) leave the existing
        # `_meta` entry untouched. Pass `preserve_meta=True` so the atomic
        # swap does NOT sweep an existing `_meta` out from under a legacy
        # bundle. When the bundle DOES carry a header, the meta upsert
        # immediately re-creates `_meta` after the swap, so the swap may
        # safely delete the prior meta.
        preserve_meta = not header.present
        self._apply_atomic_swap(namespace, ordered, preserve_meta=preserve_meta)
        if meta_to_upsert is not None:
            self._upsert_meta_entry(meta_to_upsert)
        return ordered

    def _build_meta_entry_for_upsert(
        self,
        namespace: str,
        prepared: _list[Entry],
        header: BundleHeader,
    ) -> Entry:
        """Build the ``_meta`` entry to upsert from the bundle's header fields.

        Constructs (but does NOT persist) the ``_meta`` entry; validates the
        ``NamespaceMeta`` payload immediately so a header-shape failure
        aborts the import BEFORE any repository writes are committed
        (atomic-failure contract). The payload is constructed via
        ``NamespaceMeta(...).model_dump()`` so a future schema change to
        ``NamespaceMeta`` automatically flows through.

        Args:
            namespace: The bundle's namespace (already pinned by uniform-
                namespace invariant on the prepared entries).
            prepared: The prepared bundle entries; consulted to inherit the
                ``user_id`` from the team entry (preferred) or from the
                first entry when no team is present (meta-only bundle) so
                the meta entry's ownership stays consistent with the bundle.
            header: The :class:`BundleHeader` projected by ``load_namespace``.

        Returns:
            The constructed ``_meta`` entry ready for upsert.

        Raises:
            CatalogValidationError: When ``header`` does not yield a valid
                ``NamespaceMeta`` payload (e.g. empty name).
        """
        team_entry = next((e for e in prepared if e.kind == "team"), None)
        if team_entry is not None:
            user_id = team_entry.user_id
        elif prepared:
            # Meta-only bundle: inherit user_id from the first entry so the
            # upserted meta entry stays ownership-consistent with the bundle
            # contents (Story 17.10). After Story 18.1, ``prepared[0].user_id``
            # is always a real string ("anonymous" by default).
            user_id = prepared[0].user_id
        else:
            # Defensive: a header-only bundle with zero prepared entries
            # cannot reach this branch (load_namespace rejects empty
            # entries). Fall back to "anonymous" so the constructed meta
            # entry still satisfies the tightened Entry.user_id field.
            user_id = "anonymous"
        try:
            meta_payload = NamespaceMeta(
                name=header.name,
                description=header.description,
                properties=dict(header.properties),
                shareable=header.shareable,
                public=header.public,
            ).model_dump()
        except ValueError as exc:
            raise CatalogValidationError([f"bundle header is invalid: {exc}"]) from exc
        return Entry(
            id="_meta",
            kind="meta",
            namespace=namespace,
            user_id=user_id,
            model_type=_NAMESPACE_META_TYPE,
            payload=meta_payload,
        )

    def _upsert_meta_entry(self, meta_entry: Entry) -> None:
        """Upsert ``meta_entry`` via :meth:`update` if exists else :meth:`create`.

        Goes through the standard write pipeline so ``prepare_for_write`` /
        ownership / shareable-flag-cache invalidation all fire normally. The
        meta-singleton check inside :meth:`create` is satisfied by the
        canonical id ``"_meta"`` — at most one meta entry per namespace.
        """
        existing = self._repository.get(meta_entry.namespace, meta_entry.id)
        if existing is None:
            self.create(meta_entry)
        else:
            self.update(meta_entry)

    # --- Namespace validation -------------------------------------------------

    def validate_namespace(self, namespace: str) -> NamespaceValidationReport:
        """Validate the persisted state of ``namespace`` (shard 05 algorithm).

        Delegates to :func:`akgentic.catalog.validation.validate_entries` after
        one ``list_by_namespace`` call. Never raises; empty or failing
        namespaces return a report with ``ok=False`` and the relevant error
        lists. The report's ``namespace`` is patched back to the caller's
        ``namespace`` when ``validate_entries`` saw zero entries, so the caller
        sees the requested label in the report even on an empty namespace.

        The shareable-flag check (per ADR-008 §D2 as updated 2026-05-08) is
        threaded into ``validate_entries`` so transient validation surfaces
        cross-ns errors (shareable-flag violations, ownership violations) per
        entry.
        """
        entries = self._repository.list_by_namespace(namespace)
        report = validate_entries(
            entries,
            self._repository,
            is_namespace_shareable=self._is_namespace_shareable,
        )
        if report.namespace is None:
            report = report.model_copy(update={"namespace": namespace})
        return report

    def validate_namespace_yaml(self, yaml_text: str) -> NamespaceValidationReport:
        """Dry-run validate a proposed bundle YAML without touching the repository.

        Pipeline (never raises; always returns a report):

        1. Parse ``yaml_text`` via :func:`load_namespace`; on
           :class:`CatalogValidationError`, return a report carrying the load
           errors in ``global_errors`` with ``namespace=None`` and
           ``ok=False`` — no ``entry_issues`` are populated on this path (the
           bundle did not parse into entries; nothing per-entry to report).
        2. On a successful parse, delegate to
           :func:`validate_entries(entries, self._repository)`.

        The in-bundle dangling-ref walker (shared with the persisted flow) and
        the ``populate_refs``-backed transient validation are complementary
        checks: the first catches bundle-integrity failures, the second covers
        runtime resolvability through the live repository. A bundle that
        references an id present in the persisted namespace but absent from
        the bundle will be flagged by the bundle walker, not by
        ``populate_refs``.
        """
        try:
            entries, _header = load_namespace(yaml_text)
        except CatalogValidationError as exc:
            return NamespaceValidationReport(
                namespace=None,
                ok=False,
                global_errors=list(exc.errors),
                entry_issues=[],
            )
        return validate_entries(
            entries,
            self._repository,
            is_namespace_shareable=self._is_namespace_shareable,
        )

    # --- Private helpers ------------------------------------------------------

    def _validate_bundle_invariants(
        self,
        prepared: _list[Entry],
        *,
        has_header_meta: bool = False,
    ) -> None:
        """Enforce anchor presence, uniform user_id, uniform namespace on a parsed bundle.

        Runs after ``prepare_for_write`` so validator-normalised fields are
        honoured. Collects every violation into a single
        ``CatalogValidationError`` so the UI can surface them in one pass.

        Args:
            prepared: The prepared bundle entries.
            has_header_meta: ``True`` when the bundle carries a header with
                ``present=True`` — the meta entry is hoisted to the header
                and will be upserted separately, so it counts as an anchor
                even though it is not in ``prepared``.
        """
        errors: list[str] = []
        team_entries = [e for e in prepared if e.kind == "team"]
        meta_entries = [e for e in prepared if e.kind == "meta"]
        effective_has_meta = len(meta_entries) > 0 or has_header_meta

        if len(team_entries) == 0 and not effective_has_meta:
            errors.append(
                "bundle has no team entry and no meta entry "
                "— at least one anchor (team or meta) is required"
            )
        if len(team_entries) > 1:
            ids = sorted(e.id for e in team_entries)
            errors.append(
                f"bundle has multiple team entries: {ids} — exactly one `kind=team` entry "
                f"is required"
            )

        if len(meta_entries) > 1:
            meta_ids = sorted(e.id for e in meta_entries)
            namespace_for_msg = prepared[0].namespace if prepared else ""
            errors.append(
                f"namespace '{namespace_for_msg}' has multiple meta entries: {meta_ids} — "
                f"exactly one kind=meta entry is allowed per namespace"
            )

        # Anchor resolution for user_id uniformity: team preferred, meta fallback.
        # When the bundle has a header meta (has_header_meta=True) but no
        # team/meta entry in prepared, the anchor is the header meta whose
        # user_id is derived from the first entry — skip explicit ownership
        # checks and just verify namespace uniformity.
        anchor_entry: Entry | None = None
        anchor_kind = ""
        if team_entries:
            anchor_entry = team_entries[0]
            anchor_kind = "team"
        elif meta_entries:
            anchor_entry = meta_entries[0]
            anchor_kind = "meta"

        if anchor_entry is not None:
            expected_user = anchor_entry.user_id
            expected_ns = anchor_entry.namespace
            for e in prepared:
                if e.kind in ("team", "meta"):
                    continue
                if e.user_id != expected_user:
                    errors.append(
                        f"Ownership mismatch in namespace '{expected_ns}': "
                        f"entry '{e.id}' has user_id={e.user_id!r} but "
                        f"anchor ({anchor_kind}) has user_id={expected_user!r}"
                    )
                if e.namespace != expected_ns:
                    errors.append(
                        f"entry '{e.id}' has namespace={e.namespace!r} but bundle "
                        f"namespace is {expected_ns!r}"
                    )
            # Also check namespace uniformity for the anchors themselves.
            for e in prepared:
                if e.kind in ("team", "meta") and e is not anchor_entry:
                    if e.namespace != expected_ns:
                        errors.append(
                            f"entry '{e.id}' has namespace={e.namespace!r} but bundle "
                            f"namespace is {expected_ns!r}"
                        )
        if errors:
            raise CatalogValidationError(errors)

    def _check_bundle_refs(self, prepared: _list[Entry]) -> None:
        """Reject bundles that carry ``__ref__`` targets not present in the bundle.

        Cross-namespace refs are disallowed by construction — every ref target
        MUST be an id declared in the bundle's ``entries`` map. ``__ref__``
        markers whose target id is absent collect into a single
        ``CatalogValidationError``.
        """
        bundle_ids = {e.id for e in prepared}
        missing: list[str] = []
        for entry in prepared:
            for target_id in _iter_ref_targets(entry.payload):
                if target_id not in bundle_ids:
                    missing.append(f"bundle __ref__ '{target_id}' not found in bundle")
        if missing:
            raise CatalogValidationError(missing)

    def _order_bundle_for_put(self, prepared: _list[Entry]) -> _list[Entry]:
        """Return ``prepared`` reordered as team first, then non-team sorted by id."""
        team = [e for e in prepared if e.kind == "team"]
        non_team = sorted((e for e in prepared if e.kind != "team"), key=lambda e: e.id)
        return team + non_team

    def _apply_atomic_swap(
        self,
        namespace: str,
        ordered: _list[Entry],
        *,
        preserve_meta: bool = False,
    ) -> None:
        """Replace ``namespace`` state with ``ordered`` in two passes.

        Delete non-team entries from the current namespace that are not in
        the bundle, then delete the team entry if it was dropped, then put
        team first, then put the remaining entries in the ordered sequence.
        Delete ordering preserves the bootstrap invariant across the swap;
        put ordering satisfies :meth:`Catalog.create`'s bootstrap gate in
        the "net-new team + sub-entries" path.

        Story 17.6 — when ``preserve_meta`` is True, an existing `_meta`
        entry in the namespace is excluded from the stale-sweep regardless
        of whether the bundle declares one. This is the legacy / pre-17.5
        bundle path: the bundle carries no top-level header trio, so the
        meta upsert is skipped, and the existing meta entry must be left
        untouched (AC11).

        Args:
            namespace: The namespace whose state is being replaced.
            ordered: The bundle's prepared entries (team first, then sorted
                non-team).
            preserve_meta: When True, exclude the canonical ``_meta`` entry
                (id="_meta", kind="meta") from the stale-sweep. Default
                False — the standard atomic-replace behaviour.
        """
        current = self._repository.list_by_namespace(namespace)
        bundle_ids = {e.id for e in ordered}
        stale = [e for e in current if e.id not in bundle_ids]
        if preserve_meta:
            stale = [e for e in stale if not (e.id == "_meta" and e.kind == "meta")]
        stale_non_team = [e for e in stale if e.kind != "team"]
        stale_team = [e for e in stale if e.kind == "team"]
        for e in stale_non_team:
            self._repository.delete(namespace, e.id)
            self._invalidate_meta_caches(e)
        for e in stale_team:
            self._repository.delete(namespace, e.id)
            self._invalidate_meta_caches(e)
        for e in ordered:
            self._repository.put(e)
            self._invalidate_meta_caches(e)

    def _mint_team_namespace(self, entry: Entry) -> Entry:
        """Return a copy of ``entry`` with ``namespace`` set to a fresh UUID string."""
        return entry.model_copy(update={"namespace": str(uuid.uuid4())})

    def _check_duplicate(self, namespace: str, id: str) -> None:
        """Raise ``CatalogValidationError`` if ``(namespace, id)`` already exists."""
        if self._repository.get(namespace, id) is not None:
            raise CatalogValidationError([f"Entry ({namespace}, {id}) already exists"])

    def _check_meta_singleton(self, entry: Entry) -> None:
        """Reject a second ``kind="meta"`` entry in ``entry.namespace`` (ADR-008 §D1).

        Short-circuits when ``entry.kind != "meta"`` — every existing pipeline
        for ``team`` / ``agent`` / ``tool`` / ``model`` / ``prompt`` is byte-
        identical. For ``kind="meta"``, queries the repository for any existing
        meta entry in the same namespace and raises if one is found.

        The helper is called from ``Catalog.create`` only. ``update`` cannot
        introduce a duplicate (it requires the entry to already exist by
        ``(namespace, id)``); ``clone`` of a meta entry is structurally guarded
        by the duplicate-id check (a same-namespace clone produces a different
        id, so the meta-singleton check is unreachable on the clone path —
        cross-namespace clones land in a fresh namespace where the singleton
        invariant is satisfied by construction); ``delete`` is unrelated.

        Args:
            entry: Candidate entry. Inspected for ``kind`` and ``namespace``;
                no other fields read.

        Raises:
            CatalogValidationError: When ``entry.kind == "meta"`` and another
                meta entry already exists in ``entry.namespace``.
        """
        if entry.kind != "meta":
            return
        existing_metas = self._repository.list(EntryQuery(namespace=entry.namespace, kind="meta"))
        if existing_metas:
            raise CatalogValidationError(
                [
                    f"Namespace '{entry.namespace}' already has a meta entry — "
                    f"exactly one kind=meta entry is allowed per namespace"
                ]
            )

    def _check_namespace_initialized(self, namespace: str) -> None:
        """Ensure at least one anchor (team or meta) exists in ``namespace``; raise otherwise."""
        team = self._repository.get_by_kind(namespace, "team")
        meta = self._repository.get_by_kind(namespace, "meta")
        if team is None and meta is None:
            raise CatalogValidationError(
                [
                    f"Namespace '{namespace}' has no team entry and no meta entry "
                    f"— create at least one anchor entry first (team OR meta)"
                ]
            )

    def _is_namespace_public(self, namespace: str) -> bool:
        """Return ``True`` iff ``namespace``'s ``_meta`` has ``payload["public"] is True``.

        Per ADR-009 §D2, a namespace is publicly visible iff its meta
        entry carries a typed boolean ``True`` at the root under
        ``payload["public"]``. The check is strict-bool comparison —
        ``1``, ``"true"``, ``"True"``, and other truthy values all fall
        through to ``False``. Operators must opt in unambiguously with a
        real boolean.

        The result is cached on ``self._public_flag_cache`` keyed by
        namespace; subsequent calls for the same namespace return the
        cached boolean without re-querying the repository. Cache
        invalidation happens in :meth:`create`, :meth:`update`, and
        :meth:`delete` whenever a ``kind="meta"`` entry is written or
        removed (via :meth:`_invalidate_meta_caches`, which also
        invalidates :meth:`_is_namespace_shareable`'s twin cache).

        A namespace with no ``_meta`` entry returns ``False`` (the
        default — namespaces without a meta entry are private).

        Args:
            namespace: The target namespace to interrogate.

        Returns:
            ``True`` if a meta entry exists in ``namespace`` and carries
            ``payload["public"] is True``; ``False`` otherwise (no meta
            entry, missing key, ``False``, or any non-bool value).
        """
        cached = self._public_flag_cache.get(namespace)
        if cached is not None:
            return cached
        meta = self._repository.get(namespace, "_meta")
        public = False
        if meta is not None and isinstance(meta.payload, dict):
            public = meta.payload.get("public") is True
        self._public_flag_cache[namespace] = public
        return public

    def _is_namespace_shareable(self, namespace: str) -> bool:
        """Return ``True`` iff ``namespace``'s ``_meta`` has ``payload["shareable"] is True``.

        Per ADR-008 §D2 (updated 2026-05-08, rev 2), a namespace is cross-
        namespace-referenceable iff its meta entry carries a typed boolean
        ``True`` at the root under ``payload["shareable"]``. The check is
        strict-bool comparison — ``1``, ``"true"``, ``"True"``, and other
        truthy strings all fall through to ``False``. Operators must opt in
        unambiguously with a real boolean.

        The result is cached on ``self._shareable_flag_cache`` keyed by
        namespace; subsequent calls for the same namespace return the
        cached boolean without re-querying the repository. Cache
        invalidation happens in :meth:`create`, :meth:`update`, and
        :meth:`delete` whenever a ``kind="meta"`` entry is written or
        removed.

        Args:
            namespace: The target namespace to interrogate.

        Returns:
            ``True`` if a meta entry exists in ``namespace`` and carries
            ``payload["shareable"] is True``; ``False`` otherwise (no meta
            entry, missing key, ``False``, or any non-bool value).
        """
        cached = self._shareable_flag_cache.get(namespace)
        if cached is not None:
            return cached
        meta = self._repository.get(namespace, "_meta")
        shareable = False
        if meta is not None and isinstance(meta.payload, dict):
            shareable = meta.payload.get("shareable") is True
        self._shareable_flag_cache[namespace] = shareable
        return shareable

    def _invalidate_meta_caches(self, entry: Entry) -> None:
        """Drop the cached meta-derived flags for ``entry.namespace`` on a meta-entry mutation.

        Unified invalidation hook for the two per-instance caches that
        derive from a namespace's ``_meta`` entry — ``_shareable_flag_cache``
        (gates cross-namespace ref resolution per ADR-008 §D2) and
        ``_public_flag_cache`` (gates visibility filtering per ADR-009 §D2).
        Called from ``create`` / ``update`` / ``delete`` after the
        repository write commits, so the next
        :meth:`_is_namespace_shareable` / :meth:`_is_namespace_public`
        lookup re-reads the meta entry. A non-meta entry write is a
        no-op — both caches only depend on the meta entry's payload.
        """
        if entry.kind == "meta":
            self._shareable_flag_cache.pop(entry.namespace, None)
            self._public_flag_cache.pop(entry.namespace, None)

    def _check_ownership(self, entry: Entry) -> None:
        """Ensure ``entry.user_id`` matches the namespace anchor (team, then meta fallback)."""
        team = self._repository.get_by_kind(entry.namespace, "team")
        if team is not None:
            anchor = team
            anchor_kind = "team"
        else:
            meta = self._repository.get_by_kind(entry.namespace, "meta")
            if meta is not None:
                anchor = meta
                anchor_kind = "meta"
            else:
                raise CatalogValidationError(
                    [
                        f"Namespace '{entry.namespace}' has no team entry and no "
                        f"meta entry — cannot verify ownership for '{entry.id}'"
                    ]
                )
        if entry.user_id != anchor.user_id:
            raise CatalogValidationError(
                [
                    f"Ownership mismatch in namespace '{entry.namespace}': "
                    f"entry '{entry.id}' has user_id={entry.user_id!r} but "
                    f"anchor ({anchor_kind}) has user_id={anchor.user_id!r}"
                ]
            )

    def _clone_one(
        self,
        src_namespace: str,
        src_id: str,
        cloned: dict[tuple[str, str], str],
        pending_writes: _list[Entry],
        dst_namespace: str,
        dst_user_id: str,
    ) -> str:
        """Clone a single source entry, recursing into its payload refs."""
        key = (src_namespace, src_id)
        if key in cloned:
            return cloned[key]

        src = self._repository.get(src_namespace, src_id)
        if src is None:
            raise CatalogValidationError(
                [f"Clone source ({src_namespace}, {src_id}) not found during resolution"]
            )

        new_id = self._mint_dst_id(dst_namespace, src_namespace, src_id, cloned)
        # Record BEFORE recursing so a back-ref to the same source returns
        # the same dst id (prevents infinite recursion on cycles-by-construction).
        cloned[key] = new_id

        def _callback(target_id: str) -> str:
            return self._clone_one(
                src_namespace=src_namespace,
                src_id=target_id,
                cloned=cloned,
                pending_writes=pending_writes,
                dst_namespace=dst_namespace,
                dst_user_id=dst_user_id,
            )

        new_payload = _rewrite_refs(src.payload, _callback)
        new_entry = src.model_copy(
            update={
                "id": new_id,
                "namespace": dst_namespace,
                "user_id": dst_user_id,
                "payload": new_payload,
            }
        )
        pending_writes.append(new_entry)
        return new_id

    def _mint_dst_id(
        self,
        dst_namespace: str,
        src_namespace: str,
        src_id: str,
        cloned: dict[tuple[str, str], str],
    ) -> str:
        """Return the destination id for a clone.

        Cross-namespace: reuse ``src_id``. Same-namespace: append a numeric
        suffix starting at ``-2`` and increment until the candidate does not
        collide with either a stored entry or an id already planned by the
        current clone operation.
        """
        if dst_namespace != src_namespace:
            return src_id
        planned: set[str] = set(cloned.values())
        suffix = 2
        while True:
            candidate = f"{src_id}-{suffix}"
            if self._repository.get(dst_namespace, candidate) is None and candidate not in planned:
                return candidate
            suffix += 1


def _is_cross_ns_marker(node: dict[str, Any]) -> bool:
    """Return ``True`` when ``node`` is a ref marker carrying a cross-ns hint.

    A cross-ns marker carries either an explicit ``__namespace__`` key OR a
    shorthand ``<ns>.<id>`` form in ``__ref__`` (a string value containing a
    dot). Same-namespace markers (no ``__namespace__``, no dot in
    ``__ref__``) return ``False`` and remain subject to local-ref rewrite
    and bundle dangling-ref collection (ADR-008 §D2).
    """
    if NAMESPACE_KEY in node:
        return True
    raw_ref = node.get(REF_KEY)
    return isinstance(raw_ref, str) and "." in raw_ref


def _partition_meta(entries: builtins.list[Entry]) -> tuple[Entry | None, builtins.list[Entry]]:
    """Split ``entries`` into ``(meta_entry_or_None, [non_meta_entries])``.

    The meta entry is identified by the canonical predicate
    ``e.id == "_meta" and e.kind == "meta"`` — both checks together so an
    accidental ``id="_meta"`` non-meta entry (or a hand-rolled
    ``kind="meta"`` entry under a non-canonical id) is excluded from the
    hoist. The meta-singleton invariant is enforced separately by
    :meth:`Catalog._check_meta_singleton`; this helper is a structural split
    only and does not validate cardinality.
    """
    meta_entry: Entry | None = None
    non_meta: builtins.list[Entry] = []
    for entry in entries:
        if entry.id == "_meta" and entry.kind == "meta":
            meta_entry = entry
        else:
            non_meta.append(entry)
    return meta_entry, non_meta


def _iter_cross_ns_targets(payload: Any) -> builtins.list[tuple[str, str]]:
    """Return every ``(target_namespace, target_id)`` reachable through cross-ns ref markers.

    Walks the payload tree recursively and collects every dict node carrying
    a ``__ref__`` entry that resolves to a cross-namespace target. The
    walker recognises the same two cross-ns shapes as the resolver:

    * **Canonical** — a sibling ``__namespace__`` key on the ref marker.
    * **Shorthand** — a ``<ns>.<id>`` form in the ``__ref__`` value (split
      on the first dot only, matching the resolver's
      ``_resolve_target_namespace``).

    Same-namespace markers (no ``__namespace__``, no dot in ``__ref__``) are
    excluded — they do not belong in the external section.

    The walker is parse-only:

    * No call to ``populate_refs``.
    * No Pydantic validation.
    * No repository access.
    * No allowlist or shareable-flag check (the shareable-flag gate fires later,
      when :meth:`Catalog._collect_external_refs` decides whether to fetch
      the target).

    Sibling-override sub-payloads (architecture/03 — non-reserved keys
    alongside ``__ref__`` / ``__namespace__`` / ``__type__``) are walked
    recursively, so a cross-ns ref marker that ALSO carries an override
    sub-payload containing another cross-ns ref yields both targets.

    Args:
        payload: An ``Entry.payload`` tree — arbitrary nested ``dict`` /
            ``list`` of JSON-able primitives.

    Returns:
        A flat list of ``(target_namespace, target_id)`` tuples in
        depth-first traversal order. Duplicates are NOT removed at this
        layer (the caller — :meth:`Catalog._collect_external_refs` — owns
        deduplication so the worklist + visited-set algorithm operates on
        the raw walker output).
    """
    results: builtins.list[tuple[str, str]] = []
    _walk_for_cross_ns(payload, results)
    return results


def _walk_for_cross_ns(node: Any, out: builtins.list[tuple[str, str]]) -> None:
    """Recursive helper for :func:`_iter_cross_ns_targets`."""
    if isinstance(node, dict):
        if REF_KEY in node:
            pair = _classify_cross_ns_marker(node)
            if pair is not None:
                out.append(pair)
            for key, value in node.items():
                if key not in _RESERVED_REF_KEYS:
                    _walk_for_cross_ns(value, out)
            return
        for value in node.values():
            _walk_for_cross_ns(value, out)
        return
    if isinstance(node, list):
        for item in node:
            _walk_for_cross_ns(item, out)


def _classify_cross_ns_marker(node: dict[str, Any]) -> tuple[str, str] | None:
    """Return ``(target_namespace, target_id)`` for a cross-ns ref marker, else ``None``.

    The classification mirrors the resolver's ``_resolve_target_namespace``:

    * If ``__ref__`` is a string containing a dot, the first-dot split
      yields ``(namespace, id)`` — this is the shorthand form.
    * Else if ``__namespace__`` is set explicitly, the pair is
      ``(__namespace__, __ref__)``.
    * Otherwise the marker is same-namespace and ``None`` is returned.
    """
    raw_ref = node.get(REF_KEY)
    if isinstance(raw_ref, str) and "." in raw_ref:
        ns, rid = raw_ref.split(".", 1)
        return ns, rid
    explicit_ns = node.get(NAMESPACE_KEY)
    if isinstance(explicit_ns, str) and isinstance(raw_ref, str):
        return explicit_ns, raw_ref
    return None


def _iter_ref_targets(node: Any) -> list[str]:
    """Return every same-namespace ``__ref__`` target id reachable inside ``node``.

    Walks dicts and lists recursively. A dict carrying a ``REF_KEY`` entry
    contributes its target id ONLY when the marker is same-namespace
    (cross-ns markers — those with an ``__namespace__`` key OR a
    ``<ns>.<id>`` shorthand in ``__ref__`` — are external by design and
    therefore excluded from the bundle dangling-ref check). The walker
    does NOT recurse into a ref-marker dict's other keys regardless of
    same-/cross-ns shape (``__type__`` and sibling-override values are
    handled at the resolver layer, not here). Non-ref dicts and lists
    recurse structurally; leaves contribute nothing.
    """
    results: list[str] = []
    if isinstance(node, dict):
        if REF_KEY in node:
            if _is_cross_ns_marker(node):
                return results
            target = node[REF_KEY]
            if isinstance(target, str):
                results.append(target)
            return results
        for value in node.values():
            results.extend(_iter_ref_targets(value))
        return results
    if isinstance(node, list):
        for item in node:
            results.extend(_iter_ref_targets(item))
    return results


def _rewrite_refs(node: Any, clone_target: Any) -> Any:
    """Recursively copy ``node``, replacing local ref targets via ``clone_target``.

    Dicts carrying a ``REF_KEY`` entry have their target id replaced by
    ``clone_target(target_id)`` — the callback is typically a recursive
    clone invocation that also clones the target entry. ``TYPE_KEY`` (if
    present) is preserved verbatim. Cross-ns markers (those with an
    ``__namespace__`` key OR a ``<ns>.<id>`` shorthand in ``__ref__``) are
    preserved **verbatim** — clone never rewrites cross-ns refs because the
    target lives in a separate namespace owned by a different operator
    (ADR-008 §D2). Non-ref dicts and lists recurse structurally; leaves
    pass through unchanged.

    Args:
        node: Arbitrary payload subtree.
        clone_target: Callback mapping a source target id to the corresponding
            destination target id (with side effect of cloning the target).

    Returns:
        A new payload subtree with every same-namespace ref marker pointing
        at the newly minted destination ids; cross-ns markers preserved
        byte-for-byte.
    """
    if isinstance(node, dict):
        if REF_KEY in node:
            if _is_cross_ns_marker(node):
                # Cross-ns marker — preserve verbatim. Take a shallow copy so
                # the caller's subtree is not aliased into the destination.
                return dict(node)
            new: dict[str, Any] = dict(node)
            new[REF_KEY] = clone_target(node[REF_KEY])
            return new
        return {k: _rewrite_refs(v, clone_target) for k, v in node.items()}
    if isinstance(node, list):
        return [_rewrite_refs(v, clone_target) for v in node]
    return node


class _BundleOverlayRepository:
    """Read-only overlay combining bundle entries with a backing repository.

    Used by :meth:`Catalog.import_namespace_yaml` during ``prepare_for_write``
    so that bundle-internal refs (where an entry's payload references a
    sibling entry declared in the same bundle) resolve successfully even
    before the bundle is persisted. Writes always delegate to the backing
    repository — the overlay is transparent on the write path.
    """

    def __init__(self, inner: EntryRepository, bundle_entries: list[Entry]) -> None:
        """Index ``bundle_entries`` by ``(namespace, id)`` for O(1) overlay lookups."""
        self._inner: EntryRepository = inner
        self._overlay: dict[tuple[str, str], Entry] = {
            (e.namespace, e.id): e for e in bundle_entries
        }

    def get(self, namespace: str, id: str) -> Entry | None:
        """Return the bundle entry if present, otherwise the backing repo's result."""
        overlay_hit = self._overlay.get((namespace, id))
        if overlay_hit is not None:
            return overlay_hit
        return self._inner.get(namespace, id)

    def put(self, entry: Entry) -> Entry:
        return self._inner.put(entry)

    def delete(self, namespace: str, id: str) -> None:
        self._inner.delete(namespace, id)

    def list(self, query: EntryQuery) -> _list[Entry]:
        return self._inner.list(query)

    def list_by_namespace(self, namespace: str) -> _list[Entry]:
        return self._inner.list_by_namespace(namespace)

    def get_by_kind(self, namespace: str, kind: Any) -> Entry | None:
        for (ns, _), e in self._overlay.items():
            if ns == namespace and e.kind == kind:
                return e
        return self._inner.get_by_kind(namespace, kind)

    def find_references(self, namespace: str, target_id: str) -> _list[Entry]:
        return self._inner.find_references(namespace, target_id)

    def find_references_global(self, namespace: str, target_id: str) -> _list[Entry]:
        return self._inner.find_references_global(namespace, target_id)


class _InMemoryEntryRepository:
    """Pre-loaded ``EntryRepository`` wrapper serving ``get`` from a list.

    Used exclusively by :meth:`Catalog.load_team` to short-circuit the per-ref
    ``get`` calls :func:`~akgentic.catalog.resolver.populate_refs` would
    otherwise issue against the namespace that was just loaded wholesale.
    Every non-``get`` method raises :class:`NotImplementedError` — this wrapper
    is intentionally a degraded shape, not a drop-in replacement for a real
    repository.
    """

    def __init__(
        self,
        entries: list[Entry],
        fallback: EntryRepository | None = None,
    ) -> None:
        """Index ``entries`` by ``(namespace, id)`` for O(1) ``get`` lookups.

        Args:
            entries: The pre-loaded namespace-bounded entry list.
            fallback: Optional outer repository consulted when ``get`` misses
                the in-memory index. Used by :meth:`Catalog.load_team` to
                resolve cross-namespace ref targets in allowlisted namespaces
                without sacrificing the single-query invariant for the local
                namespace.
        """
        self._by_key: dict[tuple[str, str], Entry] = {(e.namespace, e.id): e for e in entries}
        self._fallback: EntryRepository | None = fallback

    def get(self, namespace: str, id: str) -> Entry | None:
        """Return the pre-loaded entry, fall back to the outer repo, or ``None``."""
        hit = self._by_key.get((namespace, id))
        if hit is not None:
            return hit
        if self._fallback is not None:
            return self._fallback.get(namespace, id)
        return None

    def put(self, entry: Entry) -> Entry:  # noqa: ARG002
        raise NotImplementedError(
            "InMemoryEntryRepository supports only .get(); use the real repository "
            "for other operations"
        )

    def delete(self, namespace: str, id: str) -> None:  # noqa: ARG002
        raise NotImplementedError(
            "InMemoryEntryRepository supports only .get(); use the real repository "
            "for other operations"
        )

    def list(self, query: EntryQuery) -> _list[Entry]:  # noqa: ARG002
        raise NotImplementedError(
            "InMemoryEntryRepository supports only .get(); use the real repository "
            "for other operations"
        )

    def list_by_namespace(self, namespace: str) -> _list[Entry]:  # noqa: ARG002
        raise NotImplementedError(
            "InMemoryEntryRepository supports only .get(); use the real repository "
            "for other operations"
        )

    def get_by_kind(self, namespace: str, kind: Any) -> Entry | None:  # noqa: ARG002
        raise NotImplementedError(
            "InMemoryEntryRepository supports only .get(); use the real repository "
            "for other operations"
        )

    def find_references(self, namespace: str, target_id: str) -> _list[Entry]:  # noqa: ARG002
        raise NotImplementedError(
            "InMemoryEntryRepository supports only .get(); use the real repository "
            "for other operations"
        )

    def find_references_global(
        self,
        namespace: str,  # noqa: ARG002
        target_id: str,  # noqa: ARG002
    ) -> _list[Entry]:
        raise NotImplementedError(
            "InMemoryEntryRepository supports only .get(); use the real repository "
            "for other operations"
        )
