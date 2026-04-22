"""Tests for :class:`TimerStopSubscriber`.

Covers the auto-attached timer-stop bridge living in ``akgentic-team``.
The subscriber's ``on_stop_request`` offloads to a daemon thread to
avoid a deadlock with the orchestrator actor thread; tests poll for
the stop_team call to observe completion.
"""

from __future__ import annotations

import threading
import time
import uuid
from typing import Any

import pytest

from akgentic.team.subscriber import TimerStopSubscriber


class _RecordingTeamManager:
    """Minimal ``TeamManager`` stub recording stop_team calls."""

    def __init__(self, raise_exc: Exception | None = None) -> None:
        self.calls: list[uuid.UUID] = []
        self._raise_exc = raise_exc

    def stop_team(self, team_id: uuid.UUID) -> None:
        self.calls.append(team_id)
        if self._raise_exc is not None:
            raise self._raise_exc


def _wait_for(condition: Any, timeout: float = 2.0) -> None:
    """Poll ``condition`` (a callable) until true or timeout."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():  # type: ignore[operator]
            return
        time.sleep(0.01)
    pytest.fail("timed out waiting for condition")


def test_on_stop_request_calls_stop_team_with_team_id() -> None:
    manager = _RecordingTeamManager()
    team_id = uuid.uuid4()
    sub = TimerStopSubscriber(manager, team_id)  # type: ignore[arg-type]

    sub.on_stop_request()

    _wait_for(lambda: manager.calls == [team_id])


def test_on_stop_request_idempotent_on_already_stopped_value_error() -> None:
    manager = _RecordingTeamManager(raise_exc=ValueError("already stopped"))
    team_id = uuid.uuid4()
    sub = TimerStopSubscriber(manager, team_id)  # type: ignore[arg-type]

    # Must not raise.
    sub.on_stop_request()
    _wait_for(lambda: manager.calls == [team_id])


def test_on_stop_request_idempotent_on_deleted_value_error() -> None:
    manager = _RecordingTeamManager(raise_exc=ValueError("Team no longer exists"))
    team_id = uuid.uuid4()
    sub = TimerStopSubscriber(manager, team_id)  # type: ignore[arg-type]

    sub.on_stop_request()
    _wait_for(lambda: manager.calls == [team_id])


def test_on_stop_request_logs_and_swallows_unexpected_error(
    caplog: pytest.LogCaptureFixture,
) -> None:
    manager = _RecordingTeamManager(raise_exc=RuntimeError("unexpected"))
    team_id = uuid.uuid4()
    sub = TimerStopSubscriber(manager, team_id)  # type: ignore[arg-type]

    with caplog.at_level(
        "WARNING",
        logger="akgentic.team.subscriber",
    ):
        sub.on_stop_request()
        _wait_for(lambda: manager.calls == [team_id])
        # Give the thread's exception handler time to emit the warning.
        time.sleep(0.05)

    assert any(
        "stop_team failed" in record.getMessage() for record in caplog.records
    )


def test_on_stop_is_noop() -> None:
    """``on_stop`` is intentionally a no-op — trigger moved to ``on_stop_request``."""
    manager = _RecordingTeamManager()
    team_id = uuid.uuid4()
    sub = TimerStopSubscriber(manager, team_id)  # type: ignore[arg-type]

    sub.on_stop()
    # Give any daemon thread a chance to run (there should be none).
    time.sleep(0.05)
    assert manager.calls == []


def test_on_message_is_noop() -> None:
    manager = _RecordingTeamManager()
    team_id = uuid.uuid4()
    sub = TimerStopSubscriber(manager, team_id)  # type: ignore[arg-type]

    sub.on_message(object())  # type: ignore[arg-type]
    # Give any daemon thread a chance to run (there should be none).
    time.sleep(0.05)
    assert manager.calls == []


def test_set_restoring_is_noop() -> None:
    manager = _RecordingTeamManager()
    sub = TimerStopSubscriber(manager, uuid.uuid4())  # type: ignore[arg-type]
    sub.set_restoring(True)
    sub.set_restoring(False)
    assert manager.calls == []


def test_on_stop_request_uses_background_thread() -> None:
    """Ensure on_stop_request returns before stop_team fires (no sync recursion)."""
    started = threading.Event()
    release = threading.Event()
    finished = threading.Event()

    class _BlockingManager:
        def __init__(self) -> None:
            self.calls: list[uuid.UUID] = []

        def stop_team(self, team_id: uuid.UUID) -> None:
            started.set()
            release.wait(timeout=1.0)
            self.calls.append(team_id)
            finished.set()

    manager = _BlockingManager()
    sub = TimerStopSubscriber(manager, uuid.uuid4())  # type: ignore[arg-type]

    sub.on_stop_request()

    assert started.wait(timeout=1.0), "worker thread did not start"
    assert not finished.is_set(), "stop_team finished before release — not async"
    release.set()
    assert finished.wait(timeout=1.0), "worker thread did not finish after release"
