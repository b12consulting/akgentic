"""Query helpers for the Nagra-backed Postgres event store.

This module currently exposes a single helper, :func:`decode_jsonb_column`,
used by :class:`~akgentic.team.repositories.postgres.event_store.NagraEventStore`
to normalise JSONB column values to Python dicts before Pydantic hydration.

The helper is duplicated locally rather than imported from
``akgentic.catalog.repositories.postgres._queries`` because ``akgentic-catalog``
sits ABOVE ``akgentic-team`` in the dependency graph (see CLAUDE.md
"Dependency graph"). A cross-layer import would be a module-boundary
violation. The helper is four lines; duplicating it keeps team's dependency
surface clean.

Implements ADR-15 §3 (payload-authoritative JSONB hydration).
"""

from __future__ import annotations

import json
from typing import cast

__all__ = [
    "decode_jsonb_column",
]


def decode_jsonb_column(raw: object) -> dict[str, object]:
    """Normalise a JSONB column value to a Python dict.

    psycopg 3 decodes JSONB columns to native Python objects by default, but
    some driver configurations (or the plain ``JSON`` column type used by the
    team schema) return a JSON string. Handle both so hydration is robust
    regardless of the adapter wiring.

    Mirrors the catalog helper at
    ``akgentic.catalog.repositories.postgres._queries.decode_jsonb_column``.
    Kept local to avoid a cross-submodule private-module import — see this
    module's docstring for the rationale.
    """
    if isinstance(raw, str):
        return cast("dict[str, object]", json.loads(raw))
    return cast("dict[str, object]", raw)
