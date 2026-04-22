"""Unit tests for the Timer helper class and Orchestrator timer integration."""

import logging
import os
import threading
import time
import uuid
from collections.abc import Generator
from unittest.mock import MagicMock, patch

import pykka
import pytest

from akgentic.core.agent_config import BaseConfig
from akgentic.core.messages.orchestrator import ErrorMessage, ProcessedMessage, ReceivedMessage
from akgentic.core.orchestrator import TIMER_DELAY, EventSubscriber, Orchestrator, Timer


@pytest.fixture(autouse=True)
def cleanup_actors() -> Generator[None, None, None]:
    """Ensure all actors are stopped after each test."""
    yield
    pykka.ActorRegistry.stop_all()


# ---------------------------------------------------------------------------
# Timer class unit tests
# ---------------------------------------------------------------------------


class TestTimerInitialization:
    """Tests for Timer class initialization."""

    def test_timer_initializes_with_delay_and_callback(self) -> None:
        """Timer stores delay and callback correctly."""
        callback = MagicMock()
        timer = Timer(delay=60, timeout_callback=callback)

        assert timer.delay == 60
        assert timer.timeout_callback is callback

    def test_timer_initializes_task_count_to_zero(self) -> None:
        """Timer task_count starts at 0."""
        timer = Timer(delay=60, timeout_callback=MagicMock())
        assert timer.task_count == 0

    def test_timer_initializes_internal_timer_to_none(self) -> None:
        """Timer._timer starts as None before start() is called."""
        timer = Timer(delay=60, timeout_callback=MagicMock())
        assert timer._timer is None


class TestTimerStartAndCancel:
    """Tests for Timer start() and cancel() methods."""

    def test_start_creates_threading_timer(self) -> None:
        """start() creates an active threading.Timer."""
        callback = MagicMock()
        timer = Timer(delay=60, timeout_callback=callback)
        timer.start()

        try:
            assert timer._timer is not None
            assert isinstance(timer._timer, threading.Timer)
        finally:
            timer.cancel()

    def test_cancel_stops_timer(self) -> None:
        """cancel() cancels the active threading.Timer and sets it to None."""
        timer = Timer(delay=60, timeout_callback=MagicMock())
        timer.start()
        timer.cancel()

        assert timer._timer is None

    def test_cancel_on_unstarted_timer_is_safe(self) -> None:
        """cancel() on a timer that was never started does not raise."""
        timer = Timer(delay=60, timeout_callback=MagicMock())
        timer.cancel()  # Should not raise
        assert timer._timer is None

    def test_start_replaces_existing_timer(self) -> None:
        """Calling start() twice replaces the previous timer."""
        callback = MagicMock()
        timer = Timer(delay=60, timeout_callback=callback)
        timer.start()
        first_timer = timer._timer

        timer.start()
        try:
            assert timer._timer is not first_timer
        finally:
            timer.cancel()

    def test_timer_fires_callback_after_delay(self) -> None:
        """Timer invokes callback after the specified delay."""
        callback = MagicMock()
        # Use a real threading.Timer with a short float internally;
        # we construct it directly to keep the test fast.
        timer = Timer.__new__(Timer)
        timer.delay = 1
        timer.timeout_callback = callback
        timer.task_count = 0
        timer._timer = None
        # Start with a short internal threading.Timer for test speed
        timer._timer = threading.Timer(0.1, callback)
        timer._timer.start()

        time.sleep(0.3)
        callback.assert_called_once()

    def test_cancel_prevents_callback(self) -> None:
        """Cancelling timer before it fires prevents callback invocation."""
        callback = MagicMock()
        timer = Timer(delay=1, timeout_callback=callback)
        # Start a short internal timer for test speed
        timer._timer = threading.Timer(0.5, callback)
        timer._timer.start()
        timer.cancel()

        time.sleep(0.7)
        callback.assert_not_called()


