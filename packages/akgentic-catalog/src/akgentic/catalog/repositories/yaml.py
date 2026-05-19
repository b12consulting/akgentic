"""Filesystem-backed v2 ``EntryRepository`` storing one YAML file per entry.

Layout: ``root/{namespace}/{kind}/{id}.yaml`` — the namespace is the top-level
directory, the kind is the second segment, and the filename stem is the entry
id. Each file holds a single YAML document equal to
``entry.model_dump(mode="json")``. Empty directories are pruned on delete so a
round-trip ``put``/``delete`` leaves the root clean.

``(namespace, id)`` is the uniqueness key under v2; ``kind`` is metadata. Two
files with the same stem inside a single namespace — even across different kind
subdirectories — is a corruption signal the repository surfaces with
``CatalogValidationError`` on the first read that touches the namespace.

This is the shard-10 final home for the YAML-backed repository; the shared
``_payload_has_ref`` helper also lives here and is imported by
:mod:`akgentic.catalog.repositories.mongo`.
"""

from __future__ import annotations

import builtins
import logging
from pathlib import Path

import yaml

from akgentic.catalog.models.entry import Entry, EntryKind
from akgentic.catalog.models.errors import CatalogValidationError
from akgentic.catalog.models.queries import EntryQuery

__all__ = ["YamlEntryRepository"]

logger = logging.getLogger(__name__)

_list = builtins.list  # Alias: the repository's list() method shadows the built-in


class _BlockScalarDumper(yaml.SafeDumper):
    """SafeDumper subclass that emits multi-line ``str`` values as ``|`` block scalars.

    PyYAML's default representer picks single-quoted flow style for strings
    containing newlines, which renders each ``\\n`` as a visible blank line on
    disk. Registering a scoped ``str`` representer on this subclass — rather
    than mutating the global registry via ``yaml.add_representer`` — keeps the
    change local to callers that opt in via ``Dumper=_BlockScalarDumper``.

    PyYAML automatically picks ``|`` vs ``|-`` based on trailing-newline
    presence, and falls back to double-quoted for values it cannot faithfully
    represent in block style (e.g. strings with tabs). No additional predicate
    is required here.
    """


def _represent_str_block(dumper: yaml.SafeDumper, data: str) -> yaml.ScalarNode:
    """Represent ``str`` as a ``|`` block scalar when it contains a newline."""
    if "\n" in data:
        return dumper.represent_scalar("tag:yaml.org,2002:str", data, style="|")
    return dumper.represent_scalar("tag:yaml.org,2002:str", data)


_BlockScalarDumper.add_representer(str, _represent_str_block)


def _payload_has_ref(node: object, target_id: str) -> bool:
    """Depth-first scan for any ``{"__ref__": target_id}`` marker in ``node``.

    Walks dicts and lists recursively. Only dict keys named ``"__ref__"`` whose
    value equals ``target_id`` are treated as matches — raw string occurrences
    of ``"__ref__"`` or ``target_id`` as plain values are not matches. The
    sentinel key is hard-coded here to avoid importing ``REF_KEY`` from the
    resolver module (which would otherwise grow into a wider import surface in
    production code — ``REF_KEY`` is a runtime constant pinned by the resolver
    contract, not a dependency).

    Args:
        node: Arbitrary payload subtree (dict, list, or leaf value).
        target_id: The id to look for.

    Returns:
        ``True`` if any dict in the tree has ``"__ref__" == target_id``.
    """
    if isinstance(node, dict):
        if node.get("__ref__") == target_id:
            return True
        return any(_payload_has_ref(v, target_id) for v in node.values())
    if isinstance(node, list):
        return any(_payload_has_ref(v, target_id) for v in node)
    return False


