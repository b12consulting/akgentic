"""Shared JSONB predicate builders for the Nagra-backed Postgres repositories.

Each builder returns a ``(sql_fragment, params)`` tuple where:

* ``sql_fragment`` is a raw SQL condition string using ``%s`` as the psycopg
  placeholder marker — the same marker Nagra's Transaction uses when it hands
  a statement to psycopg under the hood. No user-supplied value is ever
  interpolated into the fragment text; every variable appears as ``%s`` and is
  passed through ``params``.
* ``params`` is the list of bound values in argument order.

The module exposes per-field helpers (``template_id_predicate``, ``tool_name_predicate``,
etc.) plus two compose helpers (``build_template_where`` / ``build_tool_where``)
that walk the non-None fields of a query model and AND-combine the per-field
predicates. Story 15.3 will extend the same module with agent / team builders.

Implements ADR-006 §4 predicate table for Template and Tool queries. See the
ToolEntry nested-path note: because ``ToolEntry.model_dump()`` places ``name``
and ``description`` inside a nested ``tool`` object, the JSONB path uses
``data->'tool'->>'name'`` (not the top-level ``data->>'name'`` shown in the
ADR table).
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from akgentic.catalog.models.queries import (
        AgentQuery,
        TeamQuery,
        TemplateQuery,
        ToolQuery,
    )

__all__ = [
    "agent_description_predicate",
    "agent_id_predicate",
    "agent_role_predicate",
    "agent_skills_predicate",
    "build_agent_where",
    "build_team_where",
    "build_template_where",
    "build_tool_where",
    "decode_jsonb_column",
    "team_description_predicate",
    "team_id_predicate",
    "team_name_predicate",
    "template_id_predicate",
    "template_placeholder_predicate",
    "tool_description_predicate",
    "tool_id_predicate",
    "tool_name_predicate",
    "tool_tool_class_predicate",
]


# --- ILIKE metacharacter escaping ---


def _escape_ilike(value: str) -> str:
    """Escape ``%``, ``_``, and ``\\`` in a string for literal ILIKE matching.

    PG's ILIKE treats ``%`` and ``_`` as wildcards and ``\\`` as its default
    escape character. For behavioural parity with the Mongo backend — which
    uses ``re.escape`` so user-supplied special characters match literally —
    escape these three characters before wrapping the value in ``%...%``.
    """
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


# --- JSONB column decoding (shared across repos) ---


def decode_jsonb_column(raw: object) -> dict[str, object]:
    """Normalise a JSONB column value to a Python dict.

    psycopg 3 decodes JSONB columns to native Python objects by default, but
    some driver configurations (or the plain ``JSON`` column type) return a
    JSON string. Handle both so hydration is robust regardless of the adapter
    wiring. Shared by ``template_repo`` and ``tool_repo`` (and by story 15.3's
    agent / team repos) to keep the JSONB↔dict contract in one place.
    """
    if isinstance(raw, str):
        return cast("dict[str, object]", json.loads(raw))
    return cast("dict[str, object]", raw)


# --- Per-field predicate builders (Template) ---


def template_id_predicate(value: str) -> tuple[str, list[object]]:
    """Build ``data->>'id' = %s`` predicate for an exact-id match."""
    return "data->>'id' = %s", [value]


def template_placeholder_predicate(value: str) -> tuple[str, list[object]]:
    """Build JSONB key-existence predicate on the ``placeholders`` array.

    Uses the Postgres ``?`` operator (does the right-hand text exist as a
    top-level key in the left-hand JSONB?). Against a JSON array of strings,
    this returns true when the value equals one of the array's string
    elements — matching ``placeholder in entry.placeholders`` semantics of the
    YAML and Mongo backends.

    The ``?`` operator is only defined for ``jsonb`` (not plain ``json``);
    the ``data`` column is declared as ``JSON`` in ``schema.toml`` (story
    15.1 invariant), so the value is cast to ``jsonb`` at query time.
    """
    return "(data->'placeholders')::jsonb ? %s", [value]


# --- Per-field predicate builders (Tool) ---


def tool_id_predicate(value: str) -> tuple[str, list[object]]:
    """Build ``data->>'id' = %s`` predicate for an exact-id match."""
    return "data->>'id' = %s", [value]


def tool_tool_class_predicate(value: str) -> tuple[str, list[object]]:
    """Build ``data->>'tool_class' = %s`` predicate for an exact class-path match."""
    return "data->>'tool_class' = %s", [value]


def tool_name_predicate(value: str) -> tuple[str, list[object]]:
    """Build a case-insensitive substring predicate on the tool's display name.

    The JSONB path is ``data->'tool'->>'name'`` because ``ToolEntry.model_dump()``
    nests ``name`` under a ``tool`` object — matching the Mongo backend's
    ``tool.name`` filter. The parameter is wrapped with ``%`` characters so
    the ILIKE operator performs substring matching. User-supplied
    metacharacters (``%``, ``_``, ``\\``) are escaped via :func:`_escape_ilike`
    so they match literally — parity with the Mongo backend's ``re.escape``.
    """
    return (
        "data->'tool'->>'name' ILIKE %s ESCAPE '\\'",
        [f"%{_escape_ilike(value)}%"],
    )


def tool_description_predicate(value: str) -> tuple[str, list[object]]:
    """Build a case-insensitive substring predicate on the tool's description.

    Uses the same nested JSONB path convention as :func:`tool_name_predicate`
    and the same literal-metacharacter escaping via :func:`_escape_ilike`.
    """
    return (
        "data->'tool'->>'description' ILIKE %s ESCAPE '\\'",
        [f"%{_escape_ilike(value)}%"],
    )


# --- Compose helpers ---


def _combine(
    fragments: list[tuple[str, list[object]]],
) -> tuple[str | None, list[object]]:
    """AND-combine a list of ``(fragment, params)`` pairs.

    Returns ``(None, [])`` when the input list is empty so callers can fall
    back to a bare SELECT without an empty WHERE clause.
    """
    if not fragments:
        return None, []
    clauses = [frag for frag, _ in fragments]
    params: list[object] = []
    for _, param_list in fragments:
        params.extend(param_list)
    where_sql = " AND ".join(clauses)
    return where_sql, params


def build_template_where(query: TemplateQuery) -> tuple[str | None, list[object]]:
    """Build the WHERE-clause fragment for a :class:`TemplateQuery`.

    Returns ``(None, [])`` when no fields are set (callers issue a bare SELECT).
    Otherwise returns ``(sql_fragment, params)`` with all fields AND-combined.
    """
    fragments: list[tuple[str, list[object]]] = []
    if query.id is not None:
        fragments.append(template_id_predicate(query.id))
    if query.placeholder is not None:
        fragments.append(template_placeholder_predicate(query.placeholder))
    return _combine(fragments)


# --- Per-field predicate builders (Agent) ---


def agent_id_predicate(value: str) -> tuple[str, list[object]]:
    """Build ``data->>'id' = %s`` predicate for an exact agent id match."""
    return "data->>'id' = %s", [value]


def agent_role_predicate(value: str) -> tuple[str, list[object]]:
    """Build ``data->'card'->'config'->>'role' = %s`` predicate for exact role match.

    Per ADR-007, ``AgentCard.role`` is a non-serialized ``@property`` derived
    from ``config.role`` — the serialized JSONB has no top-level ``card.role``
    key, so the predicate traverses one level deeper into ``card.config.role``
    to match the authoritative registry key.
    """
    return "data->'card'->'config'->>'role' = %s", [value]


def agent_skills_predicate(value: list[str]) -> tuple[str, list[object]]:
    """Build a JSONB ANY-match predicate over ``card.skills`` using ``?|``.

    PG's ``?|`` operator takes a left JSONB value and a right ``text[]`` and
    returns true when any element of the array exists as a top-level string
    element of the JSONB array. psycopg 3 adapts Python ``list[str]`` to
    ``text[]``; the explicit ``%s::text[]`` cast makes the binding robust
    regardless of how Nagra's ``Transaction`` surfaces the parameter shape.

    The ``?|`` operator is only defined for ``jsonb``; the ``data`` column is
    declared as ``JSON`` in ``schema.toml`` (story 15.1 invariant), so the
    traversed path is cast to ``jsonb`` at query time (same pattern as
    :func:`template_placeholder_predicate`).

    Semantic parity: matches the Mongo backend's ``{"card.skills": {"$in": query.skills}}``
    — a team matches when ANY of the queried skills is in the agent's skill list.
    """
    return "(data->'card'->'skills')::jsonb ?| %s::text[]", [value]


def agent_description_predicate(value: str) -> tuple[str, list[object]]:
    """Build a case-insensitive substring predicate on the agent's description.

    Uses ``data->'card'->>'description' ILIKE %s`` — the description lives
    under the nested ``card`` object (matching the Mongo backend's
    ``card.description`` filter). Escapes ``%``, ``_``, ``\\`` in the value so
    user-supplied wildcards match literally (parity with Mongo's
    ``re.escape``). See :func:`_escape_ilike`.
    """
    return (
        "data->'card'->>'description' ILIKE %s ESCAPE '\\'",
        [f"%{_escape_ilike(value)}%"],
    )


# --- Per-field predicate builders (Team) ---


def team_id_predicate(value: str) -> tuple[str, list[object]]:
    """Build ``data->>'id' = %s`` predicate for an exact team id match."""
    return "data->>'id' = %s", [value]


def team_name_predicate(value: str) -> tuple[str, list[object]]:
    """Build a case-insensitive substring predicate on the team's name.

    Uses ``data->>'name' ILIKE %s ESCAPE '\\'``. Escapes ``%``, ``_``, ``\\``
    in the value so user-supplied wildcards match literally (parity with the
    Mongo backend which uses ``re.escape(query.name)``). See
    :func:`_escape_ilike`.
    """
    return (
        "data->>'name' ILIKE %s ESCAPE '\\'",
        [f"%{_escape_ilike(value)}%"],
    )


def team_description_predicate(value: str) -> tuple[str, list[object]]:
    """Build a case-insensitive substring predicate on the team's description.

    Same convention as :func:`team_name_predicate` — metacharacters escaped
    for literal-substring parity with the Mongo backend.
    """
    return (
        "data->>'description' ILIKE %s ESCAPE '\\'",
        [f"%{_escape_ilike(value)}%"],
    )


def build_agent_where(query: AgentQuery) -> tuple[str | None, list[object]]:
    """Build the WHERE-clause fragment for a :class:`AgentQuery`.

    Returns ``(None, [])`` when no fields are set (callers issue a bare SELECT).
    Otherwise returns ``(sql_fragment, params)`` with all fields AND-combined.
    All parameters are bound — the ``skills`` list is passed as a single
    parameter so psycopg adapts it to the PG ``text[]`` type expected by
    ``?|``.
    """
    fragments: list[tuple[str, list[object]]] = []
    if query.id is not None:
        fragments.append(agent_id_predicate(query.id))
    if query.role is not None:
        fragments.append(agent_role_predicate(query.role))
    if query.skills is not None:
        fragments.append(agent_skills_predicate(query.skills))
    if query.description is not None:
        fragments.append(agent_description_predicate(query.description))
    return _combine(fragments)


def build_team_where(query: TeamQuery) -> tuple[str | None, list[object]]:
    """Build the WHERE-clause fragment for a :class:`TeamQuery`.

    Walks ``id``, ``name``, and ``description`` only — ``agent_id`` is
    deliberately NOT handled here. It is applied as a Python post-filter by
    :class:`NagraTeamCatalogRepository` via
    :func:`akgentic.catalog.models.team.agent_in_members` so the recursive
    member tree is walked (matching the Mongo backend's behaviour). See
    story 15.3 Dev Notes §"The ``agent_id`` predicate — ADR vs. Mongo reality".

    Returns ``(None, [])`` when no server-side fields are set (callers issue
    a bare SELECT).
    """
    fragments: list[tuple[str, list[object]]] = []
    if query.id is not None:
        fragments.append(team_id_predicate(query.id))
    if query.name is not None:
        fragments.append(team_name_predicate(query.name))
    if query.description is not None:
        fragments.append(team_description_predicate(query.description))
    return _combine(fragments)


def build_tool_where(query: ToolQuery) -> tuple[str | None, list[object]]:
    """Build the WHERE-clause fragment for a :class:`ToolQuery`.

    Returns ``(None, [])`` when no fields are set (callers issue a bare SELECT).
    Otherwise returns ``(sql_fragment, params)`` with all fields AND-combined.
    """
    fragments: list[tuple[str, list[object]]] = []
    if query.id is not None:
        fragments.append(tool_id_predicate(query.id))
    if query.tool_class is not None:
        fragments.append(tool_tool_class_predicate(query.tool_class))
    if query.name is not None:
        fragments.append(tool_name_predicate(query.name))
    if query.description is not None:
        fragments.append(tool_description_predicate(query.description))
    return _combine(fragments)
