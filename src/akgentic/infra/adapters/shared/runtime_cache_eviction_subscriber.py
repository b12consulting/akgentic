"""RuntimeCacheEvictionSubscriber -- shared subscriber that evicts stopped teams.

Implements the ``EventSubscriber`` protocol from ``akgentic.core.orchestrator``.
A single instance is shared across all teams on a worker. On ``on_stop(team_id)``
it removes the stopping team's handle from the worker's ``runtime_cache`` so the
team graph is released on EVERY stop path (HTTP stop/delete routes AND the
inactivity-timer auto-stop), not just the routes. ``remove`` is idempotent, so the
route-level eviction in ``worker/routes/teams.py`` stays as belt-and-suspenders.
"""

from __future__ import annotations

import logging
import uuid
from typing import TYPE_CHECKING

from akgentic.core.orchestrator import EventSubscriber

if TYPE_CHECKING:
    from akgentic.core.messages import Message
    from akgentic.infra.protocols.runtime_cache import RuntimeCache

logger = logging.getLogger(__name__)


class RuntimeCacheEvictionSubscriber(EventSubscriber):
    """Evicts a stopped team's handle from the shared RuntimeCache.

    On ``on_stop(team_id)``, removes the team's handle from the injected
    ``RuntimeCache`` — the path-independent per-team cleanup hook that releases
    the team graph on both the HTTP stop/delete routes and the inactivity-timer
    auto-stop. ``RuntimeCache.remove`` is idempotent, so the route-level eviction
    remains as belt-and-suspenders (double-evict is harmless).
    """

    def __init__(self, runtime_cache: RuntimeCache) -> None:
        self._runtime_cache = runtime_cache
        logger.debug("RuntimeCacheEvictionSubscriber initialized")

    def set_restoring(self, team_id: uuid.UUID, restoring: bool) -> None:  # noqa: FBT001, ARG002
        """No-op — restore replay does not touch the runtime cache.

        Args:
            team_id: ``team_id`` from the orchestrator. Ignored.
            restoring: ``True`` while restore replay is in progress, ``False``
                otherwise. Ignored.
        """

    def on_stop_request(self, team_id: uuid.UUID) -> None:  # noqa: ARG002
        """No-op — stop handling is bridged by ``TimerStopSubscriber`` in ``akgentic-team``.

        Eviction happens in ``on_stop()`` once the team actually stops, not on
        the stop-request signal.

        Args:
            team_id: ``team_id`` from the orchestrator. Ignored.
        """

    def on_stop(self, team_id: uuid.UUID) -> None:
        """Remove the stopping team's handle from the runtime cache.

        Any error raised by ``runtime_cache.remove`` (e.g. handle already
        removed by the route-level eviction as a belt-and-suspenders) is
        swallowed and logged at DEBUG — ``on_stop`` must never propagate an
        exception back to the orchestrator.

        Args:
            team_id: ``team_id`` of the stopping team.
        """
        try:
            self._runtime_cache.remove(team_id)
        except Exception:  # noqa: BLE001
            logger.debug(
                "RuntimeCacheEvictionSubscriber: remove() failed for team_id=%s",
                team_id,
            )
        logger.debug("RuntimeCacheEvictionSubscriber stopped, team_id=%s", team_id)

    def on_message(self, msg: Message) -> None:  # noqa: ARG002
        """No-op — the runtime cache is not message-driven.

        Args:
            msg: Orchestrator message. Ignored.
        """
