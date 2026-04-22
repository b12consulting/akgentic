"""EventStreamSubscriber -- shared subscriber that routes orchestrator events to EventStream.

Implements the ``EventSubscriber`` protocol from ``akgentic.core.orchestrator``.
A single instance is shared across all teams; ``team_id`` is extracted from each
``Message`` to route events to the correct per-team stream.

Threading model:
- A ``threading.Lock`` protects ``_seen_teams`` for safe concurrent access from
  multiple orchestrator actor threads.
"""

from __future__ import annotations

import logging
import threading
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

    On stop, removes streams for all teams that were seen via ``on_message()``.
    """

    def __init__(self, event_stream: EventStream) -> None:
        self._event_stream = event_stream
        self._seen_teams: set[uuid.UUID] = set()
        self._lock = threading.Lock()
        logger.debug("EventStreamSubscriber initialized")

    def set_restoring(self, restoring: bool) -> None:  # noqa: FBT001, ARG002
        """No-op — EventStream must be repopulated during restore replay."""

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

        with self._lock:
            self._seen_teams.add(team_id)

        self._event_stream.append(team_id, msg)

    def on_stop_request(self) -> None:
        """No-op — stop handling is bridged by ``TimerStopSubscriber`` in ``akgentic-team``.

        The orchestrator's inactivity-timer handler calls this on every subscriber;
        this shared subscriber has no per-team teardown to perform on that signal
        (the per-team stream is removed in ``on_stop()`` once the team actually stops).
        """

    def on_stop(self) -> None:
        """Remove streams for all tracked teams (best-effort cleanup)."""
        with self._lock:
            teams = set(self._seen_teams)

        for team_id in teams:
            try:
                self._event_stream.remove(team_id)
            except Exception:
                logger.debug(
                    "EventStreamSubscriber: remove() failed for team_id=%s",
                    team_id,
                )

        logger.debug("EventStreamSubscriber stopped, cleaned up %d team streams", len(teams))
