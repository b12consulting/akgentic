"""Null channel registry — no-op implementation for when channels are disabled."""

from __future__ import annotations

import uuid


class NullChannelRegistry:
    """No-op channel registry that always returns no match.

    Used when ``channel_registry_path`` is unset — channels are effectively
    disabled but the ``ChannelRegistry`` protocol contract is satisfied.
    """

    async def register(self, channel: str, channel_user_id: str, team_id: uuid.UUID) -> None:
        """No-op — registration is not supported when channels are disabled."""

    async def find_team(self, channel: str, channel_user_id: str) -> uuid.UUID | None:
        """Always returns None — no channel mappings exist."""
        return None

    async def deregister(self, channel: str, channel_user_id: str) -> None:
        """No-op — deregistration is not supported when channels are disabled."""