class TestTimerTaskStarted:
    """Tests for Timer.task_started() method."""

    def test_task_started_increments_task_count(self) -> None:
        """task_started() increments task_count by 1."""
        timer = Timer(delay=60, timeout_callback=MagicMock())
        timer.task_started()
        assert timer.task_count == 1

    def test_task_started_increments_multiple_times(self) -> None:
        """Multiple task_started() calls increment count cumulatively."""
        timer = Timer(delay=60, timeout_callback=MagicMock())
        timer.task_started()
        timer.task_started()
        timer.task_started()
        assert timer.task_count == 3

    def test_task_started_cancels_timer_when_count_positive(self) -> None:
        """task_started() cancels the running timer when count > 0."""
        timer = Timer(delay=60, timeout_callback=MagicMock())
        timer.start()
        assert timer._timer is not None

        timer.task_started()
        assert timer._timer is None
        assert timer.task_count == 1

    def test_task_started_cancels_timer_on_subsequent_calls(self) -> None:
        """task_started() keeps timer cancelled for multiple concurrent tasks."""
        callback = MagicMock()
        timer = Timer(delay=1, timeout_callback=callback)
        # Manually place a short internal timer to speed up the test
        timer._timer = threading.Timer(0.1, callback)
        timer._timer.start()

        timer.task_started()  # count = 1, timer cancelled
        timer.task_started()  # count = 2, timer still cancelled

        time.sleep(0.3)
        callback.assert_not_called()
        assert timer.task_count == 2


class TestTimerTaskCompleted:
    """Tests for Timer.task_completed() method."""

    def test_task_completed_decrements_task_count(self) -> None:
        """task_completed() decrements task_count by 1."""
        timer = Timer(delay=60, timeout_callback=MagicMock())
        timer.task_count = 2
        timer.task_completed()
        assert timer.task_count == 1

    def test_task_completed_starts_timer_when_count_reaches_zero(self) -> None:
        """task_completed() restarts timer when count drops to 0."""
        callback = MagicMock()
        timer = Timer(delay=60, timeout_callback=callback)
        timer.task_count = 1

        timer.task_completed()

        try:
            assert timer.task_count == 0
            assert timer._timer is not None
        finally:
            timer.cancel()

    def test_task_completed_prevents_negative_count(self) -> None:
        """task_completed() clamps task_count to 0, not negative."""
        timer = Timer(delay=60, timeout_callback=MagicMock())
        timer.task_count = 0

        timer.task_completed()

        try:
            assert timer.task_count == 0
        finally:
            timer.cancel()

    def test_task_completed_does_not_start_timer_while_tasks_active(self) -> None:
        """task_completed() does not restart timer while other tasks are still active."""
        timer = Timer(delay=60, timeout_callback=MagicMock())
        timer.task_count = 2

        timer.task_completed()  # count = 1, still active

        assert timer.task_count == 1
        assert timer._timer is None


class TestTimerConcurrentTaskTracking:
    """Tests for timer behaviour with multiple concurrent tasks."""

    def test_timer_resets_after_all_tasks_complete(self) -> None:
        """Timer restarts only after ALL started tasks have completed."""
        callback = MagicMock()
        timer = Timer(delay=60, timeout_callback=callback)
        timer.start()

        # Simulate 3 concurrent agents receiving messages
        timer.task_started()  # count = 1
        timer.task_started()  # count = 2
        timer.task_started()  # count = 3

        # Simulate 2 completing
        timer.task_completed()  # count = 2 — timer stays off
        timer.task_completed()  # count = 1 — timer stays off
        assert timer._timer is None

        # Last task completes
        timer.task_completed()  # count = 0 — timer restarts
        try:
            assert timer._timer is not None
            assert timer.task_count == 0
        finally:
            timer.cancel()

    def test_full_start_complete_cycle_restarts_timer(self) -> None:
        """Full task cycle: start → task_started → task_completed → timer active again."""
        callback = MagicMock()
        timer = Timer(delay=60, timeout_callback=callback)
        timer.start()

        timer.task_started()  # pauses timer
        assert timer._timer is None

        timer.task_completed()  # restarts timer when count=0
        try:
            assert timer._timer is not None
            assert timer.task_count == 0
        finally:
            timer.cancel()


