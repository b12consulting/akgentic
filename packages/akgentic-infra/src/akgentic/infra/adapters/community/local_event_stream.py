"""LocalEventStream — community-tier in-memory EventStream with per-reader signaling.

Provides a thread-safe, in-process event stream backed by a plain dict.
Designed for single-process community deployments where no external
infrastructure (Redis, Kafka) is available.

Threading model (CPython GIL assumption):
- One ``threading.Lock`` (``_lock``) on the ``LocalEventStream`` protects
  the ``_streams`` dict during ``append()``, ``subscribe()``, ``remove()``.
- One ``threading.Lock`` per ``_TeamStream`` protects its ``signals`` set
  and ``closed`` flag.
- ``read_next()`` is lock-free during replay: CPython's GIL guarantees
  atomic ``list.append()`` and ``len()``, so a reader whose cursor is
  behind the write frontier can safely index into ``events`` without
  holding any lock. The reader only blocks on its own ``threading.Event``
  when fully caught up.
- Safe for concurrent use from FastAPI thread-pool executors.
"""

from __future__ import annotations

import logging
import threading
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from akgentic.infra.protocols.event_stream import StreamClosed

if TYPE_CHECKING:
    from akgentic.core.messages import Message

logger = logging.getLogger(__name__)


@dataclass
class _TeamStream:
    """Per-team stream state: events, per-reader signals, lock, and closed flag."""

    events: list[Message] = field(default_factory=list)
    signals: set[threading.Event] = field(default_factory=set)
    lock: threading.Lock = field(default_factory=threading.Lock)
    closed: bool = False


class LocalStreamReader:
    """Cursor-based blocking reader for a single team's event stream.

    Each reader holds its own ``threading.Event`` for wake-up signaling
    and an absolute cursor into the parent ``_TeamStream.events`` list.

    The replay path (``cursor < len(events)``) is lock-free under CPython's
    GIL. The reader only blocks on ``_signal.wait()`` when fully caught up.
    """

    def __init__(
        self,
        team_stream: _TeamStream,
        signal: threading.Event,
        cursor: int,
    ) -> None:
        self._team_stream = team_stream
        self._signal = signal
        self._cursor = cursor
        self._closed = False

    def _advance(self) -> Message | None:
        """Advance the cursor and return the next event, or ``None`` if caught up."""
        if self._cursor < len(self._team_stream.events):
            event = self._team_stream.events[self._cursor]
            self._cursor += 1
            return event
        return None

    def _check_closed(self) -> None:
        """Raise ``StreamClosed`` if the reader or stream has been closed."""
        if self._closed or self._team_stream.closed:
            raise StreamClosed()

    def read_next(self, timeout: float = 0.5) -> Message | None:
        """Read the next event from the cursor position.

        Lock-free when replaying (``cursor < len(events)``). Blocks on
        the reader's own ``threading.Event`` when caught up.

        Args:
            timeout: Maximum seconds to block.

        Returns:
            The next event, or ``None`` on timeout.

        Raises:
            StreamClosed: If the stream has been removed.
        """
        self._check_closed()

        # Lock-free replay path
        event = self._advance()
        if event is not None:
            return event

        # Caught up — wait for signal
        self._signal.clear()

        # Re-check after clear to avoid lost-wakeup race
        self._check_closed()
        event = self._advance()
        if event is not None:
            return event

        self._signal.wait(timeout=timeout)

        # Post-wait checks
        self._check_closed()
        return self._advance()

    def close(self) -> None:
        """Release resources held by this reader. Idempotent."""
        if self._closed:
            return
        self._closed = True
        with self._team_stream.lock:
            self._team_stream.signals.discard(self._signal)


class LocalEventStream:
    """In-memory EventStream for the community tier.

    Satisfies the ``EventStream`` protocol (runtime-checkable). Backed by
    ``dict[UUID, _TeamStream]`` with a ``threading.Lock`` for thread safety.
    """

    def __init__(self) -> None:
        self._streams: dict[uuid.UUID, _TeamStream] = {}
        self._lock = threading.Lock()

    def append(self, team_id: uuid.UUID, event: Message) -> int:
        """Append an event to the team's stream.

        Creates the stream implicitly if it does not exist. Returns a
        monotonically increasing sequence number (per team).

        Args:
            team_id: ID of the team.
            event: The message to append.

        Returns:
            Monotonically increasing sequence number, or -1 if stream is closed.
        """
        with self._lock:
            ts = self._streams.get(team_id)
            if ts is None:
                ts = _TeamStream()
                self._streams[team_id] = ts

        with ts.lock:
            if ts.closed:
                logger.warning("append() on removed stream team_id=%s — discarding", team_id)
                return -1
            ts.events.append(event)
            seq = len(ts.events)
            for sig in ts.signals:
                sig.set()
            return seq

    def read_from(
        self, team_id: uuid.UUID, cursor: int = 0
    ) -> list[Message]:
        """Read all events from cursor position (non-blocking snapshot).

        Args:
            team_id: ID of the team.
            cursor: Starting position (0 = full history).

        Returns:
            List of events from cursor to current end.
        """
        with self._lock:
            ts = self._streams.get(team_id)
            if ts is None:
                return []

        if ts.closed:
            return []
        return list(ts.events[cursor:])

    def subscribe(
        self, team_id: uuid.UUID, cursor: int = 0
    ) -> LocalStreamReader:
        """Create a cursor-based blocking reader for the team's stream.

        Creates the stream implicitly if it does not exist.

        Args:
            team_id: ID of the team.
            cursor: Starting position (0 = replay full history then live).

        Returns:
            A LocalStreamReader that yields events from cursor position.
        """
        with self._lock:
            ts = self._streams.get(team_id)
            if ts is None:
                ts = _TeamStream()
                self._streams[team_id] = ts

        signal = threading.Event()
        with ts.lock:
            if ts.closed:
                raise StreamClosed()
            ts.signals.add(signal)
        return LocalStreamReader(ts, signal, cursor)

    def remove(self, team_id: uuid.UUID) -> None:
        """Remove the stream for a team.

        Sets the closed flag and signals all active readers, then
        deletes the stream data from the backing store.

        Args:
            team_id: ID of the team whose stream to remove.
        """
        with self._lock:
            ts = self._streams.pop(team_id, None)
            if ts is None:
                return

        with ts.lock:
            ts.closed = True
            for sig in ts.signals:
                sig.set()
