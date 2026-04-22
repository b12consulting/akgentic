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

    def set_restoring(self, restoring: bool) -> None:
        """Toggle restore mode to suppress delivery during event replay."""
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

    def on_stop(self) -> None:
        """Clean up all registered adapters when the team stops."""
        logger.debug("ChannelDispatcher stopped: team_id=%s", self._team_id)
        for adapter in self._adapters:
            adapter.on_stop(self._team_id)
