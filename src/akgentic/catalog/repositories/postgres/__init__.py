"""Public API surface for Postgres-backed catalog repositories.

Gates all Nagra imports behind an availability check. When ``nagra`` is not
installed, importing this package raises ``ImportError`` with installation
instructions. When ``nagra`` IS available, exposes the schema loader,
deployment-time ``init_db`` hook, and the four stub repository classes that
later stories (15.2 / 15.3) will flesh out.

Implements ADR-006 Nagra-based PostgreSQL repository §5 (lazy import gate) and
§6 (schema loader + init_db reference implementation).
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
        "Install with: pip install akgentic-catalog[postgres]"
    ) from exc

# Only reached when nagra is importable.
from nagra import Schema  # noqa: E402

_SCHEMA_LOADED = False


def _ensure_schema_loaded() -> None:
    """Load ``schema.toml`` into ``Schema.default`` exactly once.

    Idempotent — subsequent calls are no-ops. Repository constructors call
    this at instantiation so individual repos are always safe to build, but
    no repository may call :func:`init_db` implicitly.
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
    NEVER called implicitly by repository constructors.

    Args:
        conn_string: Nagra-compatible Postgres connection string.
    """
    from nagra import Transaction

    _ensure_schema_loaded()
    with Transaction(conn_string):
        Schema.default.create_tables()


# Import stub repositories last so they can rely on _ensure_schema_loaded above.
from akgentic.catalog.repositories.postgres.agent_repo import (  # noqa: E402, I001
    NagraAgentCatalogRepository,
)
from akgentic.catalog.repositories.postgres.team_repo import (  # noqa: E402
    NagraTeamCatalogRepository,
)
from akgentic.catalog.repositories.postgres.template_repo import (  # noqa: E402
    NagraTemplateCatalogRepository,
)
from akgentic.catalog.repositories.postgres.tool_repo import (  # noqa: E402
    NagraToolCatalogRepository,
)

__all__ = [
    "NagraAgentCatalogRepository",
    "NagraTeamCatalogRepository",
    "NagraTemplateCatalogRepository",
    "NagraToolCatalogRepository",
    "_ensure_schema_loaded",
    "init_db",
]
