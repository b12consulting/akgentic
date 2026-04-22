"""Init-container entry point that creates missing team event-store tables.

Runs :func:`akgentic.team.repositories.postgres.init_db` against the
Postgres instance identified by the ``DB_CONN_STRING_PERSISTENCE``
environment variable. The variable name is identical to the catalog
init-container script (``akgentic.catalog.scripts.init_db``) so operators
can drive both modules from one ``.env``.

Intended to be invoked by a Kubernetes initContainer or Nomad prestart
task before the main team-runtime process starts:

    command: ["python", "-m", "akgentic.team.scripts.init_db"]

The underlying :func:`init_db` is idempotent, so re-running against an
already-initialized database is safe — it creates only missing tables.

Exit codes:
    0 — success (tables created or already present)
    2 — ``DB_CONN_STRING_PERSISTENCE`` not set
    1 — any other failure (nagra not installed, connection refused, etc.)
"""

from __future__ import annotations

import logging
import os
import sys

_ENV_VAR = "DB_CONN_STRING_PERSISTENCE"

logger = logging.getLogger(__name__)


def main() -> int:
    """Create team event-store tables against the configured Postgres instance.

    Returns:
        Process exit code — 0 on success, 2 on missing env, 1 on other error.
    """
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
    )

    conn_string = os.environ.get(_ENV_VAR)
    if not conn_string:
        logger.error("%s is not set; cannot initialize team database", _ENV_VAR)
        return 2

    try:
        from akgentic.team.repositories.postgres import init_db
    except ImportError as exc:
        logger.error("Postgres backend unavailable: %s", exc)
        return 1

    try:
        init_db(conn_string)
    except Exception:
        logger.exception("init_db failed")
        return 1

    logger.info("Team database initialized successfully")
    return 0


if __name__ == "__main__":
    sys.exit(main())
