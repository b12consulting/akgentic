"""Ref-sentinel constants, allowlist loader, and the v2 resolver pipeline.

This module owns the v2 ref-sentinel sentinel keys (``REF_KEY``, ``TYPE_KEY``)
and the ``load_model_type(path)`` function that imports a Pydantic
``BaseModel`` class by dotted path, gated behind three defensive checks:

1. The path must start with one of ``_ALLOWED_PREFIXES`` (storage + runtime
   defence in depth — ``models.entry.AllowlistedPath`` enforces the same
   prefix at Pydantic construction time).
2. The resolved class must be a subclass of ``pydantic.BaseModel``.
3. The resolved class must not declare Pydantic fields named ``__ref__`` or
   ``__type__`` (the reserved keys used by the resolver's sentinel scheme —
   collisions would break round-tripping).

It also exposes the v2 resolver pipeline built on top of those primitives:

* :func:`populate_refs` — recursive ref replacement with cycle, missing-target,
  and ``__type__`` mismatch detection; namespace-bounded. Ref-marker positions
  resolve to typed Pydantic instances of the referenced entry's ``model_type``
  (Story 15.6) so polymorphic fields (``list[ToolCard]``, ``list[type]``,
  …) validate against concrete subclasses without authoring workarounds.

  A ref-marker dict may additionally carry non-reserved sibling keys next to
  ``__ref__`` / ``__type__``. Those siblings are treated as a **shallow
  override** (top-level ``dict.update`` semantics) merged on top of the
  resolved target payload before validation (Story 20.1 / ADR-010). This
  lets a single shared ``BaseModel`` entry (notably ``PromptTemplate``) be
  reused by many callers, each supplying its own field values without
  duplicating the full target payload subtree.
* :func:`reconcile_refs` — walks input + dumped trees in lockstep to preserve
  author-written ref markers against a Pydantic-dumped payload. Sibling
  overrides on a ref marker round-trip verbatim: the dict returned for a
  ref-marker node is the full author-written dict, siblings included.
* :func:`resolve` — full hydration of an ``Entry`` into a runtime ``BaseModel``.
* :func:`prepare_for_write` — five-step write pipeline producing the
  intent-preserving, ref-preserving stored payload.
* :func:`validate_delete` — inbound-ref guard that returns a list of
  human-readable blocker messages (empty list = safe to delete).

``check_ownership`` is deliberately NOT part of this module — Story 15.5 is the
right place to decide whether ownership enforcement lives inside
``prepare_for_write`` or inside the ``Catalog`` service itself.
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from typing import Any, Final

from pydantic import BaseModel, ValidationError

from akgentic.core.utils.deserializer import import_class

from .models.entry import Entry
from .models.errors import CatalogValidationError
from .models.native import NativeValue
from .repositories.base import EntryRepository

# Type alias for the shareable-flag check threaded through the resolver.
# A callable that takes a target namespace and returns True if the
# namespace's ``_meta`` entry carries ``payload["shareable"] is True``
# (typed bool at the root — ADR-008 §D2 as updated 2026-05-08 rev 2).
IsNamespaceShareableFn = Callable[[str], bool]

__all__ = [
    "NAMESPACE_KEY",
    "REF_KEY",
    "TYPE_KEY",
    "IsNamespaceShareableFn",
    "enumerate_allowlisted_model_types",
    "load_model_type",
    "populate_refs",
    "prepare_for_write",
    "reconcile_refs",
    "resolve",
    "validate_delete",
]


REF_KEY: Final[str] = "__ref__"
"""Sentinel dict key marking a ref placeholder inside a resolved payload.

A payload dict containing ``REF_KEY`` has been populated by the resolver and
must be hydrated (looked up in the repository) before use at runtime.
"""

TYPE_KEY: Final[str] = "__type__"
"""Sentinel dict key carrying the FQCN of a referenced entry's model type.

Emitted next to ``REF_KEY`` so the resolver can validate the target's type
without loading the target entry eagerly.
"""

NAMESPACE_KEY: Final[str] = "__namespace__"
"""Sentinel dict key carrying the target namespace of a cross-namespace ref.

