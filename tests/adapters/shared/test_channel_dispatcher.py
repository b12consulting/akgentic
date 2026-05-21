"""Tests for InteractionChannelDispatcher adapter."""

from __future__ import annotations

import uuid

import pytest
from akgentic.core.actor_address_impl import ActorAddressProxy
from akgentic.core.messages import Message
from akgentic.core.messages.orchestrator import ReceivedMessage, SentMessage, StartMessage
from akgentic.core.orchestrator import EventSubscriber

from akgentic.infra.adapters.shared.channel_dispatcher import InteractionChannelDispatcher

# ---------------------------------------------------------------------------
# Stub adapters satisfying InteractionChannelAdapter protocol (structural)
# ---------------------------------------------------------------------------


class _MatchingAdapter:
    """Adapter stub that always matches."""

    def __init__(self) -> None:
        self.matches_called = False
        self.deliver_called = False
        self.stop_called = False
        self.stop_team_id: uuid.UUID | None = None
        self.matches_msg: SentMessage | None = None
        self.deliver_msg: SentMessage | None = None

    def matches(self, msg: SentMessage) -> bool:
        self.matches_called = True
        self.matches_msg = msg
        return True

    def deliver(self, msg: SentMessage) -> None:
        self.deliver_called = True
        self.deliver_msg = msg

    def on_stop(self, team_id: uuid.UUID) -> None:
        self.stop_called = True
        self.stop_team_id = team_id


class _NonMatchingAdapter:
    """Adapter stub that never matches."""

    def __init__(self) -> None:
        self.matches_called = False
        self.deliver_called = False
        self.stop_called = False
        self.stop_team_id: uuid.UUID | None = None

    def matches(self, msg: SentMessage) -> bool:
        self.matches_called = True
        return False

    def deliver(self, msg: SentMessage) -> None:
        self.deliver_called = True

    def on_stop(self, team_id: uuid.UUID) -> None:
        self.stop_called = True
        self.stop_team_id = team_id


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_addr() -> ActorAddressProxy:
    return ActorAddressProxy(
        {
            "__actor_address__": True,
            "__actor_type__": "akgentic.core.actor_address_impl.ActorAddressProxy",
            "agent_id": str(uuid.uuid4()),
            "name": "test-agent",
            "role": "tester",
            "team_id": str(uuid.uuid4()),
            "squad_id": str(uuid.uuid4()),
            "user_message": False,
        }
    )


def _make_sent_message() -> SentMessage:
    addr = _make_addr()
    inner = Message(sender=addr)
    return SentMessage(message=inner, recipient=addr, sender=addr)


TEAM_ID = uuid.uuid4()


# ---------------------------------------------------------------------------
# AC #1: matches() then deliver() on matching adapter
# ---------------------------------------------------------------------------


class TestDispatchToMatchingAdapter:
    """AC #1: Dispatcher calls matches() then deliver() on matching adapters."""

    def test_calls_matches_then_deliver(self) -> None:
        adapter = _MatchingAdapter()
        dispatcher = InteractionChannelDispatcher(team_id=TEAM_ID, adapters=[adapter])
        sent = _make_sent_message()
        dispatcher.on_message(sent)

        assert adapter.matches_called
        assert adapter.deliver_called
        assert adapter.matches_msg is sent
        assert adapter.deliver_msg is sent


# ---------------------------------------------------------------------------
# AC #2: non-SentMessage events are skipped
# ---------------------------------------------------------------------------


class TestSkipsNonSentMessage:
    """Dispatcher ignores non-SentMessage events."""

    def test_received_message_ignored(self) -> None:
        adapter = _MatchingAdapter()
        dispatcher = InteractionChannelDispatcher(team_id=TEAM_ID, adapters=[adapter])
        dispatcher.on_message(ReceivedMessage(message_id=uuid.uuid4()))

        assert not adapter.matches_called
        assert not adapter.deliver_called

    def test_start_message_ignored(self) -> None:
        from akgentic.core.agent_config import BaseConfig

        adapter = _MatchingAdapter()
        dispatcher = InteractionChannelDispatcher(team_id=TEAM_ID, adapters=[adapter])
        dispatcher.on_message(StartMessage(config=BaseConfig()))

        assert not adapter.matches_called
        assert not adapter.deliver_called


# ---------------------------------------------------------------------------
# AC #2: no adapter matches → silently skip
# ---------------------------------------------------------------------------


