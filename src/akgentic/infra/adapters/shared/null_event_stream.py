"""Null EventStream — no-op stub satisfying the EventStream protocol."""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from akgentic.core.messages import Message


class NullStreamReader:
    """No-op StreamReader — always returns None and never raises."""

    def read_next(self, timeout: float = 0.5) -> Message | None:
        """Return None immediately (no events available)."""
        return None

    def close(self) -> None:
        """No-op — nothing to release."""


class NullEventStream:
    """No-op EventStream stub for use until a real implementation is wired.

    All writes are discarded, reads return empty, subscribe returns a
    NullStreamReader. Satisfies the EventStream protocol contract.
    """

    def append(self, team_id: uuid.UUID, event: Message) -> int:
        """Discard event, return 0."""
        return 0

    def read_from(self, team_id: uuid.UUID, cursor: int = 0) -> list[Message]:
        """Return empty list."""
        return []

    def subscribe(self, team_id: uuid.UUID, cursor: int = 0) -> NullStreamReader:
        """Return a NullStreamReader."""
        return NullStreamReader()

    def remove(self, team_id: uuid.UUID) -> None:
        """No-op."""
