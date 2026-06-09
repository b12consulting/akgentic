"""Worker-route human-input inner-id resolution tests (Epic 30 / ADR-027).

The worker ``_find_message`` resolves a persisted ``SentMessage`` by its
**inner** ``message.id`` (the id every distributed tier puts on the wire),
not the outer envelope ``SentMessage.id``. These tests pin that behavior:
inner id resolves and routes to the handle; outer id is not found.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from akgentic.team.models import PersistedEvent
from fastapi import HTTPException

from akgentic.infra.server.models import HumanInputRequest
from akgentic.infra.worker.routes.teams import _find_message, human_input
from tests.fixtures.events import build_sent_message


def _persisted(sent: object, team_id: uuid.UUID, sequence: int = 1) -> PersistedEvent:
    """Wrap an event in a PersistedEvent envelope for a team."""
    return PersistedEvent(
        team_id=team_id,
        sequence=sequence,
        event=sent,  # type: ignore[arg-type]
        timestamp=datetime.now(UTC),
    )


class _FakeEventStore:
    """Minimal EventStore stub exposing only ``load_events``."""

    def __init__(self, events: list[PersistedEvent]) -> None:
        self._events = events

    def load_events(self, team_id: uuid.UUID) -> list[PersistedEvent]:
        return [e for e in self._events if e.team_id == team_id]


def test_find_message_resolves_by_inner_id() -> None:
    """_find_message returns the SentMessage matching the inner message.id."""
    team_id = uuid.uuid4()
    sent = build_sent_message(content="please confirm")
    # The inner and outer ids must differ for this to be a meaningful test.
    assert sent.id != sent.message.id
    store = _FakeEventStore([_persisted(sent, team_id)])

    resolved = _find_message(store, team_id, str(sent.message.id))

    assert resolved is sent
    assert resolved.message.id == sent.message.id


def test_find_message_outer_id_not_found() -> None:
    """_find_message raises 'not found' when given the outer envelope id."""
    team_id = uuid.uuid4()
    sent = build_sent_message(content="please confirm")
    store = _FakeEventStore([_persisted(sent, team_id)])

    with pytest.raises(ValueError, match="not found"):
        _find_message(store, team_id, str(sent.id))


def test_find_message_ignores_non_sent_message() -> None:
    """_find_message skips non-SentMessage events even on an id match."""
    team_id = uuid.uuid4()
    inner = build_sent_message().message
    # An inner UserMessage persisted on its own is not a SentMessage envelope.
    store = _FakeEventStore([_persisted(inner, team_id)])

    with pytest.raises(ValueError, match="not found"):
        _find_message(store, team_id, str(inner.id))


def test_human_input_route_routes_inner_id_to_handle() -> None:
    """human_input route resolves the inner id and routes to the handle."""
    team_id = uuid.uuid4()
    sent = build_sent_message(content="please confirm")
    store = _FakeEventStore([_persisted(sent, team_id)])

    routed: list[tuple[str, object]] = []
    handle = SimpleNamespace(
        process_human_input=lambda content, message: routed.append((content, message))
    )
    services = SimpleNamespace(
        event_store=store,
        runtime_cache=SimpleNamespace(get=lambda _tid: handle),
    )

    body = HumanInputRequest(content="yes", message_id=str(sent.message.id))
    human_input(team_id, body, services)  # type: ignore[arg-type]

    assert routed == [("yes", sent.message)]


def test_human_input_route_outer_id_returns_404() -> None:
    """human_input route raises HTTP 404 when given the outer envelope id."""
    team_id = uuid.uuid4()
    sent = build_sent_message(content="please confirm")
    store = _FakeEventStore([_persisted(sent, team_id)])

    handle = SimpleNamespace(process_human_input=lambda content, message: None)
    services = SimpleNamespace(
        event_store=store,
        runtime_cache=SimpleNamespace(get=lambda _tid: handle),
    )

    body = HumanInputRequest(content="yes", message_id=str(sent.id))
    with pytest.raises(HTTPException) as exc_info:
        human_input(team_id, body, services)  # type: ignore[arg-type]

    assert exc_info.value.status_code == 404
    assert "not found" in exc_info.value.detail