class TestNoAdapterMatch:
    """When no adapter matches, message is silently skipped."""

    def test_no_match_no_exception(self) -> None:
        adapter = _NonMatchingAdapter()
        dispatcher = InteractionChannelDispatcher(team_id=TEAM_ID, adapters=[adapter])
        dispatcher.on_message(_make_sent_message())

        assert adapter.matches_called
        assert not adapter.deliver_called


# ---------------------------------------------------------------------------
# AC #2: multi-channel delivery — ALL matching adapters get deliver()
# ---------------------------------------------------------------------------


class TestMultiChannelDelivery:
    """With multiple adapters, ALL matching adapters receive deliver()."""

    def test_all_matching_adapters_deliver(self) -> None:
        first = _MatchingAdapter()
        second = _MatchingAdapter()
        dispatcher = InteractionChannelDispatcher(team_id=TEAM_ID, adapters=[first, second])
        sent = _make_sent_message()
        dispatcher.on_message(sent)

        assert first.matches_called
        assert first.deliver_called
        assert first.deliver_msg is sent
        assert second.matches_called
        assert second.deliver_called
        assert second.deliver_msg is sent

    def test_skips_non_matching_then_delivers_to_match(self) -> None:
        non_match = _NonMatchingAdapter()
        match = _MatchingAdapter()
        dispatcher = InteractionChannelDispatcher(team_id=TEAM_ID, adapters=[non_match, match])
        dispatcher.on_message(_make_sent_message())

        assert non_match.matches_called
        assert not non_match.deliver_called
        assert match.matches_called
        assert match.deliver_called

    def test_delivers_to_multiple_with_non_matching_in_between(self) -> None:
        first = _MatchingAdapter()
        non_match = _NonMatchingAdapter()
        second = _MatchingAdapter()
        dispatcher = InteractionChannelDispatcher(
            team_id=TEAM_ID, adapters=[first, non_match, second]
        )
        sent = _make_sent_message()
        dispatcher.on_message(sent)

        assert first.deliver_called
        assert first.deliver_msg is sent
        assert non_match.matches_called
        assert not non_match.deliver_called
        assert second.deliver_called
        assert second.deliver_msg is sent


# ---------------------------------------------------------------------------
# AC #3: set_restoring(True) causes all events to be skipped
# ---------------------------------------------------------------------------


class TestRestoreMode:
    """set_restoring suppresses delivery during event replay."""

    def test_restoring_skips_all_events(self) -> None:
        adapter = _MatchingAdapter()
        dispatcher = InteractionChannelDispatcher(team_id=TEAM_ID, adapters=[adapter])
        dispatcher.set_restoring(TEAM_ID, True)
        dispatcher.on_message(_make_sent_message())

        assert not adapter.matches_called
        assert not adapter.deliver_called

    def test_restoring_false_resumes_dispatch(self) -> None:
        adapter = _MatchingAdapter()
        dispatcher = InteractionChannelDispatcher(team_id=TEAM_ID, adapters=[adapter])
        dispatcher.set_restoring(TEAM_ID, True)
        dispatcher.on_message(_make_sent_message())
        assert not adapter.deliver_called

        dispatcher.set_restoring(TEAM_ID, False)
        dispatcher.on_message(_make_sent_message())
        assert adapter.deliver_called


# ---------------------------------------------------------------------------
# AC #4: on_stop() calls adapter.on_stop(team_id) on ALL adapters
# ---------------------------------------------------------------------------


class TestOnStop:
    """on_stop() propagates to all registered adapters."""

    def test_on_stop_calls_all_adapters(self) -> None:
        a1 = _MatchingAdapter()
        a2 = _NonMatchingAdapter()
        dispatcher = InteractionChannelDispatcher(team_id=TEAM_ID, adapters=[a1, a2])
        dispatcher.on_stop(TEAM_ID)

        assert a1.stop_called
        assert a1.stop_team_id == TEAM_ID
        assert a2.stop_called
        assert a2.stop_team_id == TEAM_ID

    def test_on_stop_empty_adapter_list(self) -> None:
        dispatcher = InteractionChannelDispatcher(team_id=TEAM_ID, adapters=[])
        dispatcher.on_stop(TEAM_ID)  # should not raise


# ---------------------------------------------------------------------------
# Edge case: empty adapter list with SentMessage
# ---------------------------------------------------------------------------


