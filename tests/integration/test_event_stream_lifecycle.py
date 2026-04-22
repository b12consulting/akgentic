"""Integration tests -- EventStream population during team lifecycle.

AC5: stream populated during normal operation
AC6: stream repopulated on restore (replay refills stream for WS clients)
AC7: stream removed on team stop (via TeamService.stop_team)
AC8: stream removed on team delete (safety net in TeamService.delete_team)

Uses smoke fixtures (TestModel) -- no OPENAI_API_KEY required.
"""

from __future__ import annotations

import time

import pytest

from akgentic.infra.adapters.community.local_event_stream import LocalEventStream
from akgentic.infra.protocols.event_stream import StreamClosed
from akgentic.infra.server.deps import CommunityServices
from akgentic.infra.server.services.team_service import TeamService


@pytest.mark.smoke
class TestEventStreamLifecycle:
    """Team lifecycle populates and cleans up the event stream."""

    @pytest.fixture()
    def team_service(self, smoke_services: CommunityServices) -> TeamService:
        svc = TeamService(services=smoke_services)
        smoke_services.ingestion.team_service = svc
        return svc

    @pytest.fixture()
    def event_stream(self, smoke_services: CommunityServices) -> LocalEventStream:
        assert isinstance(smoke_services.event_stream, LocalEventStream)
        return smoke_services.event_stream

    def test_stream_populated_during_normal_operation(
        self,
        team_service: TeamService,
        event_stream: LocalEventStream,
    ) -> None:
        """AC5: messages sent through orchestrator appear in event stream."""
        process = team_service.create_team("test-team", "test-user")
        team_id = process.team_id

        team_service.send_message(team_id, "hello")
        time.sleep(1.0)

        events = event_stream.read_from(team_id)
        assert len(events) > 0, "Expected events in stream after sending message"

        # Cleanup
        team_service.stop_team(team_id)

    def test_stream_removed_after_team_stop(
        self,
        team_service: TeamService,
        event_stream: LocalEventStream,
    ) -> None:
        """AC7: stream is removed on stop via TeamService.stop_team()."""
        process = team_service.create_team("test-team", "test-user")
        team_id = process.team_id

        team_service.send_message(team_id, "hello")
        time.sleep(1.0)

        events_before = event_stream.read_from(team_id)
        assert len(events_before) > 0

        team_service.stop_team(team_id)

        # Stream removed on stop — read_from returns empty for removed streams
        events_after = event_stream.read_from(team_id)
        assert events_after == []

    def test_stream_repopulated_on_restore(
        self,
        team_service: TeamService,
        event_stream: LocalEventStream,
    ) -> None:
        """AC6: restoring a team repopulates the EventStream from persisted events.

        The restore replay feeds events through EventStreamSubscriber (which has
        no set_restoring guard) so the stream is refilled for WS cursor-based replay.
        """
        process = team_service.create_team("test-team", "test-user")
        team_id = process.team_id

        team_service.send_message(team_id, "hello")
        time.sleep(1.0)

        events_before_stop = event_stream.read_from(team_id)
        count_before = len(events_before_stop)
        assert count_before > 0

        team_service.stop_team(team_id)

        # Stream removed on stop
        assert event_stream.read_from(team_id) == []

        # Restore team -- stream should be repopulated from persisted events.
        # The persisted event store may contain slightly fewer events than the
        # live stream (e.g. the initial StartMessage reaches EventStreamSubscriber
        # before the event store), so use a tolerant threshold.
        threshold = count_before - 2  # allow small delta between live and persisted counts

        team_service.restore_team(team_id)

        # Poll until restored stream has enough events
        events_after_restore = event_stream.read_from(team_id)
        for _ in range(20):
            time.sleep(0.25)
            events_after_restore = event_stream.read_from(team_id)
            if len(events_after_restore) >= threshold:
                break

        assert len(events_after_restore) > 0, "Expected events after restore"
        assert len(events_after_restore) >= threshold, (
            f"Restored stream should have at least {threshold} events, "
            f"got {len(events_after_restore)}"
        )

        # Cleanup
        team_service.stop_team(team_id)

    def test_stream_removed_on_delete(
        self,
        team_service: TeamService,
        event_stream: LocalEventStream,
    ) -> None:
        """AC8: deleting a team removes its event stream via safety net."""
        process = team_service.create_team("test-team", "test-user")
        team_id = process.team_id

        team_service.send_message(team_id, "hello")
        time.sleep(1.0)

        assert len(event_stream.read_from(team_id)) > 0

        team_service.delete_team(team_id)

        # After delete, stream should be removed by safety net
        events_after = event_stream.read_from(team_id)
        assert events_after == []

    def test_active_reader_gets_stream_closed_on_delete(
        self,
        team_service: TeamService,
        event_stream: LocalEventStream,
    ) -> None:
        """AC7/AC8: active StreamReader receives StreamClosed on team deletion."""
        process = team_service.create_team("test-team", "test-user")
        team_id = process.team_id

        team_service.send_message(team_id, "hello")
        time.sleep(1.0)

        reader = event_stream.subscribe(team_id)

        team_service.delete_team(team_id)

        # Drain existing buffered events first, then expect StreamClosed
        got_stream_closed = False
        for _ in range(100):  # safety limit to prevent infinite loop
            try:
                result = reader.read_next(timeout=0.5)
                if result is None:
                    # Timeout — stream may have been removed without close signal
                    break
            except StreamClosed:
                got_stream_closed = True
                break
        assert got_stream_closed, "Expected StreamClosed after team deletion"
