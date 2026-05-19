"""AC32: unit tests for ``_build_where`` / ``_escape_ilike``.

Pure-function tests — no database, no testcontainer. Verify the
translator emits the expected WHERE fragment + bound parameter list for
every AC19–AC21 branch, including the unsatisfiable
``user_id + user_id_set=False`` case and the ILIKE escape ordering.

These tests run in every install matrix regardless of the ``[postgres]``
extra — they import the translator helpers directly from
``akgentic.catalog.repositories.postgres.repository``, which is importable
without ``nagra`` / ``psycopg`` (verified by the no-extra import guard in
``test_module_import_no_extra.py``).
"""

from __future__ import annotations

from akgentic.catalog.models.queries import EntryQuery
from akgentic.catalog.repositories.postgres.repository import (
    _build_where,
    _escape_ilike,
)


def _normalise(fragment: str) -> str:
    """Collapse internal whitespace so assertions don't depend on spacing."""
    return " ".join(fragment.split())


# --- Exact-match fields (AC19) ---


def test_empty_query_yields_empty_fragment() -> None:
    """AC19 empty-query short-circuit: no fields set → empty WHERE fragment."""
    where, params = _build_where(EntryQuery())
    assert where == ""
    assert params == []


def test_namespace_only() -> None:
    """AC19: namespace → ``namespace = %s``."""
    where, params = _build_where(EntryQuery(namespace="ns-1"))
    assert _normalise(where) == "namespace = %s"
    assert params == ["ns-1"]


def test_kind_only() -> None:
    """AC19: kind → ``kind = %s``. Also AC19: namespace omitted when None."""
    where, params = _build_where(EntryQuery(kind="agent"))
    assert _normalise(where) == "kind = %s"
    assert params == ["agent"]


def test_id_only() -> None:
    """AC19: id → ``id = %s``."""
    where, params = _build_where(EntryQuery(id="entry-1"))
    assert _normalise(where) == "id = %s"
    assert params == ["entry-1"]


def test_all_exact_match_fields_and_joined() -> None:
    """AC19 (AND-semantics): every set exact-match field AND-joined."""
    where, params = _build_where(
        EntryQuery(
            namespace="ns-1",
            kind="tool",
            id="t1",
        ),
    )
    assert _normalise(where) == "namespace = %s AND kind = %s AND id = %s"
    assert params == ["ns-1", "tool", "t1"]


# --- user_id / user_id_set six-case matrix (AC21) ---


def test_user_id_only_sets_equality() -> None:
    """AC21: user_id only → user_id = %s."""
    where, params = _build_where(EntryQuery(user_id="alice"))
    assert _normalise(where) == "user_id = %s"
    assert params == ["alice"]


def test_user_id_set_true_only() -> None:
    """Story 18.1: user_id_set=True only → ``user_id != %s`` bound to "anonymous"."""
    where, params = _build_where(EntryQuery(user_id_set=True))
    assert _normalise(where) == "user_id != %s"
    assert params == ["anonymous"]


def test_user_id_set_false_only() -> None:
    """Story 18.1: user_id_set=False only → ``user_id = %s`` bound to "anonymous"."""
    where, params = _build_where(EntryQuery(user_id_set=False))
    assert _normalise(where) == "user_id = %s"
    assert params == ["anonymous"]


def test_user_id_and_user_id_set_true() -> None:
    """AC21: user_id + user_id_set=True → equality (value guarantees user_id != "anonymous")."""
    where, params = _build_where(EntryQuery(user_id="alice", user_id_set=True))
    assert _normalise(where) == "user_id = %s"
    assert params == ["alice"]


def test_user_id_and_user_id_set_false_is_unsatisfiable() -> None:
    """AC21: user_id + user_id_set=False → unsatisfiable ``1 = 0`` clause."""
    where, params = _build_where(EntryQuery(user_id="alice", user_id_set=False))
    assert _normalise(where) == "1 = 0"
    assert params == []


