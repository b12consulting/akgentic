"""LocalRuntimeCache — community-tier in-process RuntimeCache implementation."""

from __future__ import annotations

import logging
import uuid
from typing import TYPE_CHECKING

from akgentic.infra.protocols.team_handle import TeamHandle

if TYPE_CHECKING:
    from akgentic.infra.adapters.community.local_worker_handle import LocalWorkerHandle
    from akgentic.team.repositories.yaml import YamlEventStore

logger = logging.getLogger(__name__)


class LocalRuntimeCache:
    """In-process dict-backed cache mapping team IDs to live TeamHandle instances.

    Starts empty on construction — teams are only cached after explicit
    ``store()`` calls (NFR2: ghost team prevention). The cache must never
    be pre-populated from disk: if it were, stopped teams would appear as
    ghost handles (stale ``TeamHandle`` proxies that point to dead actors).
    Teams are added only after successful creation or resumption.

    Call ``warm()`` after construction to auto-restore teams that were
    running before a server restart (community tier only).
    """

    def __init__(self) -> None:
        self._handles: dict[uuid.UUID, TeamHandle] = {}

    def store(self, team_id: uuid.UUID, handle: TeamHandle) -> None:
        """Store a team handle in the cache."""
        logger.debug("Cache store: team_id=%s", team_id)
        self._handles[team_id] = handle

    def get(self, team_id: uuid.UUID) -> TeamHandle | None:
        """Retrieve a team handle, or None if not found."""
        return self._handles.get(team_id)

    def remove(self, team_id: uuid.UUID) -> None:
        """Remove a team handle from the cache (no-op if absent)."""
        logger.debug("Cache remove: team_id=%s", team_id)
        self._handles.pop(team_id, None)

    def warm(
        self,
        worker_handle: LocalWorkerHandle,
        event_store: YamlEventStore,
    ) -> None:
        """Auto-restore teams that were running before a server restart.

        Community tier only: runtimes are in-process and lost on restart.
        For each team marked RUNNING in the event store, stop it first
        (RUNNING → STOPPED) then resume (STOPPED → RUNNING) to create
        live actors and store the handle in the cache.

        Failures are logged and skipped — one broken team must not block
        server startup.
        """
        from akgentic.team.models import TeamStatus

        all_teams = event_store.list_teams()
        running = [t for t in all_teams if t.status == TeamStatus.RUNNING]
        if not running:
            return
        logger.info("Warming cache: restoring %d running team(s)", len(running))
        for process in running:
            try:
                worker_handle.stop_team(process.team_id)
                handle = worker_handle.resume_team(process.team_id)
                self.store(process.team_id, handle)
                logger.info("Restored team: %s", process.team_id)
            except Exception:  # noqa: BLE001
                logger.warning(
                    "Failed to restore team %s, skipping",
                    process.team_id,
                    exc_info=True,
                )
