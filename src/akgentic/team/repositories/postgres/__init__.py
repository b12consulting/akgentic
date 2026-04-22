"""Public API surface for the Nagra-backed Postgres event store.

Gates all Nagra imports behind an availability check. When ``nagra`` is not
installed, importing this package raises ``ImportError`` with installation
instructions. When ``nagra`` IS available, exposes the schema loader, the
deployment-time ``init_db`` hook, and the ``NagraEventStore`` class (the
nine EventStore Protocol methods land in story 17.2).

Implements ADR-15 Nagra-based PostgreSQL EventStore §5 (lazy import gate)
and §6 (schema loader + ``init_db`` reference implementation). Mirrors the
sibling catalog package at
``akgentic.catalog.repositories.postgres`` so operators learn one pattern.
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

try:
    import nagra  # type: ignore[import-untyped]  # noqa: F401 — availability probe
except ImportError as exc:
    logger.warning("nagra is not installed; Postgres backend is unavailable")
    raise ImportError(
        "nagra is required for the Postgres backend. "
        "Install with: pip install akgentic-team[postgres]"
    ) from exc

# Only reached when nagra is importable.
from nagra import Schema  # noqa: E402

_SCHEMA_LOADED = False


def _ensure_schema_loaded() -> None:
    """Load ``schema.toml`` into ``Schema.default`` exactly once.

    Idempotent — subsequent calls are no-ops. ``NagraEventStore.__init__``
    calls this at instantiation so instances are always safe to construct,
    but the constructor MUST NOT call :func:`init_db` implicitly. Operators
    are responsible for invoking :func:`init_db` once per deployment via the
    ``akgentic.team.scripts.init_db`` init-container script.
    """
    global _SCHEMA_LOADED
    if _SCHEMA_LOADED:
        return
    schema_path = Path(__file__).parent / "schema.toml"
    Schema.default.load_toml(schema_path)
    _SCHEMA_LOADED = True


def init_db(conn_string: str) -> None:
    """Create missing tables against the target Postgres instance.

    Idempotent: calling twice against the same database succeeds both times
    and does not create duplicate tables. Intended as a deployment hook —
    NEVER called implicitly by ``NagraEventStore.__init__``.

    Args:
        conn_string: Nagra-compatible Postgres connection string.
    """
    from nagra import Transaction

    _ensure_schema_loaded()
    with Transaction(conn_string):
        Schema.default.create_tables()


# Import the event store last so it can rely on _ensure_schema_loaded above.
from akgentic.team.repositories.postgres.event_store import (  # noqa: E402, I001
    NagraEventStore,
)

__all__ = [
    "NagraEventStore",
    "_ensure_schema_loaded",
    "init_db",
]
