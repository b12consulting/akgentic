"""Tests for TelemetrySubscriber adapter."""

from __future__ import annotations

import inspect
import time
import uuid
from unittest.mock import MagicMock, patch

from akgentic.core.orchestrator import EventSubscriber

from akgentic.infra.adapters.shared import telemetry_subscriber as ts_module
from akgentic.infra.adapters.shared.telemetry_subscriber import TelemetrySubscriber

_TEAM_ID = uuid.uuid4()


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

    def test_on_message_signature_matches_protocol(self) -> None:
        """on_message has msg parameter matching EventSubscriber."""
        sig = inspect.signature(TelemetrySubscriber.on_message)
        assert "msg" in sig.parameters

    def test_on_stop_signature_matches_protocol(self) -> None:
        """on_stop takes a single ``team_id`` parameter beyond self (Story 27.1)."""
        sig = inspect.signature(TelemetrySubscriber.on_stop)
        params = [p for p in sig.parameters if p != "self"]
        assert len(params) == 1
        assert params[0] == "team_id"

    def test_on_stop_request_signature_matches_protocol(self) -> None:
        """on_stop_request takes a single ``team_id`` parameter beyond self (Story 27.1)."""
        sig = inspect.signature(TelemetrySubscriber.on_stop_request)
        params = [p for p in sig.parameters if p != "self"]
        assert len(params) == 1
        assert params[0] == "team_id"

    def test_satisfies_event_subscriber_protocol(self) -> None:
        """Story 27.1 AC #4: TelemetrySubscriber structurally satisfies EventSubscriber.

        ``EventSubscriber`` is a ``typing.Protocol`` (not ``runtime_checkable``),
        so this test assigns the subscriber to a Protocol-typed name to surface
        structural mismatches at mypy time and asserts each required method
        exists at runtime.
        """
        subscriber: EventSubscriber = TelemetrySubscriber()
        assert callable(subscriber.set_restoring)
        assert callable(subscriber.on_stop_request)
        assert callable(subscriber.on_stop)
        assert callable(subscriber.on_message)


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
        subscriber.on_stop(_TEAM_ID)


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

    def test_restoring_flag_suppresses_enqueue(self) -> None:
        """``set_restoring(team, True)`` drops same-team messages before they reach the queue."""
        subscriber = TelemetrySubscriber()
        subscriber.set_restoring(_TEAM_ID, True)
        msg = MagicMock()
        msg.__class__.__name__ = "StartMessage"
        msg.team_id = _TEAM_ID

        with patch("akgentic.infra.adapters.shared.telemetry_subscriber.logfire") as mock_lf:
            for _ in range(10):
                subscriber.on_message(msg)
            assert subscriber._flush(timeout=5.0)

            mock_lf.info.assert_not_called()

    def test_worker_is_daemon(self) -> None:
        """Worker thread is a daemon — process exit is never blocked on logfire (AC #6)."""
        subscriber = TelemetrySubscriber()
        assert subscriber._worker.daemon is True


class TestOnStopRequest:
    """Story 22.1 AC4: on_stop_request is a no-op — never raises, never enqueues."""

    def test_on_stop_request_returns_none_and_does_not_raise(self) -> None:
        """Direct invocation returns None without raising."""
        subscriber = TelemetrySubscriber()
        result = subscriber.on_stop_request(_TEAM_ID)
        assert result is None

    def test_on_stop_request_does_not_enqueue_on_worker(self) -> None:
        """Queue invariant: on_stop_request must NOT enqueue a telemetry record.

        Emission belongs on_message; a stop-request signal is metadata from
        the orchestrator and is not a trace event itself.
        """
        subscriber = TelemetrySubscriber()
        qsize_before = subscriber._queue.qsize()

        subscriber.on_stop_request(_TEAM_ID)
        # Allow worker a tiny window to drain anything unexpected.
        assert subscriber._flush(timeout=5.0)

        with patch("akgentic.infra.adapters.shared.telemetry_subscriber.logfire") as mock_lf:
            # Re-flush after patching so any pending emission would surface.
            assert subscriber._flush(timeout=5.0)
            mock_lf.info.assert_not_called()

        # Queue size is unchanged (ignoring barrier sentinels which are drained
        # by _flush before we observe).
        assert subscriber._queue.qsize() == qsize_before


