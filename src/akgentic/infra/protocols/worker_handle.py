"""WorkerHandle protocol — tier-agnostic worker lifecycle operations."""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from akgentic.infra.protocols.team_handle import TeamHandle
    from akgentic.team.models import Process


@runtime_checkable
class WorkerHandle(Protocol):
    """Tier-agnostic handle for worker-level team lifecycle operations.

    Abstracts stop/delete/resume/get operations so that ``TeamService``
    can manage team lifecycle without knowing the underlying tier implementation.

    Implementations: LocalWorkerHandle (community),
    RemoteWorkerHandle (department / enterprise).

    Error contract:
        - ``stop_team()`` raises ``ValueError`` if the team is not running
          or has been deleted.
        - ``delete_team()`` raises ``ValueError`` if the team has already
          been deleted.
        - ``resume_team()`` raises ``ValueError`` if the team is not in a
          stopped state (e.g. already running or deleted).
        - ``get_team()`` returns ``None`` for unknown team IDs (never raises).
    """

    def stop_team(self, team_id: uuid.UUID) -> None:
        """Stop a running team.

        Args:
            team_id: ID of the team to stop.
        """
        ...

    def delete_team(self, team_id: uuid.UUID) -> None:
        """Delete a team and its resources.

        Args:
            team_id: ID of the team to delete.
        """
        ...

    def resume_team(self, team_id: uuid.UUID) -> TeamHandle:
        """Resume a stopped team.

        Args:
            team_id: ID of the team to resume.

        Returns:
            A TeamHandle for interacting with the resumed team.
        """
        ...

    def get_team(self, team_id: uuid.UUID) -> Process | None:
        """Get team metadata.

        Args:
            team_id: ID of the team to look up.

        Returns:
            The Process metadata, or None if not found.
        """
        ...

    def stop_all(self) -> None:
        """Stop all teams on this worker node during graceful shutdown.

        Called by the lifespan handler to drain every team before the process
        exits.  Implementations iterate all running teams and call
        ``stop_team()`` for each, logging and skipping individual failures so
        that one broken team cannot block shutdown of the rest.

        Remote / stub handles implement this as a no-op — only the local
        handle that owns the ``TeamManager`` performs real work.

        Error contract (ADR-013):
            Individual ``stop_team()`` failures are logged and skipped.
            ``ActorSystem.shutdown()`` is called after all teams are processed
            as an orphan sweep for leaked actors.
        """
        ...
