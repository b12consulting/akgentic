"""TelemetrySubscriber — shared event subscriber that traces messages via logfire.

Actor-thread contract (see ADR-018):

    ``on_message`` runs on the orchestrator's actor thread and MUST NOT perform
    blocking I/O. Subscribers that need to emit to remote systems must queue
    off-thread, following the pattern in ``TelemetrySubscriber``.

Violating this contract stalls the orchestrator's message loop, which in turn
stalls every downstream consumer (WebSocket replay, live streaming, future
subscribers). ADR-018 Decision 1 (shipped here) makes ``TelemetrySubscriber``
non-blocking internally via a daemon worker thread. ADR-018 Decision 2 (the
orchestrator-level dispatch fix) is deferred to a follow-up epic in
``akgentic-core``.
"""

from __future__ import annotations

import logging
import queue
import threading
from typing import TYPE_CHECKING

import logfire

from akgentic.core.orchestrator import EventSubscriber

if TYPE_CHECKING:
    from akgentic.core.messages import Message

logger = logging.getLogger(__name__)

# Sentinel used to signal the background worker to exit cleanly.
_SHUTDOWN = object()


class _FlushBarrier:
    """Barrier sentinel pushed onto the queue by ``_flush()``.

    Carries a ``threading.Event`` that the worker sets when it drains the
    barrier, giving tests a deterministic join point. Test-only: not part
    of the public ``EventSubscriber`` contract.
    """

    __slots__ = ("event",)

    def __init__(self) -> None:
        self.event = threading.Event()


class TelemetrySubscriber(EventSubscriber):
    """Traces orchestrator events via logfire on a background thread.

    Implements the EventSubscriber protocol from akgentic.core.orchestrator.
    Thread-safe — designed as a shared, long-lived subscriber across all teams.

    ``on_message()`` extracts the small set of attributes it needs on the
    caller's thread, enqueues them, and returns immediately. Actual emission
    runs on a single daemon worker, so a slow or misconfigured logfire
    backend can never stall the orchestrator's actor thread.
    """

    def __init__(self) -> None:
        self._restoring = False
        self._queue: queue.Queue[object] = queue.Queue()
        self._worker = threading.Thread(
            target=self._run,
            name="telemetry-subscriber",
            daemon=True,
        )
        self._worker.start()
        logger.debug("TelemetrySubscriber initialized (async worker)")

    def set_restoring(self, restoring: bool) -> None:
        """Toggle restore mode to suppress span emission during event replay."""
        self._restoring = restoring

    def on_message(self, msg: Message) -> None:
        """Enqueue a lightweight telemetry record for background emission.

        Non-blocking: reads a few plain attributes off the message on the
        caller's thread and hands them to the worker. The actor thread is
        never held on logfire I/O.

        Args:
            msg: Orchestrator telemetry message
        """
        if self._restoring:
            return

        sender = msg.sender.name if msg.sender else "unknown"
        msg_type = msg.__class__.__name__
        team_id = msg.team_id
        self._queue.put((sender, msg_type, team_id))

    def on_stop_request(self) -> None:
        """No-op — stop handling is bridged by ``TimerStopSubscriber`` in ``akgentic-team``.

        The orchestrator's inactivity-timer handler calls this on every subscriber;
        this shared telemetry subscriber has no per-team teardown to perform on that
        signal (worker drain happens in ``on_stop()`` on actual shutdown).
        """

    def on_stop(self) -> None:
        """Signal the worker to drain and exit.

        Bounded join keeps server shutdown snappy even if logfire is wedged.
        """
        self._queue.put(_SHUTDOWN)
        self._worker.join(timeout=5.0)
        logger.debug("TelemetrySubscriber stopped")

    def _flush(self, timeout: float = 5.0) -> bool:
        """Block until every item enqueued so far has been drained by the worker.

        Pushes a barrier sentinel onto the queue and waits for the worker to
        set its event. FIFO queue ordering guarantees every item enqueued
        before the call has been processed by the time the barrier fires.

        Test-only: not part of the public ``EventSubscriber`` contract.

        Args:
            timeout: Maximum seconds to wait for the worker to drain.

        Returns:
            ``True`` if the barrier was reached within ``timeout``,
            ``False`` otherwise.
        """
        barrier = _FlushBarrier()
        self._queue.put(barrier)
        return barrier.event.wait(timeout)

    def _run(self) -> None:
        """Background loop: drain the queue, emit to logfire, never raise."""
        while True:
            item = self._queue.get()
            if item is _SHUTDOWN:
                return
            if isinstance(item, _FlushBarrier):
                item.event.set()
                continue
            assert isinstance(item, tuple)
            sender: str = item[0]
            msg_type: str = item[1]
            team_id: str = item[2]
            try:
                logfire.info(
                    "{sender} event: {msg_type} - {team_id}",
                    sender=sender,
                    msg_type=msg_type,
                    team_id=team_id,
                )
                logger.debug("Telemetry event: %s", msg_type)
            except Exception:  # noqa: BLE001
                logger.exception("TelemetrySubscriber: logfire.info failed")
