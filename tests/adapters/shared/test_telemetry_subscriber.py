"""Tests for TelemetrySubscriber adapter."""

from __future__ import annotations

import inspect
import time
from unittest.mock import MagicMock, patch

from akgentic.infra.adapters.shared.telemetry_subscriber import TelemetrySubscriber


class TestTelemetrySubscriberProtocolCompliance:
    """AC5: TelemetrySubscriber implements EventSubscriber protocol."""

    def test_has_on_message_method(self) -> None:
        """TelemetrySubscriber exposes on_message."""
        subscriber = TelemetrySubscriber()
        assert callable(subscriber.on_message)

    def test_has_on_stop_method(self) -> None:
        """TelemetrySubscriber exposes on_stop."""
        subscriber = TelemetrySubscriber()
        assert callable(subscriber.on_stop)

    def test_has_on_stop_request_method(self) -> None:
        """Story 22.1 AC2: subscriber exposes on_stop_request for timer-driven shutdown."""
        subscriber = TelemetrySubscriber()
        assert callable(subscriber.on_stop_request)
        subscriber.on_stop()

    def test_on_message_signature_matches_protocol(self) -> None:
        """on_message has msg parameter matching EventSubscriber."""
        sig = inspect.signature(TelemetrySubscriber.on_message)
        assert "msg" in sig.parameters

    def test_on_stop_signature_matches_protocol(self) -> None:
        """on_stop has no parameters beyond self."""
        sig = inspect.signature(TelemetrySubscriber.on_stop)
        params = [p for p in sig.parameters if p != "self"]
        assert len(params) == 0

    def test_on_stop_request_signature_matches_protocol(self) -> None:
        """Story 22.1 AC2: on_stop_request takes no parameters beyond self."""
        sig = inspect.signature(TelemetrySubscriber.on_stop_request)
        params = [p for p in sig.parameters if p != "self"]
        assert len(params) == 0


class TestTelemetrySubscriberBehavior:
    """AC6: TelemetrySubscriber logs/traces events."""

    def test_on_message_does_not_raise(self) -> None:
        """on_message processes a mock message without error."""
        subscriber = TelemetrySubscriber()
        msg = MagicMock()
        msg.__class__.__name__ = "StartMessage"
        subscriber.on_message(msg)

    def test_on_stop_does_not_raise(self) -> None:
        """on_stop completes without error."""
        subscriber = TelemetrySubscriber()
        subscriber.on_stop()


class TestTelemetrySubscriberAsyncWorker:
    """Story 20.1 (ADR-018): async-worker contract for TelemetrySubscriber.

    ``on_message`` must not block on logfire I/O — emission runs on a daemon
    worker thread drained via a deterministic ``_flush()`` barrier.
    """

    def test_on_message_returns_immediately_without_calling_logfire_inline(self) -> None:
        """``on_message`` returns in sub-millisecond time even when logfire is slow.

        Patches ``logfire.info`` to sleep 2 s. If ``on_message`` ran on the
        caller's thread, it would block for 2 s. Because emission is queued
        to the worker, the caller returns promptly.
        """
        subscriber = TelemetrySubscriber()
        msg = MagicMock()
        msg.__class__.__name__ = "StartMessage"

        with patch("akgentic.infra.adapters.shared.telemetry_subscriber.logfire") as mock_lf:
            mock_lf.info.side_effect = lambda *a, **kw: time.sleep(2)

            start = time.perf_counter()
            subscriber.on_message(msg)
            elapsed = time.perf_counter() - start

            # Generous ceiling for shared CI runners; the real target is sub-ms.
            assert elapsed < 0.05, f"on_message took {elapsed * 1000:.1f} ms"

        subscriber.on_stop()

    def test_flush_then_logfire_called_with_expected_args(self) -> None:
        """After explicit flush, ``logfire.info`` was called with sender/msg_type/team_id."""
        subscriber = TelemetrySubscriber()
        msg = MagicMock()
        msg.__class__.__name__ = "StartMessage"
        msg.sender.name = "orchestrator"
        msg.team_id = "team-xyz"

        with patch("akgentic.infra.adapters.shared.telemetry_subscriber.logfire") as mock_lf:
            subscriber.on_message(msg)
            assert subscriber._flush(timeout=5.0), "worker did not drain in time"

            mock_lf.info.assert_called_once()
            _, kwargs = mock_lf.info.call_args
            assert kwargs["sender"] == "orchestrator"
            assert kwargs["msg_type"] == "StartMessage"
            assert kwargs["team_id"] == "team-xyz"

        subscriber.on_stop()

    def test_restoring_flag_suppresses_enqueue(self) -> None:
        """``_restoring=True`` drops messages before they reach the queue."""
        subscriber = TelemetrySubscriber()
        subscriber.set_restoring(True)
        msg = MagicMock()
        msg.__class__.__name__ = "StartMessage"

        with patch("akgentic.infra.adapters.shared.telemetry_subscriber.logfire") as mock_lf:
            for _ in range(10):
                subscriber.on_message(msg)
            assert subscriber._flush(timeout=5.0)

            mock_lf.info.assert_not_called()

        subscriber.on_stop()

    def test_worker_is_daemon(self) -> None:
        """Worker thread is a daemon — process exit is never blocked on logfire."""
        subscriber = TelemetrySubscriber()
        assert subscriber._worker.daemon is True
        subscriber.on_stop()

    def test_on_stop_joins_within_timeout(self) -> None:
        """``on_stop`` returns within ~5 s and the worker thread exits."""
        subscriber = TelemetrySubscriber()

        start = time.perf_counter()
        subscriber.on_stop()
        elapsed = time.perf_counter() - start

        assert elapsed < 5.0, f"on_stop took {elapsed:.2f} s"
        assert not subscriber._worker.is_alive()


class TestOnStopRequest:
    """Story 22.1 AC4: on_stop_request is a no-op — never raises, never enqueues."""

    def test_on_stop_request_returns_none_and_does_not_raise(self) -> None:
        """Direct invocation returns None without raising."""
        subscriber = TelemetrySubscriber()
        try:
            result = subscriber.on_stop_request()
            assert result is None
        finally:
            subscriber.on_stop()

    def test_on_stop_request_does_not_enqueue_on_worker(self) -> None:
        """Queue invariant: on_stop_request must NOT enqueue a telemetry record.

        Emission belongs on_message; a stop-request signal is metadata from
        the orchestrator and is not a trace event itself.
        """
        subscriber = TelemetrySubscriber()
        try:
            qsize_before = subscriber._queue.qsize()

            subscriber.on_stop_request()
            # Allow worker a tiny window to drain anything unexpected.
            assert subscriber._flush(timeout=5.0)

            with patch("akgentic.infra.adapters.shared.telemetry_subscriber.logfire") as mock_lf:
                # Re-flush after patching so any pending emission would surface.
                assert subscriber._flush(timeout=5.0)
                mock_lf.info.assert_not_called()

            # Queue size is unchanged (ignoring barrier sentinels which are drained
            # by _flush before we observe).
            assert subscriber._queue.qsize() == qsize_before
        finally:
            subscriber.on_stop()

    def test_on_stop_request_before_on_stop_then_stop_still_works(self) -> None:
        """Idempotent: calling on_stop_request then on_stop still shuts the worker down."""
        subscriber = TelemetrySubscriber()

        subscriber.on_stop_request()
        subscriber.on_stop()

        assert not subscriber._worker.is_alive()
