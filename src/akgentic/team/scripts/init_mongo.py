"""Init-container entry point that creates the team Mongo indexes.

Runs :func:`akgentic.team.repositories.mongo.ensure_indexes` against the
MongoDB database identified by the ``MONGO_URI`` and ``MONGO_DB``
environment variables, provisioning ``teams_user_id_idx`` and
``teams_status_idx`` on the teams collection.

Intended to be invoked by a Kubernetes initContainer or Nomad prestart
task before the main team-runtime process starts:

    command: ["python", "-m", "akgentic.team.scripts.init_mongo"]

This is the supported provisioning path for a deployment that set
``MONGO_TEAM_AUTO_INDEX=0``: a teams collection too large to absorb a
foreground index build at boot is indexed here, on the operator's own
schedule, instead of inside ``MongoEventStore.__init__``.

The underlying :func:`ensure_indexes` is idempotent and guarded per
index, so re-running against an already-indexed database is safe.

Exit codes:
    0 — success (indexes created or already present)
    2 — ``MONGO_URI`` or ``MONGO_DB`` not set
    1 — any other failure (pymongo not installed, connection refused, etc.)
"""

from __future__ import annotations

import logging
import os
import sys

_URI_ENV = "MONGO_URI"
_DB_ENV = "MONGO_DB"

logger = logging.getLogger(__name__)


def main() -> int:
    """Create the team collection indexes against the configured MongoDB.

    Returns:
        Process exit code — 0 on success, 2 on missing env, 1 on other error.
    """
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
    )

    uri = os.environ.get(_URI_ENV)
    if not uri:
        logger.error("%s is not set; cannot initialize team indexes", _URI_ENV)
        return 2
    db_name = os.environ.get(_DB_ENV)
    if not db_name:
        logger.error("%s is not set; cannot initialize team indexes", _DB_ENV)
        return 2

    try:
        import pymongo

        from akgentic.team.repositories.mongo import ensure_indexes
    except ImportError as exc:
        logger.error("Mongo backend unavailable: %s", exc)
        return 1

    try:
        # MongoClient is generic in its document type, which mypy cannot infer
        # here and which is irrelevant to index creation. Closing the client is
        # what the context manager buys: this process is short-lived.
        with pymongo.MongoClient(uri) as client:  # type: ignore[var-annotated]
            # Reach the server before provisioning. ensure_indexes swallows
            # every PyMongoError by design — a rejected spec must never stop a
            # process from starting — so an unreachable, refused or
            # authentication-rejected server would otherwise leave this script
            # logging success and exiting 0 having created nothing. The ping is
            # what keeps the exit codes above honest, and this is the only
            # provisioning path a deployment that set MONGO_TEAM_AUTO_INDEX=0
            # has left.
            client.admin.command("ping")
            ensure_indexes(client[db_name])
    except Exception:
        logger.exception("Mongo index initialization failed")
        return 1

    logger.info("Team Mongo indexes initialized successfully")
    return 0


if __name__ == "__main__":
    sys.exit(main())