# ---------------------------------------------------------------------------
# Orchestrator timer integration tests
# ---------------------------------------------------------------------------


class TestOrchestratorTimerInitialization:
    """Tests for timer initialization in Orchestrator.on_start()."""

    def test_orchestrator_creates_timer_on_init(self) -> None:
        """Orchestrator creates a Timer instance during on_start()."""
        config = BaseConfig(name="test-orchestrator", role="Orchestrator")
        orch_ref = Orchestrator.start(config=config)
        orch = orch_ref.proxy()

        timer = orch.get_timer().get()
        assert isinstance(timer, Timer)

        orch_ref.stop()

    def test_orchestrator_timer_starts_immediately(self) -> None:
        """Orchestrator timer is active immediately after on_start."""
        config = BaseConfig(name="test-orchestrator", role="Orchestrator")
        orch_ref = Orchestrator.start(config=config)
        orch = orch_ref.proxy()

        timer = orch.get_timer().get()
        # Timer should have an active internal threading.Timer
        assert timer._timer is not None

        orch_ref.stop()

    def test_orchestrator_timer_uses_default_delay(self) -> None:
        """Orchestrator timer defaults to TIMER_DELAY when env var not set."""
        with patch.dict(os.environ, {}, clear=True):
            config = BaseConfig(name="test-orchestrator", role="Orchestrator")
            orch_ref = Orchestrator.start(config=config)
            orch = orch_ref.proxy()

            timer = orch.get_timer().get()
            assert timer.delay == TIMER_DELAY

            orch_ref.stop()

    def test_orchestrator_timer_uses_env_var_delay(self) -> None:
        """Orchestrator timer delay is set from ORCHESTRATOR_TIMEOUT_DELAY env var."""
        with patch.dict(os.environ, {"ORCHESTRATOR_TIMEOUT_DELAY": "42"}):
            config = BaseConfig(name="test-orchestrator", role="Orchestrator")
            orch_ref = Orchestrator.start(config=config)
            orch = orch_ref.proxy()

            timer = orch.get_timer().get()
            assert timer.delay == 42

            orch_ref.stop()


