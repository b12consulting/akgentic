"""EventStreamSubscriber -- shared subscriber that routes orchestrator events to EventStream.

Implements the ``EventSubscriber`` protocol from ``akgentic.core.orchestrator``.
A single instance is shared across all teams; ``team_id`` is extracted from each
``Message`` to route events to the correct per-team stream.
"""

from __future__ import annotations

import logging
import uuid
from typing import TYPE_CHECKING

from akgentic.core.orchestrator import EventSubscriber

if TYPE_CHECKING:
    from akgentic.core.messages import Message
    from akgentic.infra.protocols.event_stream import EventStream

logger = logging.getLogger(__name__)


class EventStreamSubscriber(EventSubscriber):
    """Routes orchestrator events into the shared EventStream.

    Forwards each ``Message`` directly to the injected ``EventStream``.

    On ``on_stop(team_id)``, removes the per-team stream for that team only —
    the canonical community-tier per-team cleanup hook, mirroring
    ``RedisStreamSubscriber.on_stop`` in the department tier and
    ``DaprStreamSubscriber.on_stop`` in enterprise.
    """

    def __init__(self, event_stream: EventStream) -> None:
        self._event_stream = event_stream
        logger.debug("EventStreamSubscriber initialized")

    def set_restoring(self, team_id: uuid.UUID, restoring: bool) -> None:  # noqa: FBT001, ARG002
        """No-op — community ``LocalEventStream`` is repopulated by restore replay.

        Suppressing emission here would create the gap the design intentionally
        avoids: the in-memory ``LocalEventStream`` has no persistence, so the
        cursor-based replay path needs every restored event to land in the
        stream. Department and enterprise stream subscribers (Redis / Dapr)
        override this to suppress while restoring; the community-tier
        ``LocalEventStream`` does not.

        Args:
            team_id: ``team_id`` from the orchestrator. Ignored.
            restoring: ``True`` while restore replay is in progress, ``False``
                otherwise. Ignored.
        """

    def on_message(self, msg: Message) -> None:
        """Forward message directly to the event stream.

        Messages with ``team_id=None`` are silently skipped (logged at DEBUG).

        Args:
            msg: Orchestrator message.
        """
        team_id = msg.team_id
        if team_id is None:
            logger.debug("EventStreamSubscriber: skipping message with team_id=None")
            return

        self._event_stream.append(team_id, msg)

    def on_stop_request(self, team_id: uuid.UUID) -> None:  # noqa: ARG002
        """No-op — stop handling is bridged by ``TimerStopSubscriber`` in ``akgentic-team``.

        The orchestrator's inactivity-timer handler calls this on every subscriber;
        this shared subscriber has no per-team teardown to perform on that signal
        (the per-team stream is removed in ``on_stop()`` once the team actually stops).

        Args:
            team_id: ``team_id`` from the orchestrator. Accepted to satisfy the
                ``EventSubscriber`` Protocol but currently ignored — per-team
                stop handling is deferred to ``TimerStopSubscriber``.
        """

    def on_stop(self, team_id: uuid.UUID) -> None:
        """Remove the per-team stream for the stopping team.

        Canonical community-tier per-team cleanup hook. Any error raised by
        ``event_stream.remove`` (e.g. stream already removed by
        ``TeamService.stop_team`` as a belt-and-suspenders) is swallowed and
        logged at DEBUG — ``on_stop`` must never propagate an exception back
        to the orchestrator.

        Args:
            team_id: ``team_id`` of the stopping team.
        """
        try:
            self._event_stream.remove(team_id)
        except Exception:  # noqa: BLE001
            logger.debug(
                "EventStreamSubscriber: remove() failed for team_id=%s",
                team_id,
            )
        logger.debug("EventStreamSubscriber stopped, team_id=%s", team_id)
