"""EventStream and StreamReader protocols — tier-agnostic event streaming abstraction."""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from akgentic.core.messages import Message


class StreamClosed(Exception):  # noqa: N818 — ADR-010 specifies this name
    """Raised by StreamReader.read_next() when the stream has been removed."""


class StreamReader(Protocol):
    """Cursor-based blocking reader for a team's event stream."""

    def read_next(self, timeout: float = 0.5) -> Message | None:
        """Read next event from cursor position.

        Args:
            timeout: Maximum seconds to block waiting for an event.

        Returns:
            The next event, or None if timeout elapsed.

        Raises:
            StreamClosed: If the stream has been removed.
        """
        ...

    def close(self) -> None:
        """Release resources held by this reader."""
        ...


@runtime_checkable
class EventStream(Protocol):
    """Tier-agnostic event stream with replay and fan-out.

    Replaces per-WebSocket subscriber queues with a shared stream
    per team that supports cursor-based reads from any offset.
    """

    def append(self, team_id: uuid.UUID, event: Message) -> int:
        """Append event to the team's stream.

        Args:
            team_id: ID of the team.
            event: The message to append.

        Returns:
            Monotonically increasing sequence number.
        """
        ...

    def read_from(self, team_id: uuid.UUID, cursor: int = 0) -> list[Message]:
        """Read all events from cursor position (non-blocking snapshot).

        Args:
            team_id: ID of the team.
            cursor: Starting position (0 = full history).

        Returns:
            List of events from cursor to current end.
        """
        ...

    def subscribe(self, team_id: uuid.UUID, cursor: int = 0) -> StreamReader:
        """Create a cursor-based blocking reader.

        Args:
            team_id: ID of the team.
            cursor: Starting position (0 = replay full history then live).

        Returns:
            A StreamReader that yields events from cursor position.
        """
        ...

    def remove(self, team_id: uuid.UUID) -> None:
        """Remove the stream for a team.

        Called on team stop/delete. Active readers receive StreamClosed.

        Args:
            team_id: ID of the team whose stream to remove.
        """
        ...