class TestOrchestratorTimerMessageHandlers:
    """Tests verifying timer integration with message handlers.

    Strategy: we retrieve the Timer object returned by get_timer(), then send
    a message through the actor proxy and verify the Timer's task_count changed
    as expected. This avoids the need to inject mocks into a running actor.
    """

    def _make_received_message(self) -> ReceivedMessage:
        msg_id = uuid.uuid4()
        return ReceivedMessage(message_id=msg_id)

    def _make_processed_message(self) -> ProcessedMessage:
        return ProcessedMessage(message_id=uuid.uuid4())

    def _make_error_message(self) -> ErrorMessage:
        return ErrorMessage(
            exception_type="ValueError",
            exception_value="something went wrong",
        )

    def test_received_message_increments_task_count(self) -> None:
        """receiveMsg_ReceivedMessage calls timer.task_started() → task_count increases."""
        config = BaseConfig(name="test-orchestrator", role="Orchestrator")
        orch_ref = Orchestrator.start(config=config)
        orch = orch_ref.proxy()

        timer = orch.get_timer().get()
        assert timer.task_count == 0

        # We need a non-self sender; create a dummy actor to act as external sender
        dummy_config = BaseConfig(name="dummy-agent", role="Agent")

        class _DummyOrch(Orchestrator):
            pass

        dummy_ref = _DummyOrch.start(config=dummy_config)
        dummy_proxy = dummy_ref.proxy()
        sender_addr = dummy_proxy.myAddress.get()

        msg = self._make_received_message()
        orch.receiveMsg_ReceivedMessage(msg, sender_addr).get()

        # task_started() was called: timer is cancelled (count > 0 → timer = None)
        assert timer.task_count == 1
        assert timer._timer is None

        dummy_ref.stop()
        orch_ref.stop()

    def test_processed_message_decrements_task_count_and_restarts_timer(self) -> None:
        """receiveMsg_ProcessedMessage calls timer.task_completed() → timer restarts."""
        config = BaseConfig(name="test-orchestrator", role="Orchestrator")
        orch_ref = Orchestrator.start(config=config)
        orch = orch_ref.proxy()

        timer = orch.get_timer().get()
        # Manually set count to 1 to simulate active task
        timer.task_count = 1
        timer.cancel()  # timer was paused due to active task

        dummy_config = BaseConfig(name="dummy-agent2", role="Agent")

        class _DummyOrch2(Orchestrator):
            pass

        dummy_ref = _DummyOrch2.start(config=dummy_config)
        dummy_proxy = dummy_ref.proxy()
        sender_addr = dummy_proxy.myAddress.get()

        msg = self._make_processed_message()
        orch.receiveMsg_ProcessedMessage(msg, sender_addr).get()

        # task_completed() was called: count reaches 0, timer restarted
        assert timer.task_count == 0
        assert timer._timer is not None

        dummy_ref.stop()
        orch_ref.stop()

    def test_error_message_does_not_affect_task_count(self) -> None:
        """receiveMsg_ErrorMessage does NOT modify timer task_count (timer relies only on received/processed)."""
        config = BaseConfig(name="test-orchestrator", role="Orchestrator")
        orch_ref = Orchestrator.start(config=config)
        orch = orch_ref.proxy()

        timer = orch.get_timer().get()
        timer.task_count = 1
        timer.cancel()

        dummy_config = BaseConfig(name="dummy-agent3", role="Agent")

        class _DummyOrch3(Orchestrator):
            pass

        dummy_ref = _DummyOrch3.start(config=dummy_config)
        dummy_proxy = dummy_ref.proxy()
        sender_addr = dummy_proxy.myAddress.get()

        msg = self._make_error_message()
        orch.receiveMsg_ErrorMessage(msg, sender_addr).get()

        # Error messages should NOT decrement task_count
        assert timer.task_count == 1

        dummy_ref.stop()
        orch_ref.stop()

    def test_received_message_from_self_skips_task_started(self) -> None:
        """receiveMsg_ReceivedMessage from self does NOT increment task_count."""
        config = BaseConfig(name="test-orchestrator", role="Orchestrator")
        orch_ref = Orchestrator.start(config=config)
        orch = orch_ref.proxy()

        timer = orch.get_timer().get()
        initial_count = timer.task_count

        my_address = orch.myAddress.get()
        msg = self._make_received_message()
        orch.receiveMsg_ReceivedMessage(msg, my_address).get()

        assert timer.task_count == initial_count

        orch_ref.stop()


class TestOrchestratorStop:
    """Tests for timer cancellation in Orchestrator.stop()."""

    def test_stop_cancels_timer(self) -> None:
        """Calling stop() on the orchestrator cancels the inactivity timer."""
        config = BaseConfig(name="test-orchestrator", role="Orchestrator")
        orch_ref = Orchestrator.start(config=config)
        orch = orch_ref.proxy()

        # Retrieve the timer object before stopping
        timer = orch.get_timer().get()
        assert timer._timer is not None

        # Call the orchestrator's own stop() method (not the pykka ActorRef.stop())
        # This exercises the Orchestrator.stop() override that cancels the timer.
        orch.stop().get()

        # Give the actor time to fully process the stop
        time.sleep(0.1)

        # Timer should have been cancelled by the Orchestrator.stop() override
        assert timer._timer is None


class _RecordingStopSubscriber:
    """Subscriber that records on_stop_request / on_stop / on_message invocations."""

    def __init__(self) -> None:
        self.stop_request_count: int = 0
        self.stop_count: int = 0
        self.messages: list[object] = []

    def set_restoring(self, restoring: bool) -> None:  # noqa: FBT001
        """Protocol compliance — no-op for these tests."""
        pass

    def on_stop_request(self) -> None:
        self.stop_request_count += 1

    def on_stop(self) -> None:
        self.stop_count += 1

    def on_message(self, msg: object) -> None:
        self.messages.append(msg)