def test_user_id_none_and_user_id_set_none_emits_no_user_id_clause() -> None:
    """AC21: both unset → no user_id clause (verified via empty query case)."""
    # Combine with a namespace filter so the fragment is non-empty and we
    # can assert the user_id clause was not appended alongside it.
    where, params = _build_where(EntryQuery(namespace="ns-1"))
    assert "user_id" not in where
    assert params == ["ns-1"]


# --- description_contains ILIKE escape (AC20) ---


def test_description_contains_plain_substring() -> None:
    """AC20: plain substring with no metacharacters → ILIKE ``%substring%``."""
    where, params = _build_where(EntryQuery(description_contains="hello"))
    assert _normalise(where) == "description ILIKE %s"
    assert params == ["%hello%"]


def test_description_contains_escapes_percent() -> None:
    """AC20: ``%`` in input is escaped to ``\\%`` before wildcard wrap."""
    where, params = _build_where(EntryQuery(description_contains="50% off"))
    assert _normalise(where) == "description ILIKE %s"
    assert params == ["%50\\% off%"]


def test_description_contains_escapes_underscore() -> None:
    """AC20: ``_`` in input is escaped to ``\\_`` before wildcard wrap."""
    where, params = _build_where(EntryQuery(description_contains="a_b"))
    assert _normalise(where) == "description ILIKE %s"
    assert params == ["%a\\_b%"]


def test_description_contains_escapes_backslash_first() -> None:
    """AC20 order: backslash escaped FIRST so ``%``/``_`` escapes stay single."""
    where, params = _build_where(EntryQuery(description_contains="a\\b"))
    assert _normalise(where) == "description ILIKE %s"
    # `\` → `\\`; the wildcard wrap adds a raw `%` on each side.
    assert params == ["%a\\\\b%"]


def test_description_contains_all_three_metacharacters_combined() -> None:
    """AC20 composite: `\\`, `%`, and `_` combined — escape order stays stable."""
    where, params = _build_where(EntryQuery(description_contains="50% off _ \\n"))
    assert _normalise(where) == "description ILIKE %s"
    # Order matters — backslash first:
    #   "50% off _ \n"
    # → (\ → \\) "50% off _ \\n"
    # → (% → \%) "50\% off _ \\n"
    # → (_ → \_) "50\% off \_ \\n"
    # Wrapped: "%50\% off \_ \\n%"
    assert params == ["%50\\% off \\_ \\\\n%"]


def test_escape_ilike_empty_string_is_empty() -> None:
    """AC20: empty input → empty escaped form; caller still wraps with %…%."""
    assert _escape_ilike("") == ""


def test_escape_ilike_no_metacharacters_is_identity() -> None:
    """AC20: input without metacharacters passes through unchanged."""
    assert _escape_ilike("hello world") == "hello world"


# --- Combined clauses (AC19 + AC20 + AC21 at once) ---


def test_combined_clauses_and_joined_in_declared_order() -> None:
    """AC18 + AC19 + AC20 + AC21: clause ordering is stable and deterministic.

    Stability matters for test reproducibility — a diff that reorders the
    exact-match fields, the user_id clause, and the description clause
    would surface here.
    """
    where, params = _build_where(
        EntryQuery(
            namespace="ns-1",
            kind="agent",
            user_id="alice",
            description_contains="foo",
        ),
    )
    assert _normalise(where) == (
        "namespace = %s AND kind = %s AND user_id = %s AND description ILIKE %s"
    )
    assert params == ["ns-1", "agent", "alice", "%foo%"]


# --- No-WHERE-when-only-nones case (AC19 short-circuit) ---


def test_only_none_fields_yields_empty_fragment() -> None:
    """AC19 empty-query short-circuit: None everywhere → empty fragment, empty params.

    Constructing every field as ``None`` is equivalent to the bare
    ``EntryQuery()`` — both routes emit the bare ``SELECT`` on the caller
    side.
    """
    query = EntryQuery(
        namespace=None,
        kind=None,
        id=None,
        user_id=None,
        user_id_set=None,
        description_contains=None,
    )
    where, params = _build_where(query)
    assert where == ""
    assert params == []
