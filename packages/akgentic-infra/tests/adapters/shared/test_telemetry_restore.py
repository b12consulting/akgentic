"""Tests for TelemetrySubscriber restore-awareness (Story 4.2 + Story 20.1 updates).

Story 20.1 (ADR-018): ``on_message`` is now non-blocking — logfire emission
runs on a daemon worker. Live-emit assertions must flush the worker via
``subscriber._flush()`` before asserting on the mock.
"""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock, patch

from akgentic.infra.adapters.shared.telemetry_subscriber import TelemetrySubscriber

_TEAM_ID = uuid.uuid4()


class TestTelemetryRestoreAwareness:
    """AC #5, #6: TelemetrySubscriber supports set_restoring to skip logfire spans."""

    def test_on_message_calls_logfire_for_live_events(self) -> None:
        """AC #5: logfire.info is called for live (non-restoring) events.

        Flush the async worker before asserting — Story 20.1 moved emission
        off the actor thread, so the call is no longer synchronous.
        """
        subscriber = TelemetrySubscriber()
        msg = MagicMock()
        msg.__class__.__name__ = "StartMessage"

        with patch("akgentic.infra.adapters.shared.telemetry_subscriber.logfire") as mock_lf:
            subscriber.on_message(msg)
            assert subscriber._flush(timeout=5.0), "worker did not drain in time"
            mock_lf.info.assert_called_once()

    def test_restoring_skips_logfire_emission(self) -> None:
        """AC #6: set_restoring(team_id, True) suppresses logfire.info for that team.

        The ``_restoring`` guard drops messages whose ``team_id`` is in the
        per-team restoring set before they enter the queue, so the worker
        has nothing to emit.
        """
        subscriber = TelemetrySubscriber()
        subscriber.set_restoring(_TEAM_ID, True)
        msg = MagicMock()
        msg.__class__.__name__ = "StartMessage"
        msg.team_id = _TEAM_ID

        with patch("akgentic.infra.adapters.shared.telemetry_subscriber.logfire") as mock_lf:
            subscriber.on_message(msg)
            assert subscriber._flush(timeout=5.0)
            mock_lf.info.assert_not_called()

    def test_restoring_false_resumes_logfire(self) -> None:
        """set_restoring(False) resumes normal logfire emission."""
        subscriber = TelemetrySubscriber()
        subscriber.set_restoring(_TEAM_ID, True)
        subscriber.set_restoring(_TEAM_ID, False)
        msg = MagicMock()
        msg.__class__.__name__ = "StartMessage"

        with patch("akgentic.infra.adapters.shared.telemetry_subscriber.logfire") as mock_lf:
            subscriber.on_message(msg)
            assert subscriber._flush(timeout=5.0)
            mock_lf.info.assert_called_once()

    def test_on_stop_does_not_raise(self) -> None:
        """on_stop completes without error."""
        subscriber = TelemetrySubscriber()
        subscriber.on_stop(_TEAM_ID)
