"""Tests for EventStreamSubscriber adapter.

Story 27.1: ``_seen_teams`` tracking removed; ``on_stop(team_id)`` is the
canonical per-team cleanup hook; ``set_restoring`` remains a documented no-op.
"""

from __future__ import annotations

import inspect
import uuid
from unittest.mock import MagicMock

from akgentic.core.messages import Message
from akgentic.core.orchestrator import EventSubscriber

from akgentic.infra.adapters.community.local_event_stream import LocalEventStream
from akgentic.infra.adapters.shared.event_stream_subscriber import EventStreamSubscriber
from akgentic.infra.protocols.event_stream import EventStream

_TEAM_ID = uuid.uuid4()


def _make_message(team_id: uuid.UUID | None = None) -> Message:
    """Create a Message with the given team_id."""
    return Message(team_id=team_id)


class TestEventStreamSubscriberProtocolCompliance:
    """AC1: EventStreamSubscriber implements EventSubscriber protocol."""

    def test_has_on_message_method(self) -> None:
        subscriber = EventStreamSubscriber(event_stream=LocalEventStream())
        assert callable(subscriber.on_message)

    def test_has_on_stop_method(self) -> None:
        subscriber = EventStreamSubscriber(event_stream=LocalEventStream())
        assert callable(subscriber.on_stop)

    def test_has_on_stop_request_method(self) -> None:
        """Story 22.1 AC1: subscriber exposes on_stop_request for timer-driven shutdown."""
        subscriber = EventStreamSubscriber(event_stream=LocalEventStream())
        assert callable(subscriber.on_stop_request)

    def test_on_message_signature_matches_protocol(self) -> None:
        sig = inspect.signature(EventStreamSubscriber.on_message)
        assert "msg" in sig.parameters

    def test_on_stop_signature_matches_protocol(self) -> None:
        """on_stop takes a single ``team_id`` parameter beyond self (Story 27.1)."""
        sig = inspect.signature(EventStreamSubscriber.on_stop)
        params = [p for p in sig.parameters if p != "self"]
        assert len(params) == 1
        assert params[0] == "team_id"

    def test_on_stop_request_signature_matches_protocol(self) -> None:
        """on_stop_request takes a single ``team_id`` parameter beyond self (Story 27.1)."""
        sig = inspect.signature(EventStreamSubscriber.on_stop_request)
        params = [p for p in sig.parameters if p != "self"]
        assert len(params) == 1
        assert params[0] == "team_id"

    def test_satisfies_event_subscriber_protocol(self) -> None:
        """Story 27.1 AC #2: EventStreamSubscriber structurally satisfies EventSubscriber."""
        subscriber: EventSubscriber = EventStreamSubscriber(event_stream=LocalEventStream())
        assert callable(subscriber.set_restoring)
        assert callable(subscriber.on_stop_request)
        assert callable(subscriber.on_stop)
        assert callable(subscriber.on_message)


class TestNoSeenTeamsAttribute:
    """Story 27.1 AC #8: ``_seen_teams`` tracking removed entirely."""

    def test_no_seen_teams_attribute(self) -> None:
        subscriber = EventStreamSubscriber(event_stream=LocalEventStream())
        assert not hasattr(subscriber, "_seen_teams")

    def test_no_internal_lock_attribute(self) -> None:
        """The ``threading.Lock`` that guarded _seen_teams was removed too."""
        subscriber = EventStreamSubscriber(event_stream=LocalEventStream())
        assert not hasattr(subscriber, "_lock")


