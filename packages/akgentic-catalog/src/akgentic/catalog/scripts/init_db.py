"""Runnable init-container entry point for Postgres schema creation.

Invocation: ``python -m akgentic.catalog.scripts.init_db`` — intended for
Kubernetes ``initContainer`` / Nomad ``prestart`` deployment patterns where
schema creation runs once per environment before the catalog service
starts.

Environment-variable contract:

* ``DB_CONN_STRING_PERSISTENCE`` — the libpq-style Postgres DSN. Mirrors
  the v1 operator UX (structural continuity for existing deployments).

Exit-code convention (pinned by Story 22.2 ACs 14–16):

* ``0`` — schema applied successfully (``init_db`` returned normally).
* ``1`` — any arbitrary failure raised by
  :class:`PostgresCatalogConfig` validation or :func:`init_db` itself
  (connection refused, unreachable host, malformed DSN past validation,
  ``ImportError`` when the ``[postgres]`` extra is absent, etc.). A
  traceback is logged at ERROR for diagnosis.
* ``2`` — ``DB_CONN_STRING_PERSISTENCE`` is missing or empty. No
  traceback; a single stderr diagnostic is emitted.

The module's top-level imports are ``nagra``-free and ``psycopg``-free —
:class:`PostgresCatalogConfig` is importable without the ``[postgres]``
extra (Story 22.1 AC6 guarantees this), and :func:`init_db` defers its
``nagra`` import to the function body. A missing ``[postgres]`` extra
therefore surfaces only at ``init_db(config)`` call time and is caught
by the broad ``except Exception`` below.

Implements ADR-011 §"``init_db`` is a separate concern" — navigation-only
reference.
"""

from __future__ import annotations

import logging
import os
import sys
import traceback

from akgentic.catalog.repositories.postgres.config import PostgresCatalogConfig
from akgentic.catalog.repositories.postgres.init_db import init_db

__all__ = ["main"]

logger = logging.getLogger(__name__)

_ENV_VAR = "DB_CONN_STRING_PERSISTENCE"


def main() -> None:
    """Read the DSN, call :func:`init_db`, exit with the documented code.

    Reads ``os.environ.get(DB_CONN_STRING_PERSISTENCE)``. Missing / empty
    values produce a terse stderr diagnostic and exit ``2``. Successful
    schema application logs at INFO and exits ``0``. Any other exception
    is caught, logged at ERROR with traceback, and produces exit ``1``.
    """
    dsn = os.environ.get(_ENV_VAR)
    if not dsn:
        # Empty-string DSN is equivalent to missing — the config validator
        # would reject it downstream anyway, and a missing env var is the
        # more informative diagnostic.
        sys.stderr.write(f"{_ENV_VAR} environment variable is required\n")
        sys.exit(2)

    try:
        config = PostgresCatalogConfig(connection_string=dsn)
        init_db(config)
    except Exception:
        # Broad except is load-bearing here — the goal is to map ANY
        # failure (validation error, connection refused, driver import
        # error) to exit code 1 with a traceback for operators. We
        # route the traceback through both the logger (for structured
        # log pipelines) and sys.stderr (for operators tailing stderr
        # in init-container logs — the test surface relies on the
        # stderr channel).
        logger.exception("init_db failed")
        traceback.print_exc()
        sys.exit(1)

    logger.info("catalog_entries schema initialized")
    sys.exit(0)


if __name__ == "__main__":
    main()
