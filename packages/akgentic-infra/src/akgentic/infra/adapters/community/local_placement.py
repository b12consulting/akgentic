"""LocalPlacement — community-tier placement that creates teams in the current process."""

from __future__ import annotations

import logging
import uuid
from typing import TYPE_CHECKING

from akgentic.infra.adapters.community.local_team_handle import LocalTeamHandle
from akgentic.team.manager import TeamManager
from akgentic.team.ports import ServiceRegistry

if TYPE_CHECKING:
    from akgentic.infra.protocols.team_handle import TeamHandle
    from akgentic.team.models import TeamCard


logger = logging.getLogger(__name__)


class LocalPlacement:
    """Creates teams in the current process instance.

    Satisfies the PlacementStrategy protocol via structural subtyping.
    Delegates team creation to ``TeamManager`` and wraps the result
    in a ``LocalTeamHandle``.
    """

    def __init__(
        self,
        team_manager: TeamManager,
        service_registry: ServiceRegistry,
    ) -> None:
        self._instance_id = uuid.uuid4()
        self._team_manager = team_manager
        self._service_registry = service_registry

    @property
    def instance_id(self) -> uuid.UUID:
        """The worker instance ID representing this process."""
        return self._instance_id

    def create_team(self, team_card: TeamCard, user_id: str) -> TeamHandle:
        """Create a team in the local process and return a handle.

        Args:
            team_card: Team configuration card.
            user_id: ID of the user creating the team.

        Returns:
            A LocalTeamHandle for interacting with the newly created team.
        """
        logger.debug("LocalPlacement creating team: user_id=%s", user_id)
        runtime = self._team_manager.create_team(team_card, user_id)
        logger.debug("Team created locally: team_id=%s", runtime.id)
        return LocalTeamHandle(runtime)