class TestOnMessage:
    """Story 27.1 AC #9: ``on_message`` appends every event to the per-team stream."""

    def test_on_message_appends_to_stream_with_team_id(self) -> None:
        """Two messages with the same team_id ⇒ two ``append`` calls (no dedup)."""
        mock_stream = MagicMock(spec=EventStream)
        subscriber = EventStreamSubscriber(event_stream=mock_stream)
        team_id = uuid.uuid4()
        msg1 = _make_message(team_id=team_id)
        msg2 = _make_message(team_id=team_id)

        subscriber.on_message(msg1)
        mock_stream.append.assert_called_once_with(team_id, msg1)

        subscriber.on_message(msg2)
        assert mock_stream.append.call_count == 2
        mock_stream.append.assert_called_with(team_id, msg2)

    def test_on_message_skips_team_id_none(self) -> None:
        """A message with team_id=None must not reach the stream."""
        mock_stream = MagicMock(spec=EventStream)
        subscriber = EventStreamSubscriber(event_stream=mock_stream)
        msg = _make_message(team_id=None)

        subscriber.on_message(msg)

        mock_stream.append.assert_not_called()

    def test_valid_team_id_forwards_message_to_local_stream(self) -> None:
        """End-to-end smoke against the real LocalEventStream."""
        stream = LocalEventStream()
        subscriber = EventStreamSubscriber(event_stream=stream)
        team_id = uuid.uuid4()
        msg = _make_message(team_id=team_id)

        subscriber.on_message(msg)

        events = stream.read_from(team_id)
        assert len(events) == 1
        assert events[0].id == msg.id
        assert events[0].team_id == team_id

    def test_multiple_messages_appended_in_order(self) -> None:
        """Messages are appended in order across multiple teams (no dedup)."""
        stream = LocalEventStream()
        subscriber = EventStreamSubscriber(event_stream=stream)
        team_a = uuid.uuid4()
        team_b = uuid.uuid4()

        # Interleave messages: A, B, A, B, A
        subscriber.on_message(_make_message(team_id=team_a))
        subscriber.on_message(_make_message(team_id=team_b))
        subscriber.on_message(_make_message(team_id=team_a))
        subscriber.on_message(_make_message(team_id=team_b))
        subscriber.on_message(_make_message(team_id=team_a))

        events_a = stream.read_from(team_a)
        events_b = stream.read_from(team_b)

        assert len(events_a) == 3
        assert len(events_b) == 2

    def test_forwarded_message_is_original(self) -> None:
        """Forwarded event is the original Message, not a wrapper."""
        stream = LocalEventStream()
        subscriber = EventStreamSubscriber(event_stream=stream)
        team_id = uuid.uuid4()

        msg1 = _make_message(team_id=team_id)
        msg2 = _make_message(team_id=team_id)
        subscriber.on_message(msg1)
        subscriber.on_message(msg2)

        events = stream.read_from(team_id)
        assert len(events) == 2
        assert events[0].id == msg1.id
        assert events[1].id == msg2.id


class TestOnStop:
    """Story 27.1 AC #10: ``on_stop(team_id)`` removes exactly one team's stream."""

    def test_on_stop_removes_only_supplied_team(self) -> None:
        """``on_stop(team_a)`` calls ``remove(team_a)`` once; team_b untouched."""
        mock_stream = MagicMock(spec=EventStream)
        subscriber = EventStreamSubscriber(event_stream=mock_stream)
        team_a = uuid.uuid4()

        subscriber.on_stop(team_a)

        mock_stream.remove.assert_called_once_with(team_a)

    def test_on_stop_does_not_touch_other_teams(self) -> None:
        """Pre-populating a LocalEventStream with two teams: on_stop(team_a) removes only team_a."""
        stream = LocalEventStream()
        subscriber = EventStreamSubscriber(event_stream=stream)
        team_a = uuid.uuid4()
        team_b = uuid.uuid4()
        subscriber.on_message(_make_message(team_id=team_a))
        subscriber.on_message(_make_message(team_id=team_b))

        subscriber.on_stop(team_a)

        # team_a stream is gone, team_b still has its event
        assert stream.read_from(team_a) == []
        assert len(stream.read_from(team_b)) == 1

    def test_on_stop_swallows_remove_exception(self) -> None:
        """If ``event_stream.remove`` raises, the exception is swallowed (logged)."""
        mock_stream = MagicMock(spec=EventStream)
        mock_stream.remove.side_effect = KeyError("stream not found")
        subscriber = EventStreamSubscriber(event_stream=mock_stream)
        team_a = uuid.uuid4()

        # Must not raise.
        subscriber.on_stop(team_a)

        mock_stream.remove.assert_called_once_with(team_a)


class TestSetRestoringNoOp:
    """Story 27.1 AC #11: ``set_restoring`` is a documented no-op on EventStreamSubscriber."""

    def test_set_restoring_is_noop(self) -> None:
        """Neither ``set_restoring(True)`` nor ``set_restoring(False)`` calls any stream method."""
        mock_stream = MagicMock(spec=EventStream)
        subscriber = EventStreamSubscriber(event_stream=mock_stream)
        team_a = uuid.uuid4()

        subscriber.set_restoring(team_a, True)
        subscriber.set_restoring(team_a, False)

        mock_stream.append.assert_not_called()
        mock_stream.remove.assert_not_called()
        mock_stream.read_from.assert_not_called()


class TestOnStopRequest:
    """Story 22.1 AC4: on_stop_request is a no-op — never raises, never mutates state."""

    def test_on_stop_request_returns_none_and_does_not_raise(self) -> None:
        """Direct invocation returns None without raising."""
        stream = LocalEventStream()
        subscriber = EventStreamSubscriber(event_stream=stream)

        result = subscriber.on_stop_request(_TEAM_ID)

        assert result is None

    def test_on_stop_request_does_not_touch_event_stream(self) -> None:
        """No-op contract: on_stop_request must not append, remove, or read from the stream."""
        mock_stream = MagicMock(spec=EventStream)
        subscriber = EventStreamSubscriber(event_stream=mock_stream)

        subscriber.on_stop_request(_TEAM_ID)

        mock_stream.append.assert_not_called()
        mock_stream.remove.assert_not_called()
        mock_stream.read_from.assert_not_called()
