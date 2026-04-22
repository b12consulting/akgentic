"""LocalIngestion — community-tier InteractionChannelIngestion implementation."""

from __future__ import annotations

import logging
import uuid
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from akgentic.infra.server.services.team_service import TeamService

logger = logging.getLogger(__name__)


class LocalIngestion:
    """Routes inbound channel messages directly to TeamManager in-process.

    Community-tier implementation of InteractionChannelIngestion.
    Delegates all operations to TeamService, which already encapsulates
    catalog resolution, TeamManager lifecycle, and runtime caching.

    Supports deferred wiring: ``team_service`` can be ``None`` at construction
    time and set later via the property, allowing ``wire_community`` to build
    the ingestion instance before ``TeamService`` exists.

    Deferred wiring pattern:
        ``team_service`` is ``None`` at construction because
        ``wire_community()`` must return ``CommunityServices`` before
        ``TeamService`` exists (TeamService requires CommunityServices).
        The setter is called later by ``_wire_ingestion`` in ``app.py``.
        Enterprise implementations won't use this pattern — their ingestion
        adapters communicate over the network and are fully wired at
        construction time.
    """

    def __init__(self, team_service: TeamService | None = None) -> None:
        self._team_service = team_service

    @property
    def team_service(self) -> TeamService | None:
        """Return the wired TeamService, or None if not yet wired."""
        return self._team_service

    @team_service.setter
    def team_service(self, value: TeamService) -> None:
        """Set the TeamService for deferred wiring."""
        self._team_service = value

    def _require_team_service(self) -> TeamService:
        """Return the wired TeamService or raise if not yet wired."""
        if self._team_service is None:
            msg = "LocalIngestion.team_service has not been wired yet"
            raise RuntimeError(msg)
        return self._team_service

    async def route_reply(
        self,
        team_id: uuid.UUID,
        content: str,
        original_message_id: str | None = None,
    ) -> None:
        """Route an inbound reply to an existing team.

        Args:
            team_id: Target team ID.
            content: Message content from the human.
            original_message_id: Optional ID of the message being replied to.
        """
        logger.info("Inbound reply: team_id=%s", team_id)
        self._require_team_service().send_message(team_id, content)

    async def initiate_team(
        self,
        content: str,
        channel_user_id: str,
        catalog_entry_id: str,
    ) -> uuid.UUID:
        """Create a new team and send the initial message.

        Args:
            content: Initial message content.
            channel_user_id: Channel-specific user identifier.
            catalog_entry_id: Catalog entry to use for team creation.

        Returns:
            The newly created team's ID.
        """
        logger.info("Inbound initiation: catalog=%s", catalog_entry_id)
        logger.debug("Initiation user: %s", channel_user_id)
        ts = self._require_team_service()
        process = ts.create_team(catalog_entry_id, user_id=channel_user_id)
        ts.send_message(process.team_id, content)
        logger.debug("Team initiated: team_id=%s", process.team_id)
        return process.team_id
