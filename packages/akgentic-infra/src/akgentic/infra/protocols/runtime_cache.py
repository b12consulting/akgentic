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
        - **Community** (``LocalRuntimeCache``): In-memory dict within a
          single process. Server and workers share the same process, so the
          cache holds live ``LocalTeamHandle`` objects directly.
        - **Department / Enterprise** (two-layer): The server holds a
          ``RemoteCache`` that maps team IDs to ``RemoteTeamHandle`` instances.
          Each worker holds its own ``LocalRuntimeCache`` with the actual live
          handles. The ``RemoteCache`` on the server delegates to the worker's
          ``LocalRuntimeCache`` over the network.
          - Department: Redis-backed transport.
          - Enterprise: Dapr state management building block.

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