class TestPerTeamRestoringSet:
    """Story 27.1 AC #3, #4: ``_restoring`` is a per-team set with lock-guarded mutation."""

    def test_set_restoring_adds_team_id_to_set(self) -> None:
        """``set_restoring(team, True)`` adds; ``set_restoring(team, False)`` discards."""
        subscriber = TelemetrySubscriber()
        team_a = uuid.uuid4()
        subscriber.set_restoring(team_a, True)
        assert team_a in subscriber._restoring

        subscriber.set_restoring(team_a, False)
        assert team_a not in subscriber._restoring

    def test_set_restoring_discard_is_idempotent(self) -> None:
        """``set_restoring(team, False)`` on an absent team is a no-op (set.discard semantics)."""
        subscriber = TelemetrySubscriber()
        team_a = uuid.uuid4()
        # Not previously added; must not raise.
        subscriber.set_restoring(team_a, False)
        assert team_a not in subscriber._restoring

    def test_restoring_set_starts_empty(self) -> None:
        """Fresh subscriber has an empty restoring set (not False/None)."""
        subscriber = TelemetrySubscriber()
        assert isinstance(subscriber._restoring, set)
        assert len(subscriber._restoring) == 0

    def test_on_message_suppresses_only_restoring_team(self) -> None:
        """team_a in restoring ⇒ team_a messages dropped, team_b messages still emitted."""
        subscriber = TelemetrySubscriber()
        team_a = uuid.uuid4()
        team_b = uuid.uuid4()
        subscriber.set_restoring(team_a, True)

        msg_a = MagicMock()
        msg_a.__class__.__name__ = "StartMessage"
        msg_a.sender.name = "orchestrator"
        msg_a.team_id = team_a

        msg_b = MagicMock()
        msg_b.__class__.__name__ = "StartMessage"
        msg_b.sender.name = "orchestrator"
        msg_b.team_id = team_b

        with patch("akgentic.infra.adapters.shared.telemetry_subscriber.logfire") as mock_lf:
            subscriber.on_message(msg_a)  # dropped
            subscriber.on_message(msg_b)  # emitted
            assert subscriber._flush(timeout=5.0)

            # Only team_b's message reached logfire.
            assert mock_lf.info.call_count == 1
            _, kwargs = mock_lf.info.call_args
            assert kwargs["team_id"] == team_b


class TestOnStopIsNoActionOnSharedSubscriber:
    """Story 28.1 AC #5/#6: ``on_stop(team_id)`` is a no-action signal on this shared subscriber.

    The daemon worker is shared across every team. ``on_stop`` MUST NOT tear it
    down on a single team's stop. The worker is reclaimed by the Python runtime
    at process exit (``daemon=True``).
    """

    def test_on_stop_leaves_worker_alive(self) -> None:
        """Two per-team ``on_stop`` calls leave the worker alive."""
        subscriber = TelemetrySubscriber()
        team_a = uuid.uuid4()
        team_b = uuid.uuid4()

        subscriber.on_stop(team_a)
        assert subscriber._worker.is_alive()

        subscriber.on_stop(team_b)
        assert subscriber._worker.is_alive()


class TestCloseAndShutdownSentinelAreGone:
    """Story 28.1 AC #5: ``close()`` and ``_SHUTDOWN`` are removed; the worker thread is daemon."""

    def test_close_method_does_not_exist(self) -> None:
        """``TelemetrySubscriber.close`` is gone — no caller in any tier (ADR-025 §5)."""
        assert hasattr(TelemetrySubscriber, "close") is False

    def test_shutdown_sentinel_is_gone(self) -> None:
        """Module-level ``_SHUTDOWN`` sentinel is removed — no consumer in ``_run``."""
        assert getattr(ts_module, "_SHUTDOWN", None) is None

    def test_worker_thread_is_daemon(self) -> None:
        """Worker thread remains ``daemon=True`` — reclaimed at process exit."""
        subscriber = TelemetrySubscriber()
        assert subscriber._worker.daemon is True


class TestFlushBarrierIsPreserved:
    """Story 28.1 AC #7: ``_flush()`` and ``_FlushBarrier`` are preserved as test-only helpers."""

    def test_flush_barrier_returns_true(self) -> None:
        """``_flush(timeout=1.0)`` returns ``True`` on a healthy worker (AC #7)."""
        subscriber = TelemetrySubscriber()
        assert subscriber._flush(timeout=1.0) is True

    def test_flush_after_on_message_emits_to_logfire(self) -> None:
        """``on_message`` (non-restoring) followed by ``_flush`` triggers one emit (AC #6)."""
        subscriber = TelemetrySubscriber()
        msg = MagicMock()
        msg.__class__.__name__ = "StartMessage"
        msg.sender.name = "orchestrator"
        msg.team_id = _TEAM_ID

        with patch("akgentic.infra.adapters.shared.telemetry_subscriber.logfire") as mock_lf:
            subscriber.on_message(msg)
            assert subscriber._flush(timeout=1.0) is True
            mock_lf.info.assert_called_once()
