"""InteractionChannelDispatcher — per-team outbound message dispatcher."""

from __future__ import annotations

import logging
import uuid
from typing import TYPE_CHECKING

from akgentic.core.messages import SentMessage

if TYPE_CHECKING:
    from akgentic.core.messages import Message
    from akgentic.infra.protocols.channels import InteractionChannelAdapter

logger = logging.getLogger(__name__)


class InteractionChannelDispatcher:
    """Routes outbound SentMessage events to all matching channel adapters.

    Satisfies the EventSubscriber protocol from akgentic.core.orchestrator
    via structural subtyping. Each team gets its own dispatcher instance
    with its own ordered adapter list.

    Multi-channel dispatch: iterates adapters, calls matches() on each,
    and delivers to ALL matching adapters. If no adapter matches, the
    message is silently skipped (web channel handles it via WebSocket).
    """

    def __init__(self, team_id: uuid.UUID, adapters: list[InteractionChannelAdapter]) -> None:
        self._adapters = list(adapters)
        self._team_id = team_id
        self._restoring = False

    def set_restoring(self, team_id: uuid.UUID, restoring: bool) -> None:
        """Toggle restore mode to suppress delivery during event replay.

        Args:
            team_id: ``team_id`` from the orchestrator. Must equal
                ``self._team_id`` — the dispatcher is per-team, so a mismatch
                indicates a wiring bug and fails loud.
            restoring: ``True`` while restore replay is in progress, ``False``
                to resume dispatch.
        """
        assert team_id == self._team_id
        self._restoring = restoring

    def on_message(self, msg: Message) -> None:
        """Dispatch a SentMessage to all matching adapters.

        Skips delivery entirely during restore mode. Ignores non-SentMessage
        events. Silently skips if no adapter matches — unmatched messages are
        handled by the web channel (WebSocket subscribers). Absence of an
        adapter match is not an error.

        Args:
            msg: Orchestrator event message.
        """
        if self._restoring:
            return
        if not isinstance(msg, SentMessage):
            return
        logger.debug(
            "Dispatching SentMessage to %d adapter(s): team_id=%s",
            len(self._adapters),
            self._team_id,
        )
        for adapter in self._adapters:
            if adapter.matches(msg):
                adapter.deliver(msg)

    def on_stop_request(self, team_id: uuid.UUID) -> None:  # noqa: ARG002
        """No-op — dispatcher has no work to do on the inactivity-timer signal.

        Channel-side teardown happens on ``on_stop()`` once the team actually
        stops. Present to satisfy the ``EventSubscriber`` Protocol.

        Args:
            team_id: ``team_id`` from the orchestrator. Accepted to satisfy the
                Protocol but ignored — no work is performed here.
        """

    def on_stop(self, team_id: uuid.UUID) -> None:
        """Clean up all registered adapters when the team stops.

        Args:
            team_id: ``team_id`` from the orchestrator. Must equal
                ``self._team_id`` — the dispatcher is per-team, so a mismatch
                indicates a wiring bug and fails loud.
        """
        assert team_id == self._team_id
        logger.debug("ChannelDispatcher stopped: team_id=%s", self._team_id)
        for adapter in self._adapters:
            adapter.on_stop(self._team_id)