Implements ADR-008 §D2 — the canonical cross-ns sentinel. A ref-marker dict
may carry ``NAMESPACE_KEY`` next to ``REF_KEY`` (and optionally ``TYPE_KEY``)
to address an entry in a different namespace; the resolver gates the lookup
on the data-driven shareable-flag (the target namespace's ``_meta`` entry has
``payload["shareable"] is True`` — ADR-008 §D2 as updated 2026-05-08 rev 2).
The shorthand ``{"__ref__": "<ns>.<id>"}`` is parsed equivalently — the
resolver splits on the first ``.``. Same-namespace refs (no
``NAMESPACE_KEY``, no dot in ``__ref__``) bypass the gate entirely.
"""

# Runtime allowlist for ``load_model_type``. Duplicated intentionally in
# ``models.entry`` for the annotation-layer defence — two layers, two
# policies that only happen to agree today. See Story 15.1 Dev Notes.
_ALLOWED_PREFIXES: tuple[str, ...] = ("akgentic.",)

_RESERVED_KEYS: frozenset[str] = frozenset({REF_KEY, TYPE_KEY, NAMESPACE_KEY})


def load_model_type(path: str) -> type[BaseModel]:
    """Import and return a Pydantic ``BaseModel`` class by dotted path.

    Three checks run in order:

    1. ``path`` must start with one of ``_ALLOWED_PREFIXES``.
    2. The resolved object must be a subclass of ``pydantic.BaseModel``.
    3. The resolved class must not declare Pydantic fields named ``__ref__``
       or ``__type__``.

    Args:
        path: Dotted class path (e.g. ``"akgentic.core.agent_card.AgentCard"``).

    Returns:
        The imported class.

    Raises:
        CatalogValidationError: If any of the three checks fails. The error
            carries a single-element ``errors`` list with a substring-stable
            message (``"outside allowlist"``, ``"is not a Pydantic BaseModel
            subclass"``, or ``"reserved ref-sentinel fields"``) so callers
            can assert on behaviour without loading the exception chain.
    """
    if not any(path.startswith(prefix) for prefix in _ALLOWED_PREFIXES):
        raise CatalogValidationError([f"model_type '{path}' outside allowlist {_ALLOWED_PREFIXES}"])

    cls = import_class(path)

    if not (isinstance(cls, type) and issubclass(cls, BaseModel)):
        raise CatalogValidationError([f"model_type '{path}' is not a Pydantic BaseModel subclass"])

    collisions = sorted(_RESERVED_KEYS & set(cls.model_fields.keys()))
    if collisions:
        raise CatalogValidationError(
            [f"model_type '{path}' declares reserved ref-sentinel fields: {collisions}"]
        )

    return cls


def populate_refs(
    node: Any,
    repository: EntryRepository,
    namespace: str,
    _visiting: set[tuple[str, str]] | None = None,
    *,
    is_namespace_shareable: IsNamespaceShareableFn | None = None,
) -> Any:
    """Recursively replace ref markers in ``node`` with their resolved payloads.

    Walks the payload tree. A dict with a ``REF_KEY`` (``"__ref__"``) entry is
    treated as a ref marker and replaced by a **typed Pydantic instance** built
    from the target entry's declared ``model_type`` (Story 15.6); ordinary
    dicts and lists recurse structurally; every other value is returned
    unchanged. The ``namespace`` is forwarded as-is to every recursive call —
    cross-namespace resolution is structurally impossible at runtime.

    Return-type note (Story 15.6): nodes that are **not** ref markers still
    return structurally as ``dict | list | leaf``. Only ref-marker positions
    become ``BaseModel`` instances. Downstream ``cls.model_validate(...)`` calls
    accept nested Pydantic instances where a field's declared annotation is
    the instance's base class (Pydantic v2 behaviour), so the return-type
    widening is compatible with every existing caller (``resolve``,
    ``prepare_for_write``, ``validation._check_transient_validation``).

    Sibling overrides (Story 20.1 / ADR-010): a ref-marker dict may carry
    non-reserved sibling keys alongside ``__ref__`` / ``__type__``. Those
    siblings are read as a **shallow override** (top-level ``dict.update``
    semantics) and merged on top of the resolved target payload before the
    target's ``model_type`` validates the result. The merge is shallow — a
    sibling ``params: {role: "Manager"}`` **replaces** the target's
    ``params`` entirely rather than recursing into it. Override values may
    themselves be ref markers; they are resolved recursively with the same
    cycle-detection set as any other nested ref. If the ref marker has no
    non-reserved siblings, the resolver's behaviour is observationally
    identical to the pre-20.1 baseline.

    Args:
        node: Arbitrary payload subtree (dict, list, or leaf value).
        repository: Entry repository used to resolve refs in ``namespace``.
        namespace: The namespace used for every repository lookup in this call.
        _visiting: Internal cycle-detection set of ``(namespace, target_id)``
            pairs already traversed on the current ref chain. Callers pass
            ``None`` (the default); the function builds a fresh set per call.
        is_namespace_shareable: Optional callable answering "is this namespace
            cross-namespace-referenceable?". The resolver invokes it for every
            cross-ns marker — when it returns ``False`` (or when the argument
            is ``None``), the marker is rejected with the substring
            ``"is not shareable"``. Same-namespace refs bypass the gate. Per
            ADR-008 §D2 (updated 2026-05-08 rev 2) the canonical implementation
            consults the target namespace's ``_meta`` entry and answers
            ``True`` iff ``payload["shareable"] is True`` (typed bool at the
            root, strict-bool comparison). Default ``None`` ⇒ no namespace
            is shareable (cross-ns refs unconditionally rejected).

    Returns:
        A new payload subtree with every ref marker replaced by a typed
        Pydantic instance of the referenced entry's ``model_type``. Dict and
        list inputs are never mutated — fresh containers are built for every
        recursion.

    Raises:
        CatalogValidationError: If a ref cycle is detected, the target id is
            absent from ``repository``, a ``TYPE_KEY`` hint does not match
            the target entry's ``model_type``, or the target entry's payload
            fails validation against its own ``model_type`` class (after any
            sibling override merge). The error carries a single-element
            ``errors`` list with substring-stable messages (``"cycle"``,
            ``"not found"``, ``"expected X"`` + ``"got Y"``, or ``"Payload of
            '<id>' does not validate against <model_type>"``).
    """
    visiting: set[tuple[str, str]] = set() if _visiting is None else _visiting

    if isinstance(node, dict):
        if REF_KEY in node:
            return _populate_ref_marker(
                node,
                repository,
                namespace,
                visiting,
                is_namespace_shareable=is_namespace_shareable,
            )
        return {
            k: populate_refs(
                v,
                repository,
                namespace,
                visiting,
                is_namespace_shareable=is_namespace_shareable,
            )
            for k, v in node.items()
        }

    if isinstance(node, list):
        return [
            populate_refs(
                v,
                repository,
                namespace,
                visiting,
                is_namespace_shareable=is_namespace_shareable,
            )
            for v in node
        ]

    return node


def _resolve_target_namespace(
    node: dict[str, Any],
    current_namespace: str,
) -> tuple[str, str]:
    """Return ``(target_namespace, target_id)`` for a ref-marker dict.

    Implements ADR-008 §D2 cross-namespace ref shape resolution. The
    canonical form carries an explicit ``__namespace__`` key; the shorthand
    encodes the namespace as a ``<ns>.<id>`` prefix on the ``__ref__`` value
    and the resolver splits on the FIRST dot only (so ids may continue to
    contain ``.``). When both shapes are present and disagree, raise.

    Args:
        node: The ref-marker dict (must contain ``REF_KEY``).
        current_namespace: The enclosing namespace passed by ``populate_refs``;
            used when neither shorthand nor explicit ``__namespace__`` is set.

    Returns:
        A pair ``(target_namespace, target_id)``. ``target_namespace`` equals
        ``current_namespace`` when the marker carries no cross-ns hint.

    Raises:
        CatalogValidationError: When shorthand and explicit ``__namespace__``
            both appear and disagree.
    """
    raw_ref: Any = node[REF_KEY]
    explicit_ns: str | None = node.get(NAMESPACE_KEY)
    # Shorthand parsing — first-dot split. Ids may continue to contain dots.
    if isinstance(raw_ref, str) and "." in raw_ref:
        shorthand_ns, shorthand_id = raw_ref.split(".", 1)
        if explicit_ns is not None and explicit_ns != shorthand_ns:
            raise CatalogValidationError(
                [
                    f"Ref marker has both shorthand 'ns.id' and explicit "
                    f"__namespace__ — these disagree: '{shorthand_ns}' vs "
                    f"'{explicit_ns}'"
                ]
            )
        return shorthand_ns, shorthand_id
    if explicit_ns is not None:
        return explicit_ns, raw_ref
    return current_namespace, raw_ref


def _check_cross_ns_shareable_flag(
    target_namespace: str,
    target_id: str,
    current_namespace: str,
    is_namespace_shareable: IsNamespaceShareableFn | None,
) -> None:
    """Raise when a cross-ns ref points at a non-shareable namespace.

    The same-namespace path is exempt — a marker resolving to
    ``current_namespace`` is not a cross-ns ref and is never gated by the
    shareable-flag check.

    Per ADR-008 §D2 (updated 2026-05-08, rev 2), a namespace is "shareable"
    iff its ``_meta`` entry's ``payload["shareable"] is True`` (typed bool
    at the root, strict-bool comparison — no truthy-string coercion). The
    check is delegated to the ``is_namespace_shareable`` callable so the
    resolver stays a pure function over the repository protocol — the
    per-``Catalog`` shareable-flag cache lives on the ``Catalog`` instance
    and is consulted via this callback.

    Args:
        target_namespace: The namespace resolved from the marker (canonical
            ``__namespace__`` or shorthand prefix).
        target_id: The id resolved from the marker (used for the error
            message only).
        current_namespace: The enclosing namespace; refs resolving to this
            namespace bypass the gate.
        is_namespace_shareable: Callable answering "is this namespace
            shareable?" or ``None``. ``None`` is interpreted as "no
            namespace is shareable" — every cross-ns ref is unconditionally
            rejected with the ``"is not shareable"`` substring.

    Raises:
        CatalogValidationError: When ``target_namespace != current_namespace``
            and ``is_namespace_shareable`` returns ``False`` (or is ``None``).
            The message carries the substring
            ``"namespace '<target_namespace>' is not shareable"``.
    """
    if target_namespace == current_namespace:
        return
    if is_namespace_shareable is not None and is_namespace_shareable(target_namespace):
        return
    raise CatalogValidationError(
        [
            f"Cross-namespace ref to '{target_namespace}.{target_id}': "
            f"namespace '{target_namespace}' is not shareable"
        ]
    )


def _populate_ref_marker(
    node: dict[str, Any],
    repository: EntryRepository,
    namespace: str,
    visiting: set[tuple[str, str]],
    *,
    is_namespace_shareable: IsNamespaceShareableFn | None = None,
) -> Any:
    """Resolve a single ref-marker dict into a typed Pydantic instance.

    Checks run in order — parse target namespace (canonical
    ``__namespace__`` or first-dot shorthand), shareable-flag gate (cross-ns
    only), cycle, missing target, ``__type__`` mismatch, then (Story 15.6)
    recursive population of nested refs inside the target's payload,
    optional shallow merge of non-reserved siblings on top of that payload
    (Story 20.1 / ADR-010), ``load_model_type`` on the target's declared
    ``model_type``, and ``cls.model_validate`` to build a typed instance.

    Order rationale: the shareable-flag gate fires BEFORE the repository
    lookup so a denied cross-ns ref produces the ``"is not shareable"`` error
    even when the target id genuinely does not exist (ADR-008 §D2 —
    denying access takes precedence over reporting "not found"). Cycle
    comes next because the cross-ns target may be visited along a chain we
    already walked. ``__type__`` mismatch and downstream pure-data errors
    follow.

    Sibling-override semantics (ADR-010): any keys on ``node`` other than
    ``__ref__`` / ``__type__`` / ``__namespace__`` are collected as
    overrides. When non-empty, the override dict is itself run through
    :func:`populate_refs` against the **target's namespace** (so nested
    cross-ns refs in the override are subject to the same shareable-flag +
    ownership gates), then shallow-merged on top of the populated target
    payload via ``{**target, **overrides}``. The merge is top-level — no
    recursive descent into shared subkeys — and runs before
    ``cls.model_validate``. When the ref marker has no non-reserved
    siblings, this branch is skipped entirely.
    """
    target_namespace, target_id = _resolve_target_namespace(node, namespace)
    expected = node.get(TYPE_KEY)
    key = (target_namespace, target_id)

    # Shareable-flag gate fires BEFORE repository.get so a denied cross-ns
    # ref raises the "is not shareable" error even when the target id does
    # not exist.
    _check_cross_ns_shareable_flag(target_namespace, target_id, namespace, is_namespace_shareable)

    if key in visiting:
        raise CatalogValidationError(
            [f"Reference cycle detected at ({target_namespace}, {target_id})"]
        )

    target = repository.get(target_namespace, target_id)
    if target is None:
        raise CatalogValidationError(
            [f"Ref '{target_id}' not found in namespace '{target_namespace}'"]
        )

    if expected is not None and target.model_type != expected:
        raise CatalogValidationError(
            [f"Ref '{target_id}' expected {expected}, got {target.model_type}"]
        )

    # Story 15.6: recurse into the target's payload (resolves nested refs),
    # then validate against the target's declared model_type so the splice
    # becomes a typed instance. Recursion runs in the TARGET's namespace so
    # nested same-ns refs in a cross-ns target's payload resolve correctly.
    populated_payload = populate_refs(
        target.payload,
        repository,
        target_namespace,
        visiting | {key},
        is_namespace_shareable=is_namespace_shareable,
    )

    # Story 20.1 / ADR-010: merge non-reserved siblings on top of the target
    # payload as a shallow override. The override dict is resolved against
    # the TARGET's namespace so nested cross-ns refs in the override are
    # gated by the same shareable-flag + ownership rules.
    overrides = {k: v for k, v in node.items() if k not in _RESERVED_KEYS}
    if overrides:
        resolved_overrides = populate_refs(
            overrides,
            repository,
            target_namespace,
            visiting | {key},
            is_namespace_shareable=is_namespace_shareable,
        )
        # resolved_overrides is always a dict here: populate_refs preserves
        # the dict shape of any non-ref-marker dict input (the outer override
        # dict has no __ref__ at its top level — only its values may).
        if not isinstance(populated_payload, dict):  # pragma: no cover — defensive
            # Target payloads are always dicts for v2 catalog entries; this
            # guard pins the invariant for readers rather than handling a
            # real failure mode.
            raise CatalogValidationError(
                [
                    f"Cannot merge sibling overrides onto '{target.id}': "
                    "resolved target payload is not a dict"
                ]
            )
        merged = {**populated_payload, **resolved_overrides}
    else:
        merged = populated_payload

    cls = load_model_type(target.model_type)
    try:
        instance = cls.model_validate(merged)
    except ValidationError as e:
        raise CatalogValidationError(
            [f"Payload of '{target.id}' does not validate against {target.model_type}: {e}"]
        ) from e
    # ADR-015: NativeValue is the catalog's sanctioned scalar carrier. At the
    # ref-splice site we return the bare ``.value`` (str / int / bool / list /
    # dict) so a typed field on the consuming entry can be assigned via a
    # ``__ref__`` marker without inlining the scalar. Every other model_type
    # returns the validated BaseModel unchanged.
    if isinstance(instance, NativeValue):
        return instance.value
    return instance


def resolve(
    entry: Entry,
    repository: EntryRepository,
    *,
    is_namespace_shareable: IsNamespaceShareableFn | None = None,
) -> BaseModel:
    """Hydrate ``entry`` into an instance of its declared runtime Pydantic class.

    Composition: ``cls = load_model_type(entry.model_type)``; ``populated =
    populate_refs(entry.payload, repository, entry.namespace)``; finally
    ``cls.model_validate(populated)``. Errors from ``load_model_type`` and
    ``populate_refs`` propagate unchanged (per AC14/AC15). Only the
    Pydantic ``ValidationError`` from ``model_validate`` is converted to
    ``CatalogValidationError``, preserving the original traceback via
    ``raise ... from e`` (AC16).

    Story 15.6 note: ``populate_refs`` may now return a tree that contains
    nested typed Pydantic instances at ref-marker positions. The final
    ``cls.model_validate`` call accepts such trees — Pydantic v2 treats a
    concrete subclass instance as a valid value wherever the declared
    annotation is the instance's base class, so polymorphic fields
    (``list[ToolCard]``, ``list[type]``, …) validate without
    per-payload workarounds.

    Args:
        entry: The catalog entry to hydrate.
        repository: Entry repository used to resolve refs in ``entry.namespace``.

    Returns:
        A runtime instance of the class named by ``entry.model_type``.

    Raises:
        CatalogValidationError: From ``load_model_type`` (allowlist, BaseModel,
            reserved-key checks), from ``populate_refs`` (cycle, missing, type
            mismatch), or wrapping a ``pydantic.ValidationError`` from
            ``model_validate`` with the substring ``"Payload does not validate
            against"`` followed by the ``model_type`` string.
    """
    cls = load_model_type(entry.model_type)
    populated = populate_refs(
        entry.payload,
        repository,
        entry.namespace,
        is_namespace_shareable=is_namespace_shareable,
    )
    try:
        return cls.model_validate(populated)
    except ValidationError as e:
        raise CatalogValidationError(
            [f"Payload does not validate against {entry.model_type}: {e}"]
        ) from e


def reconcile_refs(input_node: Any, dumped_node: Any) -> Any:
    """Restore ref markers from ``input_node`` against a dumped Pydantic tree.

    The dumped tree (from ``obj.model_dump(mode='python', exclude_unset=True)``)
    is the authority for every non-ref field — it carries validator-normalised
    values. Author-written ref markers in ``input_node`` must win verbatim so
    that the stored payload round-trips back to itself when re-resolved.

    A ref-marker dict is returned verbatim as a whole — any non-reserved
    sibling keys the author wrote next to ``__ref__`` (Story 20.1 /
    ADR-010, shallow overrides) survive the write path unchanged, because
    this function never recurses into a ref-marker dict's values. The
    stored payload therefore resolves to the same in-memory shape when
    re-read, including its override values.

    Args:
        input_node: The original payload subtree the author wrote (may carry
            ``REF_KEY`` markers, optionally with sibling overrides).
        dumped_node: The corresponding subtree from ``model_dump``.

    Returns:
        A reconciled subtree: ref markers preserved verbatim from ``input_node``,
        non-ref dict keys recursed pairwise, list elements zipped pairwise with
        strict length, and leaves taken from ``dumped_node``. Neither input
        tree is mutated.

    Raises:
        ValueError: If ``input_node`` and ``dumped_node`` are both lists but of
            mismatched lengths (``zip(..., strict=True)`` raises).
    """
    if isinstance(input_node, dict):
        if REF_KEY in input_node:
            return input_node
        return _reconcile_dict(input_node, dumped_node)

    if isinstance(input_node, list) and isinstance(dumped_node, list):
        return [reconcile_refs(i, d) for i, d in zip(input_node, dumped_node, strict=True)]

    return dumped_node


def _reconcile_dict(input_node: dict[str, Any], dumped_node: Any) -> dict[str, Any]:
    """Reconcile a non-ref input dict against the dumped counterpart.

    Iterates input keys in order. For each key: if it is present in
    ``dumped_node``, recurse pairwise; otherwise, if the input value is itself
    a ref-marker dict, preserve it verbatim (the "unset-but-refed" branch from
    AC19); otherwise drop the key (the dumped tree is authoritative for
    absent non-ref fields).
    """
    dumped_dict = dumped_node if isinstance(dumped_node, dict) else {}
    result: dict[str, Any] = {}
    for key, value in input_node.items():
        if key in dumped_dict:
            result[key] = reconcile_refs(value, dumped_dict[key])
        elif isinstance(value, dict) and REF_KEY in value:
            result[key] = value
    return result


def prepare_for_write(
    entry: Entry,
    repository: EntryRepository,
    *,
    is_namespace_shareable: IsNamespaceShareableFn | None = None,
) -> Entry:
    """Run the five-step write pipeline and return a ref-preserving ``Entry``.

    Pipeline (each step's failure short-circuits the remainder, raising
    ``CatalogValidationError`` before the next step runs):

    1. ``resolved = populate_refs(entry.payload, repo, entry.namespace)``
       — surfaces missing target, ``__type__`` mismatch, and cycle errors.
    2. ``cls = load_model_type(entry.model_type)``
       — surfaces allowlist / reserved-key / non-BaseModel errors.
    3. ``obj = cls.model_validate(resolved)``
       — ``ValidationError`` is converted to ``CatalogValidationError`` with
       the substring ``"Payload does not validate against"`` and the
       ``model_type`` string, chained via ``from e``.
    4. ``dumped = obj.model_dump(mode="python", exclude_unset=True)``
       — intent-preserving dump.
    5. ``stored = reconcile_refs(entry.payload, dumped)``
       — restore the author's ref markers on top of the dumped tree.

    The returned ``Entry`` is ``entry.model_copy(update={"payload": stored})``
    — a shallow copy; the input ``entry`` is not mutated.

    Ownership enforcement (team-entry existence + ``user_id`` match) is
    currently the ``Catalog`` service's responsibility and will remain outside
    this function until Story 15.5 finalises the placement.

    Args:
        entry: Candidate entry to persist.
        repository: Entry repository for ref resolution in ``entry.namespace``.

    Returns:
        A new ``Entry`` with the reconciled, intent-preserving payload.

    Raises:
        CatalogValidationError: From any pipeline step that fails.
    """
    resolved = populate_refs(
        entry.payload,
        repository,
        entry.namespace,
        is_namespace_shareable=is_namespace_shareable,
    )
    cls = load_model_type(entry.model_type)
    obj = _validate_payload(cls, resolved, entry.model_type)
    dumped = obj.model_dump(mode="python", exclude_unset=True)
    stored = reconcile_refs(entry.payload, dumped)
    return entry.model_copy(update={"payload": stored})


def _validate_payload(cls: type[BaseModel], resolved: Any, model_type: str) -> BaseModel:
    """Run ``cls.model_validate`` and convert Pydantic errors to catalog errors.

    Extracted so ``prepare_for_write`` stays short; the substring
    ``"Payload does not validate against"`` and the ``model_type`` string are
    pinned by AC23 step 3 and AC16.
    """
    try:
        return cls.model_validate(resolved)
    except ValidationError as e:
        raise CatalogValidationError(
            [f"Payload does not validate against {model_type}: {e}"]
        ) from e


def validate_delete(namespace: str, id: str, repository: EntryRepository) -> list[str]:
    """Return blocker messages for deleting ``(namespace, id)``; empty means safe.

    Two-phase check:

    1. If ``repository.get(namespace, id)`` returns ``None``, the target does
       not exist — surface a single-element list containing ``"not found"``
       and the offending ``(namespace, id)`` pair.
    2. Otherwise, call ``repository.find_references(namespace, id)`` and build
       one blocker message per inbound referrer, preserving repository order.

    No branching on ``user_id`` or ``kind`` — the rule is uniform across
    enterprise and user namespaces per ADR-007/ADR-008: any inbound ref
    within the passed namespace blocks the delete.

    Args:
        namespace: Namespace of the entry to delete.
        id: Id of the entry to delete within ``namespace``.
        repository: Entry repository providing ``get`` and ``find_references``.

    Returns:
        A list of human-readable blocker messages. An empty list is the
        "safe to delete" signal.
    """
    target = repository.get(namespace, id)
    if target is None:
        return [f"Entry ({namespace}, {id}) not found"]

    referrers = repository.find_references(namespace, id)
    return [
        f"Entry '{r.id}' (kind={r.kind}) in namespace '{namespace}' references '{id}'"
        for r in referrers
    ]


def enumerate_allowlisted_model_types() -> list[str]:
    """Enumerate allowlisted ``BaseModel`` subclasses loaded under ``akgentic.*``.

    Walks a snapshot of ``sys.modules`` to avoid mutation-during-iteration
    issues. Per-module introspection errors are swallowed — optional
    dependencies may be absent or partially imported. ``load_model_type``
    acts as the authoritative allowlist + ``BaseModel`` + reserved-key gate
    so enumeration never broadens the allowlist.

    Used by both the REST router (``GET /catalog/model_types``) and the
    ``ak-catalog model-types`` CLI verb.
    """
    results: set[str] = set()
    modules_snapshot = list(sys.modules.items())
    for module_name, module in modules_snapshot:
        if not module_name.startswith("akgentic.") or module is None:
            continue
        _collect_allowlisted(module, results)
    return sorted(results)


def _collect_allowlisted(module: Any, results: set[str]) -> None:
    """Add every allowlisted ``BaseModel`` subclass from ``module`` into ``results``."""
    try:
        items = list(vars(module).items())
    except Exception:  # noqa: BLE001 — defensive; partially imported modules
        return
    for _name, value in items:
        if not isinstance(value, type) or not issubclass(value, BaseModel):
            continue
        path = f"{value.__module__}.{value.__name__}"
        if not path.startswith("akgentic.") or path in results:
            continue
        try:
            load_model_type(path)
        except Exception:  # noqa: BLE001 — swallow reserved-key or import errors
            continue
        results.add(path)