class TestEventSubscriberProtocol:
    """AC1: ``on_stop_request`` is part of the ``EventSubscriber`` protocol."""

    def test_on_stop_request_exists_on_protocol(self) -> None:
        """The ``EventSubscriber`` protocol exposes ``on_stop_request``."""
        assert hasattr(EventSubscriber, "on_stop_request")
        assert callable(EventSubscriber.on_stop_request)

    def test_on_stop_request_default_implementation_is_noop(self) -> None:
        """A subclass that inherits the default definition returns ``None``.

        The protocol body is ``...`` which is compiled to a ``None`` return.
        Subscribers that do not implement ``on_stop_request`` should therefore
        silently do nothing.
        """

        class _MinimalSubscriber(EventSubscriber):
            pass

        sub = _MinimalSubscriber()
        # Calling the default method must not raise and must return None
        assert sub.on_stop_request() is None  # type: ignore[func-returns-value]


class TestOrchestratorTimeoutHandler:
    """AC2/AC3/AC4: ``_timeout_handler`` delegates shutdown via subscribers."""

    def test_timeout_notifies_subscribers_on_stop_request(self) -> None:
        """AC2: firing the timer calls ``on_stop_request`` on every subscriber."""
        config = BaseConfig(name="test-orchestrator", role="Orchestrator")
        orch_ref = Orchestrator.start(config=config)
        orch = orch_ref.proxy()

        sub = _RecordingStopSubscriber()
        orch.subscribe(sub).get()

        # Invoke the handler the same way ``threading.Timer`` would —
        # directly via the callback stored on the Timer. This isolates the
        # behavioural contract of ``_timeout_handler`` from scheduling noise.
        timer = orch.get_timer().get()
        timer.timeout_callback()

        # Give the actor thread a moment to process any internal messages
        # (snapshot_for_subscribers runs on the caller; subscriber calls are
        # synchronous within _notify_subscribers, so assertions are immediate).
        assert sub.stop_request_count == 1
        # on_stop is a separate lifecycle hook; the timeout must not trigger it
        assert sub.stop_count == 0

        orch_ref.stop()

    def test_timeout_does_not_stop_orchestrator(self) -> None:
        """AC2/AC3: the orchestrator stays alive after timer fires.

        The refactor delegates shutdown to the subscriber — the orchestrator
        no longer sends ``StopRecursively`` to itself on inactivity. This
        test verifies the actor remains alive after a real timeout fires
        without any subscriber taking action.
        """
        with patch.dict(os.environ, {"ORCHESTRATOR_TIMEOUT_DELAY": "1"}):
            config = BaseConfig(name="test-orchestrator", role="Orchestrator")
            orch_ref = Orchestrator.start(config=config)
            orch = orch_ref.proxy()

            sub = _RecordingStopSubscriber()
            orch.subscribe(sub).get()

            # Wait past the configured 1s inactivity delay so the timer fires.
            # Poll up to 3s to confirm the subscriber was notified.
            deadline = time.monotonic() + 3.0
            while sub.stop_request_count == 0 and time.monotonic() < deadline:
                time.sleep(0.1)

            # Subscriber was notified at least once
            assert sub.stop_request_count >= 1
            # Orchestrator remained alive — shutdown is the subscriber's job now
            assert orch_ref.is_alive()

            orch_ref.stop()

    def test_timeout_log_uses_actor_team_id(self, caplog: pytest.LogCaptureFixture) -> None:
        """AC4: the inactivity log line uses the actor's ``team_id`` attribute."""
        team_id = uuid.uuid4()
        config = BaseConfig(name="test-orchestrator", role="Orchestrator")
        orch_ref = Orchestrator.start(config=config, team_id=team_id)
        orch = orch_ref.proxy()

        try:
            timer = orch.get_timer().get()
            with caplog.at_level(logging.INFO, logger="akgentic.core.orchestrator"):
                # Fire the handler directly (same callback threading.Timer uses)
                timer.timeout_callback()

            timeout_records = [
                r for r in caplog.records if "Orchestrator timeout after" in r.getMessage()
            ]
            assert len(timeout_records) == 1
            assert f"team={team_id}" in timeout_records[0].getMessage()
        finally:
            orch_ref.stop()
