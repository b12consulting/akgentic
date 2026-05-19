"""Namespace-bundle YAML serialization for the catalog v2 service.

This module owns the single-bundle YAML format defined in architecture shard 09:
an entire user or enterprise namespace serialised as one self-contained YAML
document with document-level ``namespace`` + ``user_id`` (and optional header
projection — ``name`` / ``description`` / ``properties`` — hoisted from the
namespace's ``_meta`` entry) plus per-entry fields nested under ``entries.<id>``.

Story 17.6 — the wire format is the pre-17.5 dict-keyed shape extended with:

* Top-level ``name`` / ``description`` / ``properties`` (header projection).
* Per-kind external sections inside ``entries:`` keyed by composite ``<ns>.<id>``
  for cross-namespace targets (collected upstream by
  :meth:`Catalog._collect_external_refs`).

The module exposes two pure functions:

* :func:`dump_namespace` — serialise ``list[Entry]`` (+ optional header +
  optional external refs) to YAML ``str``.
* :func:`load_namespace` — parse YAML ``str`` into ``(list[Entry], BundleHeader)``.

The module is repository-agnostic: neither function performs repository I/O,
runs ``prepare_for_write``, or mutates any catalog state. The service-level
``Catalog.export_namespace_yaml`` / ``import_namespace_yaml`` methods own the
repository boundary; this module owns only the wire format.

``load_namespace`` is deliberately kept pure (no ``prepare_for_write``) so
``Catalog.validate_namespace_yaml`` can reuse it in-process for dry-run
validation of proposed bundles.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

import yaml
from pydantic import ValidationError

from akgentic.catalog.models.entry import Entry
from akgentic.catalog.models.errors import CatalogValidationError
from akgentic.catalog.repositories.yaml import _BlockScalarDumper

__all__ = ["BundleHeader", "dump_namespace", "load_namespace"]

logger = logging.getLogger(__name__)


@dataclass
class BundleHeader:
    """Parsed top-level header fields for an export bundle.

    Story 17.7 added the fourth field ``shareable: bool`` after ``properties``;
    Story 18.2 adds the fifth field ``public: bool`` after ``shareable`` to
    mirror :class:`~akgentic.catalog.models.namespace_meta.NamespaceMeta`'s
    declaration order. Bundles produced by 18.2+ exports always carry
    ``public`` at the top level (alongside ``name`` / ``description`` /
    ``properties`` / ``shareable``); legacy export bundles (pre-18.2 — no
    ``public``) project to ``public=False`` (default).

    ``present`` is ``True`` iff at least one of ``name`` / ``description`` /
    ``properties`` / ``shareable`` / ``public`` was explicitly set in the
    source YAML. Pre-17.5 bundles (no header fields at all) parse to
    ``BundleHeader(present=False, ...)`` so the import handler can skip the
    meta-upsert step (AC11 / AC12).
    """

    name: str = ""
    description: str = ""
    properties: dict[str, str] = field(default_factory=dict)
    shareable: bool = False
    public: bool = False
    present: bool = False


# Kind emit order for bundle serialisation: team → meta → agent → prompt → tool → model.
# Reading a bundle top-down then mirrors the consumption graph: teams consume
# agents; agents consume prompts, tools, and models. ``meta`` follows ``team``
# because both describe the namespace as a whole (ADR-008 §D1). ``EntryKind``
# is a closed ``Literal`` of exactly these six values, so indexing by ``e.kind``
# is safe without a fallback. Story 17.6: the meta entry is hoisted to the
# header and never appears in ``entries:`` for bundles produced by this
# function, but the kind is retained in the table for the defensive case
# where a caller passes a meta entry through ``dump_namespace`` directly.
_KIND_EMIT_ORDER: dict[str, int] = {
    "team": 0,
    "meta": 1,
    "agent": 2,
    "prompt": 3,
    "tool": 4,
    "model": 5,
}

# Story 17.6: external sections emit AFTER all local sections, in pre-17.5
# kind order (team → agent → prompt → tool → model). Meta is intentionally
# NOT in this ordering — meta entries are namespace-level metadata and never
# appear as cross-ns targets.
_EXTERNAL_KIND_ORDER: list[str] = ["team", "agent", "prompt", "tool", "model"]

# Section-header comment strings for each local kind, aligned to an 80-character
# visual width and bracketed with ``####`` markers. The character ─ is U+2500
# (1 Python char, 3 UTF-8 bytes). Pinned as frozen strings keyed by lowercase
# kind name — deliberately NOT computed from kind.capitalize() so a future
# EntryKind rename cannot silently shift header text.
_KIND_HEADERS: dict[str, str] = {
    "team": "  #### ─── Teams ".ljust(75, "─") + " ####",
    "meta": "  #### ─── Meta ".ljust(75, "─") + " ####",
    "agent": "  #### ─── Agents ".ljust(75, "─") + " ####",
    "prompt": "  #### ─── Prompts ".ljust(75, "─") + " ####",
    "tool": "  #### ─── Tools ".ljust(75, "─") + " ####",
    "model": "  #### ─── Models ".ljust(75, "─") + " ####",
}

# Story 17.6 — section headers for external (cross-ns, readonly) entries.
# One per kind that may appear in an external section.
_EXTERNAL_KIND_HEADERS: dict[str, str] = {
    "team": "  #### ─── Teams (External ref, readonly) ".ljust(75, "─") + " ####",
    "agent": "  #### ─── Agents (External ref, readonly) ".ljust(75, "─") + " ####",
    "prompt": "  #### ─── Prompts (External ref, readonly) ".ljust(75, "─") + " ####",
    "tool": "  #### ─── Tools (External ref, readonly) ".ljust(75, "─") + " ####",
    "model": "  #### ─── Models (External ref, readonly) ".ljust(75, "─") + " ####",
}

# Regex patterns used by the post-processor.
# Matches a top-level entry key: exactly 2 spaces + identifier (allowing dots
# and underscores so composite ``<ns>.<id>`` keys are recognised) + colon.
_ENTRY_KEY_RE = re.compile(r"^  [A-Za-z0-9_\-.]+:$")
# Matches the kind line of an entry: 4 spaces + "kind: " + kind value.
_KIND_LINE_RE = re.compile(r"^    kind: ([a-z]+)$")


# --- dump_namespace ---------------------------------------------------------


def dump_namespace(
    entries: list[Entry],
    *,
    name: str = "",
    description: str = "",
    properties: dict[str, str] | None = None,
    shareable: bool = False,
    public: bool = False,
    external_refs: list[Entry] | None = None,
) -> str:
    """Serialise a uniform-namespace list of entries to bundle YAML.

    The output document carries up to eight top-level keys in declaration
    order: ``namespace``, ``user_id``, ``name``, ``description``,
    ``properties``, ``shareable``, ``public``, ``entries``. The header
    (Story 17.7 added ``shareable`` after ``properties``; Story 18.2 adds
    ``public`` after ``shareable``) is emitted ONLY when at least one of
    the six optional parameters forces it: ``name`` is non-empty,
    ``description`` is non-empty, ``properties`` is non-empty, ``shareable``
    is ``True``, ``public`` is ``True``, or ``external_refs`` is non-empty.
    When all six parameters are at their default values, the function emits
    the pre-17.5 wire shape verbatim (three top-level keys: ``namespace``,
    ``user_id``, ``entries``) so legacy callers see no behaviour change.

    Each value under ``entries`` is keyed either by the entry id (local) or
    by the composite ``<entry.namespace>.<entry.id>`` (external). Local values
    map to four per-entry fields in declaration order: ``kind``, ``model_type``,
    ``description``, ``payload``. The ``id``, ``namespace`` and ``user_id``
    fields are NOT duplicated inside the per-entry maps — they are implied by
    the document context and the outer key.

    Ownership invariant: every entry in ``entries`` MUST share the same
    ``user_id`` (a real string after Story 18.1 — ``"anonymous"`` for
    community-tier exports; the authenticated caller's identifier on
    department / enterprise tier).
    Namespace invariant: every entry MUST share the same ``namespace``.
    Both invariants are checked together before emit; violations raise
    ``CatalogValidationError`` with one message per offender.

    ``payload`` values pass through verbatim — ref markers (``__ref__`` /
    ``__type__``) are preserved unchanged. ``dump_namespace`` does NOT
    re-resolve, re-validate, or re-reconcile; stored payloads are already
    intent-preserving.

    Local entries are emitted in a stable order grouped by kind in
    consumption order — ``team`` → ``meta`` → ``agent`` → ``prompt`` →
    ``tool`` → ``model`` — and within each kind sorted by ``id``
    (lexicographic). External sections emit AFTER all local kinds, in pre-17.5
    kind order (team → agent → prompt → tool → model — no meta), each
    section sorted by ``(namespace, id)`` ascending.

    Args:
        entries: Non-empty list of ``Entry`` instances sharing a single
            namespace and user_id.
        name: Optional namespace display name (header projection from
            ``_meta.payload`` with team-payload fallback). Default ``""``
            suppresses the header.
        description: Optional namespace description. Default ``""``.
        properties: Optional namespace properties mapping. Default ``None``
            is treated as empty.
        external_refs: Optional list of cross-namespace target entries. When
            non-empty, emit per-kind external sections inside ``entries:``
            using composite ``<ns>.<id>`` keys.

    Returns:
        A YAML document string produced via ``yaml.dump`` with
        ``sort_keys=False``, ``allow_unicode=True``, and
        ``default_flow_style=False``, post-processed to add section headers
        and blank-line separators.

    Raises:
        CatalogValidationError: When ``entries`` is empty, or when any
            entry's ``user_id`` / ``namespace`` disagrees with the first
            entry's values.
    """
    if not entries:
        raise CatalogValidationError(["bundle must declare at least one entry"])

    errors: list[str] = []
    errors.extend(_check_uniform_owner(entries))
    errors.extend(_check_uniform_namespace(entries))
    if errors:
        raise CatalogValidationError(errors)

    properties = properties if properties is not None else {}
    external_refs = external_refs if external_refs is not None else []
    has_header = (
        bool(name)
        or bool(description)
        or bool(properties)
        or shareable
        or public
        or bool(external_refs)
    )

    sorted_entries = _sort_entries_for_emit(entries)
    entries_map: dict[str, Any] = {e.id: _entry_to_map(e) for e in sorted_entries}
    if external_refs:
        _append_external_sections(entries_map, external_refs)

    doc: dict[str, Any] = {
        "namespace": entries[0].namespace,
        "user_id": entries[0].user_id,
    }
    if has_header:
        doc["name"] = name
        doc["description"] = description
        doc["properties"] = properties
        # Story 17.7 — emit ``shareable`` between ``properties`` and
        # ``entries`` whenever a header is emitted (declaration order pinned
        # by NamespaceMeta + AC8). When the header is suppressed entirely
        # (pre-17.5 three-key shape), ``shareable`` is also suppressed.
        doc["shareable"] = shareable
        # Story 18.2 — emit ``public`` immediately after ``shareable`` so the
        # wire-format declaration order matches NamespaceMeta. When the
        # header is suppressed entirely, ``public`` is also suppressed
        # (pre-17.5 three-key shape preserved).
        doc["public"] = public
    doc["entries"] = entries_map

    raw = yaml.dump(
        doc,
        Dumper=_BlockScalarDumper,
        sort_keys=False,
        allow_unicode=True,
        default_flow_style=False,
    )
    return _format_bundle_sections(raw)


def _append_external_sections(entries_map: dict[str, Any], external_refs: list[Entry]) -> None:
    """Append composite-keyed external sections to ``entries_map``.

    Inserts a sentinel comment marker before each kind transition so the
    post-processor can plant the matching ``External-ref, readonly`` header.
    Empty kinds (no external entry of that kind) emit no marker and no
    header at all, per AC5.
    """
    for kind in _EXTERNAL_KIND_ORDER:
        kind_entries = sorted(
            (e for e in external_refs if e.kind == kind),
            key=lambda e: (e.namespace, e.id),
        )
        if not kind_entries:
            continue
        # Sentinel key — must NOT contain characters that PyYAML would need
        # to quote (no ``:``, no ``.``, no leading dash). Underscores + ASCII
        # letters keep PyYAML in the bare-string emit path so the post-
        # processor's regex match is stable. The colon-free shape is
        # intentional: a YAML key containing ``:`` is forced into quoted form
        # by PyYAML, which would break the post-processor's prefix match.
        marker_key = f"__external_marker_{kind}"
        entries_map[marker_key] = None
        for e in kind_entries:
            composite_key = f"{e.namespace}.{e.id}"
            entries_map[composite_key] = _entry_to_map(e)


def _peek_kind(lines: list[str], i: int) -> str:
    """Return the kind value from the line immediately following entry key at index i."""
    for j in range(i + 1, len(lines)):
        m = _KIND_LINE_RE.match(lines[j])
        if m:
            return m.group(1)
    raise AssertionError("unreachable: every entry must start with `kind:`")


def _format_bundle_sections(yaml_text: str) -> str:
    """Post-process a raw PyYAML bundle string to add section headers and spacing.

    Inserts a kind-section comment header (from ``_KIND_HEADERS``) and a blank
    line at each kind transition inside the ``entries:`` block. Consecutive
    entries within the same kind are separated by exactly one blank line. The
    header line for a kind is preceded by one blank line (visual gap after the
    previous section or after the ``entries:`` key). External sections (planted
    via the sentinel marker) replace the marker line with the
    External-ref-suffixed header.

    The document ends with exactly one trailing newline.
    """
    lines = yaml_text.rstrip("\n").split("\n")
    output: list[str] = []
    last_kind: str | None = None
    in_external = False
    pending_external_header: str | None = None
    for i, line in enumerate(lines):
        # External-section sentinel — a YAML ``__external_marker__:<kind>: null``
        # pair planted by ``_append_external_sections``. Replace with the
        # External-ref-suffixed header; suppress the local header for the
        # entry that follows (its kind already matches the section).
        marker_kind = _try_parse_external_marker_yaml(line)
        if marker_kind is not None:
            output.append("")
            output.append(_EXTERNAL_KIND_HEADERS[marker_kind])
            in_external = True
            pending_external_header = marker_kind
            last_kind = marker_kind  # the next entry key matches this kind
            continue
        if _ENTRY_KEY_RE.match(line):
            kind = _peek_kind(lines, i)
            if pending_external_header is not None:
                # First entry under a freshly-emitted external section header.
                # The header already wrote the blank-above; still need a blank
                # line between the header and the entry key to honour AC5
                # ("every section header is surrounded by a blank line above
                # and a blank line below").
                output.append("")
                pending_external_header = None
            elif kind != last_kind:
                # Kind transition within local sections — emit a fresh local
                # header. External sections never reach this branch (the
                # marker pre-set ``last_kind`` to the external kind).
                output.append("")
                if not in_external:
                    output.append(_KIND_HEADERS[kind])
                last_kind = kind
            else:
                # Same-kind continuation — blank line between consecutive
                # entries in the same section (local OR external).
                output.append("")
            output.append(line)
        else:
            output.append(line)
    return "\n".join(output) + "\n"


def _try_parse_external_marker_yaml(line: str) -> str | None:
    """If ``line`` is the YAML form of an external-section sentinel, return its kind.

    The sentinel is inserted as ``__external_marker_<kind>`` keyed at indent 2
    with a ``None`` value. PyYAML emits ``  __external_marker_<kind>: null``.
    The kind suffix is one of the closed ``EntryKind`` values that may appear
    in an external section: team / agent / prompt / tool / model.
    """
    prefix = "  __external_marker_"
    if not line.startswith(prefix):
        return None
    rest = line[len(prefix) :]
    if rest.endswith(": null"):
        return rest[: -len(": null")]
    if rest.endswith(":"):
        return rest[:-1]
    return None


def _check_uniform_owner(entries: list[Entry]) -> list[str]:
    """Return one error message per entry whose ``user_id`` disagrees with the first."""
    expected = entries[0].user_id
    return [
        f"entry '{e.id}' has user_id={e.user_id!r} but bundle user_id is {expected!r}"
        for e in entries
        if e.user_id != expected
    ]


def _check_uniform_namespace(entries: list[Entry]) -> list[str]:
    """Return one error message per entry whose ``namespace`` disagrees with the first."""
    expected = entries[0].namespace
    return [
        f"entry '{e.id}' has namespace={e.namespace!r} but bundle namespace is {expected!r}"
        for e in entries
        if e.namespace != expected
    ]


def _sort_entries_for_emit(entries: list[Entry]) -> list[Entry]:
    """Return a new list sorted by (kind emit order, id)."""
    return sorted(entries, key=lambda e: (_KIND_EMIT_ORDER[e.kind], e.id))


def _entry_to_map(entry: Entry) -> dict[str, Any]:
    """Return the per-entry YAML map with the four pinned keys in declaration order."""
    return {
        "kind": entry.kind,
        "model_type": entry.model_type,
        "description": entry.description,
        "payload": entry.payload,
    }


# --- load_namespace ---------------------------------------------------------


def load_namespace(yaml_text: str) -> tuple[list[Entry], BundleHeader]:
    """Parse a bundle YAML document and return ``(entries, header)``.

    Story 17.6 — the parser is structurally strict: the root must be a
    ``dict`` with ``namespace`` (non-empty ``str``), ``user_id`` (non-empty
    ``str``; legacy ``null`` is accepted on read and rewritten to
    ``"anonymous"`` per Story 18.1), and ``entries`` (non-empty ``dict``).
    Optional top-level fields ``name`` (``str``), ``description`` (``str``),
    ``properties`` (``dict[str, str]``), ``shareable`` (``bool``), and
    ``public`` (``bool``) are projected into the returned
    :class:`BundleHeader`. Any missing or wrong-typed required key
    accumulates into a single ``CatalogValidationError`` — the error list
    is NOT short-circuited after the first failure so frontends can render
    every issue in one pass.

    Each ``(entry_key, entry_map)`` pair under ``entries:`` is split on the
    FIRST ``.`` to decide local vs. external:

    * Key contains no ``.`` → local entry. The handler constructs an ``Entry``
      with ``id=key``, ``namespace=<doc namespace>``, ``user_id=<doc user_id>``.
    * Key contains at least one ``.`` → external entry (cross-namespace
      target). The handler **SKIPS** it entirely (the import path treats
      external entries as readonly display projection — they belong to other
      namespaces and are not part of this namespace's atomic-replace contents).

    A bundle whose ``entries:`` block is a YAML list (the rejected Story 17.5
    wire shape) raises ``CatalogValidationError`` with an explicit message —
    the field-shape is unambiguous, so an explicit error is preferable to
    silent degradation.

    The function is parse-only: it does NOT call ``prepare_for_write``, does
    NOT touch a repository, and does NOT persist anything.

    Args:
        yaml_text: The full bundle YAML document as a string.

    Returns:
        ``(entries, header)`` — the list of parsed local ``Entry`` instances
        in dict-iteration order (PyYAML yields keys in document order), plus
        the :class:`BundleHeader` projection of the optional top-level
        header trio (``present=False`` when none of the three header fields
        appears in the source).

    Raises:
        CatalogValidationError: On malformed YAML, structural failures,
            empty / no-local-entries collection, or per-entry construction
            failures.
    """
    doc = _parse_yaml(yaml_text)
    structural_errors = _validate_root_shape(doc)
    if structural_errors:
        raise CatalogValidationError(structural_errors)

    entries_map: dict[str, Any] = doc["entries"]
    if not entries_map:
        raise CatalogValidationError(["bundle must declare at least one entry"])

    namespace: str = doc["namespace"]
    raw_user_id = doc["user_id"]
    # Legacy-bundle migration: pre-Story-18.1 bundles emitted ``user_id: null``
    # at the document root for community-tier exports. Rewrite to the literal
    # ``"anonymous"`` before any ``Entry`` is built so the tightened
    # :class:`Entry.user_id: NonEmptyStr` field accepts the value. The catalog
    # never writes ``user_id: null`` again — see ``dump_namespace``.
    user_id: str = raw_user_id if isinstance(raw_user_id, str) else "anonymous"
    header = _project_header(doc)
    entries: list[Entry] = []
    for entry_key, entry_map in entries_map.items():
        if "." in entry_key:
            # External entry — skip on import per AC10.
            continue
        entries.append(_build_entry(entry_key, entry_map, namespace, user_id))

    if not entries:
        raise CatalogValidationError(["bundle must declare at least one entry"])

    return entries, header


def _project_header(doc: dict[str, Any]) -> BundleHeader:
    """Project the optional header fields onto a :class:`BundleHeader`.

    Header fields: ``name`` / ``description`` / ``properties`` /
    ``shareable`` / ``public``.

    Story 17.7 added ``shareable`` to the projected fields; Story 18.2 adds
    ``public``. Defensive parsing: a missing key projects to ``False``; a
    non-bool value also projects to ``False`` (Pydantic strict-mode at the
    upsert site surfaces the typing contract for non-bool inputs, but the
    dataclass projection itself stays infallible to keep ``load_namespace``
    parse-only).

    ``present=True`` iff at least one of the five header fields
    (``name`` / ``description`` / ``properties`` / ``shareable`` /
    ``public``) is explicitly present in the document (regardless of value).
    This signal lets the import handler distinguish a pre-17.5 bundle (no
    header at all → skip meta upsert) from a 17.6+ bundle whose header
    carries empty strings (still upsert meta, possibly with the team-
    fallback values that the export path synthesised).
    """
    name_present = "name" in doc
    desc_present = "description" in doc
    props_present = "properties" in doc
    shareable_present = "shareable" in doc
    public_present = "public" in doc
    name_val = doc.get("name", "")
    desc_val = doc.get("description", "")
    props_val = doc.get("properties", {})
    shareable_val = doc.get("shareable", False)
    public_val = doc.get("public", False)
    if not isinstance(name_val, str):
        name_val = ""
    if not isinstance(desc_val, str):
        desc_val = ""
    if not isinstance(props_val, dict):
        props_val = {}
    # Strict-bool projection — only a real bool ``True`` projects to True.
    # Truthy strings / ints fall through to False, matching the catalog
    # gate's ``meta.payload.get("shareable") is True`` semantics.
    shareable_bool = shareable_val is True
    # Story 18.2 — strict-bool projection for ``public`` mirrors ``shareable``.
    # Pydantic's strict-mode on the upsert site (NamespaceMeta(public=...))
    # surfaces operator typos for explicit upserts; this projection is the
    # read-side defence in depth.
    public_bool = public_val is True
    # Coerce property values to strings if any leaked through; this is a
    # display projection so strict typing is enforced upstream by NamespaceMeta.
    properties: dict[str, str] = {
        str(k): str(v) for k, v in props_val.items() if isinstance(k, str)
    }
    return BundleHeader(
        name=name_val,
        description=desc_val,
        properties=properties,
        shareable=shareable_bool,
        public=public_bool,
        present=(
            name_present or desc_present or props_present or shareable_present or public_present
        ),
    )


def _parse_yaml(yaml_text: str) -> Any:
    """Return ``yaml.safe_load(yaml_text)`` or wrap failures as CatalogValidationError."""
    try:
        return yaml.safe_load(yaml_text)
    except yaml.YAMLError as exc:
        raise CatalogValidationError([f"Failed to parse bundle YAML: {exc}"]) from exc


def _validate_root_shape(doc: Any) -> list[str]:
    """Return a list of structural failures for the bundle root document.

    Accumulates every failure found; does not short-circuit on the first one.
    """
    errors: list[str] = []
    if not isinstance(doc, dict):
        return [f"bundle root must be a mapping, got {type(doc).__name__}"]

    if "namespace" not in doc:
        errors.append("bundle root missing required key 'namespace'")
    elif not isinstance(doc["namespace"], str) or not doc["namespace"]:
        errors.append("bundle 'namespace' must be a non-empty string")

    if "user_id" not in doc:
        errors.append("bundle root missing required key 'user_id'")
    else:
        user_id_val = doc["user_id"]
        # Story 18.1: ``null`` continues to pass structural validation
        # (legacy-bundle read path — ``load_namespace`` rewrites it to
        # ``"anonymous"`` before constructing any ``Entry``). Empty strings
        # are bugs, not legacy shapes — reject explicitly. The ``"must be a"``
        # substring is preserved for tests that already assert on it.
        if user_id_val is None:
            pass
        elif not isinstance(user_id_val, str):
            errors.append("bundle 'user_id' must be a non-empty string or null")
        elif user_id_val == "":
            errors.append("bundle 'user_id' must be a non-empty string or null")

    if "entries" not in doc:
        errors.append("bundle root missing required key 'entries'")
    elif isinstance(doc["entries"], list):
        # Story 17.6 — the rejected 17.5 wire shape (``entries:`` as a list)
        # is structurally unambiguous; raise an explicit error rather than
        # silently producing an empty bundle (per AC12 — "any unambiguous
        # behaviour is acceptable; silently producing wrong results is NOT").
        errors.append(
            "bundle 'entries' must be a mapping (the Story 17.5 list-of-items "
            "shape is rejected — re-export to the dict-keyed shape)"
        )
    elif not isinstance(doc["entries"], dict):
        errors.append(f"bundle 'entries' must be a mapping, got {type(doc['entries']).__name__}")
    return errors


def _build_entry(
    entry_id: str,
    entry_map: Any,
    namespace: str,
    user_id: str,
) -> Entry:
    """Build a single ``Entry`` from a per-entry YAML map.

    Missing required keys (``kind``, ``model_type``) surface through Pydantic
    validation; the caller wraps that into ``CatalogValidationError`` with the
    stable ``"entry '<id>' is invalid"`` substring.
    """
    if not isinstance(entry_map, dict):
        raise CatalogValidationError(
            [f"entry '{entry_id}' is invalid: expected a mapping, got {type(entry_map).__name__}"]
        )
    try:
        # Pydantic performs validation at construction; pass values through
        # ``model_validate`` so mypy stays out of the Literal / AllowlistedPath
        # type contract on ``Entry.kind`` / ``Entry.model_type``.
        return Entry.model_validate(
            {
                "id": entry_id,
                "namespace": namespace,
                "user_id": user_id,
                "kind": entry_map.get("kind"),
                "model_type": entry_map.get("model_type"),
                "description": entry_map.get("description", ""),
                "payload": entry_map.get("payload", {}),
            }
        )
    except ValidationError as exc:
        raise CatalogValidationError([f"entry '{entry_id}' is invalid: {exc}"]) from exc