def _payload_has_cross_ns_ref(node: object, target_namespace: str, target_id: str) -> bool:
    """Depth-first scan for a cross-ns ref marker pointing at ``(target_namespace, target_id)``.

    Recognises both ADR-008 §D2 cross-ns marker shapes:

    * Canonical: ``{"__ref__": target_id, "__namespace__": target_namespace}``.
    * Shorthand: ``{"__ref__": "<target_namespace>.<target_id>"}``.

    Same-namespace markers (no ``__namespace__``, no dot in ``__ref__``) are
    NOT matched — they are served by :func:`_payload_has_ref` and the
    namespace-bounded :func:`find_references` path. The walker treats a dict
    carrying ``__ref__`` as a leaf — it does NOT recurse into the marker's
    other keys (``__type__``, ``__namespace__``, sibling overrides). Like
    :func:`_payload_has_ref` the sentinel keys are hard-coded to avoid a
    cross-module import.

    Args:
        node: Arbitrary payload subtree (dict, list, or leaf value).
        target_namespace: The target namespace to match.
        target_id: The target id to match.

    Returns:
        ``True`` if any dict in the tree carries a cross-ns ref marker
        whose target equals ``(target_namespace, target_id)``.
    """
    if isinstance(node, dict):
        if "__ref__" in node:
            raw_ref = node["__ref__"]
            explicit_ns = node.get("__namespace__")
            # Canonical form: explicit __namespace__ + __ref__ (the value is
            # the bare id; no dot semantics).
            if explicit_ns is not None:
                return bool(explicit_ns == target_namespace and raw_ref == target_id)
            # Shorthand form: __ref__ == "<ns>.<id>" with no explicit
            # __namespace__. Split on the first dot only — ids may
            # legitimately contain dots.
            if isinstance(raw_ref, str) and "." in raw_ref:
                shorthand_ns, shorthand_id = raw_ref.split(".", 1)
                return shorthand_ns == target_namespace and shorthand_id == target_id
            # Same-namespace marker — not a cross-ns referrer.
            return False
        return any(_payload_has_cross_ns_ref(v, target_namespace, target_id) for v in node.values())
    if isinstance(node, list):
        return any(_payload_has_cross_ns_ref(v, target_namespace, target_id) for v in node)
    return False


