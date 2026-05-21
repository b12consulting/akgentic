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

Worker-thread teardown (see ADR-025 §5, revised 2026-05-20):

    The background worker thread is constructed with ``daemon=True`` and is
    reclaimed by the Python runtime at process exit; no explicit drain hook is
    plumbed through ``WorkerServices``. The previous ``close()`` API and the
    ``_SHUTDOWN`` sentinel that fed it have been removed — no tier called them.
    Tests that need a deterministic flush use ``_flush()``.
"""

from __future__ import annotations

import logging
import queue
import threading
import uuid
from typing import TYPE_CHECKING

import logfire

from akgentic.core.orchestrator import EventSubscriber

if TYPE_CHECKING:
    from akgentic.core.messages import Message

logger = logging.getLogger(__name__)


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
    backend can never stall the orchestrator's actor thread. The daemon
    worker is reclaimed by the Python runtime at process exit; no explicit
    drain is plumbed through ``WorkerServices`` (ADR-025 §5).
    """

    def __init__(self) -> None:
        self._restoring: set[uuid.UUID] = set()
        self._restoring_lock = threading.Lock()
        self._queue: queue.Queue[object] = queue.Queue()
        self._worker = threading.Thread(
            target=self._run,
            name="telemetry-subscriber",
            daemon=True,
        )
        self._worker.start()
        logger.debug("TelemetrySubscriber initialized (async worker)")

    def set_restoring(self, team_id: uuid.UUID, restoring: bool) -> None:
        """Toggle restore-mode suppression for a single team.

        The restoring set is per-team — only messages whose ``team_id`` is in
        the set are suppressed by ``on_message``. Mutation is guarded by
        ``self._restoring_lock`` because multiple orchestrator threads may
        toggle the flag concurrently (one orchestrator per team).

        Args:
            team_id: ``team_id`` from the orchestrator triggering the notification.
            restoring: ``True`` to suppress emission for ``team_id``, ``False``
                to resume normal emission for it. Calling with ``restoring=False``
                when ``team_id`` is not in the set is a silent no-op
                (``set.discard`` semantics).
        """
        with self._restoring_lock:
            if restoring:
                self._restoring.add(team_id)
            else:
                self._restoring.discard(team_id)

    def on_message(self, msg: Message) -> None:
        """Enqueue a lightweight telemetry record for background emission.

        Non-blocking: reads a few plain attributes off the message on the
        caller's thread and hands them to the worker. The actor thread is
        never held on logfire I/O. Messages whose ``team_id`` is currently
        flagged restoring are dropped before they reach the queue, so the
        restore-replay window of one team cannot interfere with the live
        telemetry of another.

        Args:
            msg: Orchestrator telemetry message
        """
        with self._restoring_lock:
            if msg.team_id in self._restoring:
                return

        sender = msg.sender.name if msg.sender else "unknown"
        msg_type = msg.__class__.__name__
        team_id = msg.team_id
        self._queue.put((sender, msg_type, team_id))

    def on_stop_request(self, team_id: uuid.UUID) -> None:  # noqa: ARG002
        """No-op — stop handling is bridged by ``TimerStopSubscriber`` in ``akgentic-team``.

        The orchestrator's inactivity-timer handler calls this on every subscriber;
        this shared telemetry subscriber has no per-team teardown to perform on that
        signal. The daemon worker thread lives for the process lifetime.

        Args:
            team_id: ``team_id`` from the orchestrator triggering the notification.
                Accepted to satisfy the ``EventSubscriber`` Protocol but currently
                ignored — per-team handling is deferred.
        """

    def on_stop(self, team_id: uuid.UUID) -> None:
        """Per-team stop notification — no action on this shared subscriber.

        The daemon worker is shared across every team that publishes through
        this subscriber, so tearing it down on a single team's stop would
        starve the rest. The worker thread is ``daemon=True`` and is reclaimed
        by the Python runtime at process exit; no explicit drain is performed
        on ``on_stop`` (ADR-025 §5).

        Args:
            team_id: ``team_id`` from the orchestrator triggering the notification.
                Logged at DEBUG; no other action is taken.
        """
        logger.debug("TelemetrySubscriber: on_stop for team_id=%s", team_id)

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
