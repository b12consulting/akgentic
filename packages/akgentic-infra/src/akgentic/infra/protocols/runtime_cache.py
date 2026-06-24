"""RuntimeCache protocol — tier-agnostic team handle lookup abstraction."""

from __future__ import annotations

import uuid
from typing import Protocol, runtime_checkable

from akgentic.infra.protocols.team_handle import TeamHandle


@runtime_checkable
class RuntimeCache(Protocol):
    """Manages the mapping from team IDs to live TeamHandle instances.

    Provides a simple store/get/remove interface so that ``TeamService``
    can resolve a ``team_id`` to a usable ``TeamHandle`` without managing
    the cache structure directly.

    The protocol contract is the same across all tiers — only the
    implementation and transport differ.

    Tier topology:
        - **Community** (``LocalRuntimeCache``): single process — an in-memory dict
          holding live ``LocalTeamHandle`` objects directly. The server and worker
          are the same process, so one cache fills both roles.
        - **Department / Enterprise** (two roles, two caches):
            - *Worker* (``LocalRuntimeCache``): the real cache — an in-memory dict
              holding the live handles for teams placed on that worker.
            - *Server* (``HttpRuntimeCache`` department / ``RemoteRuntimeCache``
              enterprise): a **stateless no-op** — ``store``/``remove`` do nothing
              and ``get`` re-resolves the team's worker via the service registry
              (Redis department / Dapr enterprise), returning a fresh remote handle
              (``HttpTeamHandle`` / ``RemoteTeamHandle``). Holding no actor state
              keeps any server replica able to serve any request.

    Behavioral contract (applies to all implementations):
        - ``get()`` returns ``None`` for unknown team IDs (never raises).
        - ``remove()`` is idempotent — removing an absent ID is a no-op.
        - ``store()`` overwrites any existing entry for the same team ID.
    """

    def store(self, team_id: uuid.UUID, handle: TeamHandle) -> None:
        """Store a team handle in the cache.

        Args:
            team_id: ID of the team.
            handle: The TeamHandle instance to cache.
        """
        ...

    def get(self, team_id: uuid.UUID) -> TeamHandle | None:
        """Retrieve a team handle from the cache.

        Args:
            team_id: ID of the team to look up.

        Returns:
            The cached TeamHandle, or None if not found.
        """
        ...

    def remove(self, team_id: uuid.UUID) -> None:
        """Remove a team handle from the cache.

        Args:
            team_id: ID of the team to remove.
        """
        ...