class YamlEntryRepository:
    """Per-namespace, per-kind, per-file v2 ``EntryRepository`` on the filesystem.

    Satisfies the ``akgentic.catalog.repositories.base.EntryRepository``
    structural protocol. ``root`` is stored verbatim as a ``Path`` — no
    directory is created in ``__init__``; the first ``put`` materialises the
    tree lazily. Reads across a namespace go through ``_scan_namespace`` which
    validates every file and surfaces duplicate-id corruption.

    The repository is intent-preserving: ``put`` writes
    ``entry.model_dump(mode="json")`` verbatim, so author-written ref markers
    (``{"__ref__": ...}``, ``{"__type__": ...}``) round-trip byte-for-byte.
    The ``Catalog`` service (Story 15.5) runs ``prepare_for_write`` before
    reaching the repository; this class never re-dumps payload content.

    Caching is namespace-scoped and lazy: the first read for a namespace walks
    the directory once; subsequent reads return the cached list until a write
    or ``reload`` invalidates it. ``reload(namespace=None)`` clears the entire
    cache when no argument is given.
    """

    def __init__(self, root: Path) -> None:
        """Store the repository root; do not create it on disk.

        Args:
            root: Root directory under which per-namespace subtrees live.
                The directory does NOT have to exist — it is created lazily on
                the first ``put``.
        """
        self._root = root
        self._cache: dict[str, _list[Entry]] = {}

    # --- Path helpers ---

    def _namespace_dir(self, namespace: str) -> Path:
        """Return ``root/{namespace}``."""
        return self._root / namespace

    def _kind_dir(self, namespace: str, kind: EntryKind) -> Path:
        """Return ``root/{namespace}/{kind}``."""
        return self._namespace_dir(namespace) / kind

    def _entry_path(self, namespace: str, kind: EntryKind, id: str) -> Path:
        """Return ``root/{namespace}/{kind}/{id}.yaml``."""
        return self._kind_dir(namespace, kind) / f"{id}.yaml"

    # --- Write operations ---

    def put(self, entry: Entry) -> Entry:
        """Insert or replace ``entry`` keyed by ``(namespace, id)``.

        If an existing file for the same ``(namespace, id)`` lives under a
        different kind directory, it is removed first (and any now-empty kind
        directory is pruned). The new file is written atomically via
        ``write_text``. The affected namespace cache entry is invalidated so
        the next read reflects the write.

        Args:
            entry: The entry to persist.

        Returns:
            The stored ``entry`` (unchanged — caller may chain or assert).
        """
        self._remove_existing_with_different_kind(entry)
        path = self._entry_path(entry.namespace, entry.kind, entry.id)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = yaml.dump(
            entry.model_dump(mode="json"),
            Dumper=_BlockScalarDumper,
            default_flow_style=False,
            sort_keys=False,
            allow_unicode=True,
        )
        path.write_text(payload, encoding="utf-8")
        self._invalidate(entry.namespace)
        return entry

    def _remove_existing_with_different_kind(self, entry: Entry) -> None:
        """Delete any prior file for ``(namespace, id)`` under another kind dir.

        v2 uniqueness is ``(namespace, id)``; ``kind`` is metadata. When an
        upsert changes the kind, the old file must be removed so the namespace
        does not end up with two stems claiming the same id (which
        ``_scan_namespace`` would surface as duplicate-id corruption).
        """
        namespace_dir = self._namespace_dir(entry.namespace)
        if not namespace_dir.exists():
            return
        target_path = self._entry_path(entry.namespace, entry.kind, entry.id)
        for existing in sorted(namespace_dir.glob(f"*/{entry.id}.yaml")):
            if existing.resolve() == target_path.resolve():
                continue
            existing.unlink()
            self._prune_empty_parents(existing.parent)

    def delete(self, namespace: str, id: str) -> None:
        """Remove the entry file and prune empty parent directories.

        Locates the file by scanning every kind directory under
        ``root/{namespace}/``. If no file is found, the call is a no-op (the
        service layer's ``validate_delete`` is the authoritative guard against
        deleting missing entries).

        After unlink, the kind directory is removed if empty; if the namespace
        directory is then empty, it is removed too. The root itself is never
        removed.

        Args:
            namespace: Namespace containing the entry.
            id: Entry id within ``namespace``.
        """
        namespace_dir = self._namespace_dir(namespace)
        if not namespace_dir.exists():
            return
        for candidate in sorted(namespace_dir.glob(f"*/{id}.yaml")):
            candidate.unlink()
            self._prune_empty_parents(candidate.parent)
            self._invalidate(namespace)
            return
        # AC12: no match — no-op (no cache invalidation needed either)

    def _prune_empty_parents(self, leaf_dir: Path) -> None:
        """Walk up from ``leaf_dir`` removing empty directories, stopping at root.

        Uses ``any(path.iterdir())`` for a short-circuit emptiness check.
        Never removes ``self._root`` itself — the repository may still be
        written to after a full cleanup.
        """
        current = leaf_dir
        while current != self._root and current.exists() and not any(current.iterdir()):
            current.rmdir()
            current = current.parent

    # --- Read operations ---

    def get(self, namespace: str, id: str) -> Entry | None:
        """Return the entry for ``(namespace, id)`` or ``None`` if absent.

        Delegates to ``_scan_namespace`` so the duplicate-id check runs on
        the first read. If the namespace directory does not exist, returns
        ``None`` without raising (empty namespace is not a corruption signal).
        """
        for entry in self._scan_namespace(namespace):
            if entry.id == id:
                return entry
        return None

    def list(self, query: EntryQuery) -> _list[Entry]:
        """Return entries matching ``query`` with AND semantics over set fields.

        The scan scope is narrowed from ``query.namespace`` (single namespace
        subtree) or all namespaces under ``root``. Remaining filters are
        applied in memory via ``_matches``.
        """
        scope = self._resolve_scan_scope(query.namespace)
        return [entry for entry in scope if self._matches(entry, query)]

    def _resolve_scan_scope(self, namespace: str | None) -> _list[Entry]:
        """Return the entries the ``list`` filters should run over.

        When ``namespace`` is set, scan only that namespace subtree. Otherwise
        scan every namespace directory under ``root``.
        """
        if namespace is not None:
            return self._scan_namespace(namespace)
        if not self._root.exists():
            return []
        entries: _list[Entry] = []
        for namespace_dir in sorted(self._root.iterdir()):
            if namespace_dir.is_dir():
                entries.extend(self._scan_namespace(namespace_dir.name))
        return entries

    def _matches(self, entry: Entry, query: EntryQuery) -> bool:
        """Return ``True`` if ``entry`` satisfies every set filter in ``query``.

        Each filter is a conjunct: a ``None`` filter is ignored, a set filter
        must match. ``user_id_set`` is tri-state — ``None`` means "no filter",
        ``True`` restricts to ``user_id != "anonymous"``, ``False`` restricts
        to ``user_id == "anonymous"``. ``description_contains`` is a
        case-sensitive substring check matching v1 semantics.
        """
        if query.kind is not None and entry.kind != query.kind:
            return False
        if query.id is not None and entry.id != query.id:
            return False
        # query.user_id is the EntryQuery exact-match filter (still
        # tri-state: a falsy / None value means "no filter"). entry.user_id
        # is always a real string after Story 18.1.
        query_user_id = query.user_id
        if query_user_id and entry.user_id != query_user_id:
            return False
        if query.user_id_set is True and entry.user_id == "anonymous":
            return False
        if query.user_id_set is False and entry.user_id != "anonymous":
            return False
        if (
            query.description_contains is not None
            and query.description_contains not in entry.description
        ):
            return False
        return True

    def list_by_namespace(self, namespace: str) -> _list[Entry]:
        """Return every entry in ``namespace`` regardless of kind."""
        return _list(self._scan_namespace(namespace))

    def get_by_kind(self, namespace: str, kind: EntryKind) -> Entry | None:
        """Return a single entry of ``kind`` in ``namespace`` or ``None``.

        On multiple matches (the corruption state the service layer polices),
        returns the alphabetically-first file. The repository does NOT raise —
        the singleton invariant is a service-level concern (Story 15.5). The
        cross-kind duplicate-id check still runs via ``_scan_namespace``.
        """
        # Trigger the per-namespace duplicate/mismatch checks before scanning
        # the narrower kind directory — keeps semantics consistent with other
        # read paths.
        self._scan_namespace(namespace)
        kind_dir = self._kind_dir(namespace, kind)
        if not kind_dir.exists():
            return None
        yaml_files = sorted(kind_dir.glob("*.yaml"))
        if not yaml_files:
            return None
        return self._load_entry(yaml_files[0], namespace, kind)

    def find_references(self, namespace: str, target_id: str) -> _list[Entry]:
        """Return entries in ``namespace`` whose payload references ``target_id``."""
        return [
            entry
            for entry in self._scan_namespace(namespace)
            if _payload_has_ref(entry.payload, target_id)
        ]

    def find_references_global(self, namespace: str, target_id: str) -> _list[Entry]:
        """Return entries across every namespace whose payload carries a cross-ns ref.

        Iterates every namespace directory under ``root`` (no caller-
        supplied scope) and walks each entry's payload via the shared
        :func:`_payload_has_cross_ns_ref` helper. Documented as O(N)
        where N = total entries on disk (ADR-008 §D2 as updated
        2026-05-08; no JSONB / wildcard index — same shape as the
        existing ``find_references`` walker).
        """
        if not self._root.exists():
            return []
        results: _list[Entry] = []
        for namespace_dir in sorted(self._root.iterdir()):
            if not namespace_dir.is_dir():
                continue
            for entry in self._scan_namespace(namespace_dir.name):
                if _payload_has_cross_ns_ref(entry.payload, namespace, target_id):
                    results.append(entry)
        return results

    def reload(self, namespace: str | None = None) -> None:
        """Invalidate the internal cache.

        Args:
            namespace: If ``None``, clear the entire cache; otherwise clear
                only the entry for ``namespace`` (missing key is fine).
        """
        if namespace is None:
            self._cache.clear()
        else:
            self._cache.pop(namespace, None)

    def _invalidate(self, namespace: str) -> None:
        """Drop the cached view for ``namespace`` so the next read re-scans."""
        self._cache.pop(namespace, None)

    # --- Namespace scan (single source of truth) ---

    def _scan_namespace(self, namespace: str) -> _list[Entry]:
        """Walk ``root/{namespace}/`` validating every file; cache and return.

        Duplicate-id detection spans kind subdirectories: if two files under
        different kind dirs share a filename stem, raise
        ``CatalogValidationError`` (AC17). Per-file namespace/kind/id stem
        must agree with the containing path segments (AC18). Empty files are
        skipped with a warning (AC19).
        """
        cached = self._cache.get(namespace)
        if cached is not None:
            return cached

        namespace_dir = self._namespace_dir(namespace)
        if not namespace_dir.exists():
            self._cache[namespace] = []
            return []

        entries: _list[Entry] = []
        seen_ids: dict[str, Path] = {}
        for kind_dir in sorted(namespace_dir.iterdir()):
            if not kind_dir.is_dir():
                continue
            entries.extend(self._scan_kind_dir(kind_dir, namespace, seen_ids))

        self._cache[namespace] = entries
        return entries

    def _scan_kind_dir(
        self,
        kind_dir: Path,
        namespace: str,
        seen_ids: dict[str, Path],
    ) -> _list[Entry]:
        """Load every YAML file in ``kind_dir``, checking duplicates vs ``seen_ids``.

        The ``kind_dir.name`` is expected to be an ``EntryKind`` literal; if a
        file's validated ``kind`` disagrees with the directory name, AC18
        raises through ``_load_entry``.
        """
        kind = kind_dir.name
        entries: _list[Entry] = []
        for path in sorted(kind_dir.glob("*.yaml")):
            entry = self._load_entry(path, namespace, kind)
            if entry is None:
                continue
            if entry.id in seen_ids:
                raise CatalogValidationError(
                    [f"Duplicate id '{entry.id}' found in '{seen_ids[entry.id]}' and '{path}'"]
                )
            seen_ids[entry.id] = path
            entries.append(entry)
        return entries

    def _load_entry(self, path: Path, namespace: str, kind: str) -> Entry | None:
        """Parse ``path`` and validate it against the expected path segments.

        Returns ``None`` for empty YAML (AC19). Raises
        ``CatalogValidationError`` if the file body's ``namespace``, ``kind``,
        or ``id`` disagrees with the directory segments or filename stem.
        Pydantic ``ValidationError`` propagates unchanged — bad YAML is a
        caller concern, matching v1 behaviour.
        """
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        if raw is None:
            logger.warning("empty YAML file skipped: %s", path)
            return None
        entry = Entry.model_validate(raw)
        self._check_path_agreement(entry, path, namespace, kind)
        return entry

    def _check_path_agreement(
        self,
        entry: Entry,
        path: Path,
        namespace: str,
        kind: str,
    ) -> None:
        """Enforce AC18: file's ``namespace``/``kind``/``id`` must match path segments."""
        if entry.namespace != namespace:
            raise CatalogValidationError(
                [f"File '{path}' has namespace '{entry.namespace}' but lives under '{namespace}'"]
            )
        if entry.kind != kind:
            raise CatalogValidationError(
                [f"File '{path}' has kind '{entry.kind}' but lives under '{kind}'"]
            )
        stem = path.stem
        if entry.id != stem:
            raise CatalogValidationError(
                [f"File '{path}' has id '{entry.id}' but filename stem is '{stem}'"]
            )