class TestEmptyAdapterList:
    """Dispatcher with empty adapter list handles messages without error."""

    def test_sent_message_with_no_adapters(self) -> None:
        dispatcher = InteractionChannelDispatcher(team_id=TEAM_ID, adapters=[])
        dispatcher.on_message(_make_sent_message())  # should not raise


# ---------------------------------------------------------------------------
# Reclassified from integration/test_spec_channels.py — TestMultiAdapterDispatch
# Direct dispatcher construction with stubs; no real app needed.
# ---------------------------------------------------------------------------


class TestMultiAdapterDispatch:
    """Verify dispatcher delivers to ALL matching adapters (no break short-circuit)."""

    def test_two_adapters_both_receive_message(self) -> None:
        """Two StubChannelAdapters both receive the dispatched SentMessage."""
        adapter_a = _MatchingAdapter()
        adapter_b = _MatchingAdapter()
        dispatcher = InteractionChannelDispatcher(
            team_id=TEAM_ID,
            adapters=[adapter_a, adapter_b],
        )

        sent = _make_sent_message()
        dispatcher.on_message(sent)

        assert adapter_a.deliver_called, "Adapter A should receive the message"
        assert adapter_b.deliver_called, "Adapter B should receive the message"
        assert adapter_a.deliver_msg is sent
        assert adapter_b.deliver_msg is sent


# ---------------------------------------------------------------------------
# Story 27.1 AC #3: dispatcher asserts team_id equality on lifecycle entries
# ---------------------------------------------------------------------------


class TestOnStopAssertsTeamId:
    """on_stop and set_restoring raise ``AssertionError`` on team_id mismatch.

    The dispatcher is per-team; the orchestrator MUST pass the matching
    ``team_id`` on every lifecycle call. A mismatch indicates a wiring bug.
    """

    def test_on_stop_with_mismatched_team_id_raises(self) -> None:
        adapter = _MatchingAdapter()
        dispatcher = InteractionChannelDispatcher(team_id=TEAM_ID, adapters=[adapter])

        with pytest.raises(AssertionError):
            dispatcher.on_stop(uuid.uuid4())

        assert not adapter.stop_called

    def test_set_restoring_with_mismatched_team_id_raises(self) -> None:
        adapter = _MatchingAdapter()
        dispatcher = InteractionChannelDispatcher(team_id=TEAM_ID, adapters=[adapter])

        with pytest.raises(AssertionError):
            dispatcher.set_restoring(uuid.uuid4(), True)

        # _restoring must remain False (assertion failed before assignment).
        assert dispatcher._restoring is False


class TestOnStopRequest:
    """Story 27.1 AC #3: on_stop_request is a no-op for Protocol compliance.

    The dispatcher has no work to perform on the inactivity-timer signal; all
    channel-side teardown happens on on_stop.
    """

    def test_on_stop_request_returns_none(self) -> None:
        adapter = _MatchingAdapter()
        dispatcher = InteractionChannelDispatcher(team_id=TEAM_ID, adapters=[adapter])

        result = dispatcher.on_stop_request(TEAM_ID)

        assert result is None

    def test_on_stop_request_does_not_call_adapter_methods(self) -> None:
        adapter = _MatchingAdapter()
        dispatcher = InteractionChannelDispatcher(team_id=TEAM_ID, adapters=[adapter])

        dispatcher.on_stop_request(TEAM_ID)

        assert not adapter.stop_called
        assert not adapter.matches_called
        assert not adapter.deliver_called

    def test_on_stop_request_does_not_mutate_restoring(self) -> None:
        adapter = _MatchingAdapter()
        dispatcher = InteractionChannelDispatcher(team_id=TEAM_ID, adapters=[adapter])

        dispatcher.on_stop_request(TEAM_ID)

        assert dispatcher._restoring is False


class TestProtocolCompliance:
    """Story 27.1 AC #4: InteractionChannelDispatcher structurally satisfies EventSubscriber."""

    def test_satisfies_event_subscriber_protocol(self) -> None:
        dispatcher: EventSubscriber = InteractionChannelDispatcher(team_id=TEAM_ID, adapters=[])
        assert callable(dispatcher.set_restoring)
        assert callable(dispatcher.on_stop_request)
        assert callable(dispatcher.on_stop)
        assert callable(dispatcher.on_message)
