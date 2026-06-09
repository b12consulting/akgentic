"""Guarding tests for the angular_v1 ``process_human_input`` inner-id alignment.

Story 30-2: the v1 message history (``_events_to_v1_messages``) exposes the
**outer** ``SentMessage.id`` (the event-envelope id), so the v1 modal posts that
id back to ``POST /process_human_input/{id}/human/{proxy}``. Since Story 30-1 the
gateway resolves human input by the **inner** ``SentMessage.message.id``, so the
route translates the posted outer id to the inner id server-side before calling
``TeamService.process_human_input``. These tests lock that behavior in: an outer
id resolves (routes the inner Message to the cached handle / 200), an unknown id
404s, and an id that is already the inner id still resolves (pass-through).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from akgentic.core.messages.orchestrator import SentMessage
from akgentic.team.models import PersistedEvent
from fastapi import HTTPException

from akgentic.infra.server.routes.frontend_adapter.angular_v1.router import (
    V1HumanInputBody,
    _resolve_inner_message_id,
    process_human_input,
)
from akgentic.infra.server.services.team_service import TeamService
from tests.fixtures.events import build_sent_message


def _seed_sent_message(team_service: TeamService, team_id: uuid.UUID) -> SentMessage:
    """Persist a SentMessage (inner id != outer id) into a team's event store."""
    sent = build_sent_message(content="please confirm")
    # The translation under test is only meaningful when inner != outer.
    assert sent.id != sent.message.id
    team_service._services.event_store.save_event(
        PersistedEvent(
            team_id=team_id,
            sequence=1,
            event=sent,
            timestamp=datetime.now(UTC),
        )
    )
    return sent


def test_resolve_inner_message_id_maps_outer_to_inner(team_service: TeamService) -> None:
    """A posted outer envelope id is translated to the inner message id."""
    process = team_service.create_team("test-team", user_id="anonymous")
    sent = _seed_sent_message(team_service, process.team_id)

    resolved = _resolve_inner_message_id(team_service, process.team_id, str(sent.id))

    assert resolved == str(sent.message.id)


def test_resolve_inner_message_id_passes_through_inner(team_service: TeamService) -> None:
    """An id that is already the inner id is returned unchanged (pass-through)."""
    process = team_service.create_team("test-team", user_id="anonymous")
    sent = _seed_sent_message(team_service, process.team_id)

    resolved = _resolve_inner_message_id(
        team_service, process.team_id, str(sent.message.id)
    )

    assert resolved == str(sent.message.id)


def test_resolve_inner_message_id_passes_through_unknown(team_service: TeamService) -> None:
    """An unknown id is returned unchanged so the downstream lookup 404s."""
    process = team_service.create_team("test-team", user_id="anonymous")
    _seed_sent_message(team_service, process.team_id)

    resolved = _resolve_inner_message_id(team_service, process.team_id, "nonexistent")

    assert resolved == "nonexistent"


def test_process_human_input_outer_id_routes_inner(team_service: TeamService) -> None:
    """POSTing the outer envelope id routes the inner Message to the handle (200)."""
    process = team_service.create_team("test-team", user_id="anonymous")
    sent = _seed_sent_message(team_service, process.team_id)

    routed: list[tuple[str, object]] = []
    handle = team_service.get_handle(process.team_id)
    assert handle is not None
    handle.process_human_input = (  # type: ignore[method-assign]
        lambda content, message: routed.append((content, message))
    )

    body = V1HumanInputBody(
        content="yes",
        message={"id": str(sent.id), "content": "original"},
    )
    resp = process_human_input(
        id=str(process.team_id), proxy="@Human", body=body, service=team_service
    )

    assert resp.status == "ok"
    # The inner Message — not the outer SentMessage — reaches the handle.
    assert routed == [("yes", sent.message)]


def test_process_human_input_inner_id_routes_inner(team_service: TeamService) -> None:
    """POSTing the inner id directly also resolves and routes the inner Message."""
    process = team_service.create_team("test-team", user_id="anonymous")
    sent = _seed_sent_message(team_service, process.team_id)

    routed: list[tuple[str, object]] = []
    handle = team_service.get_handle(process.team_id)
    assert handle is not None
    handle.process_human_input = (  # type: ignore[method-assign]
        lambda content, message: routed.append((content, message))
    )

    body = V1HumanInputBody(
        content="yes",
        message={"id": str(sent.message.id), "content": "original"},
    )
    resp = process_human_input(
        id=str(process.team_id), proxy="@Human", body=body, service=team_service
    )

    assert resp.status == "ok"
    assert routed == [("yes", sent.message)]


def test_process_human_input_unknown_id_404(team_service: TeamService) -> None:
    """An id matching no SentMessage maps to HTTP 404 (not-found)."""
    process = team_service.create_team("test-team", user_id="anonymous")
    _seed_sent_message(team_service, process.team_id)

    body = V1HumanInputBody(
        content="yes",
        message={"id": str(uuid.uuid4()), "content": "original"},
    )
    with pytest.raises(HTTPException) as exc_info:
        process_human_input(
            id=str(process.team_id), proxy="@Human", body=body, service=team_service
        )
    assert exc_info.value.status_code == 404


def test_process_human_input_missing_id_422(team_service: TeamService) -> None:
    """A body whose message lacks an 'id' field maps to HTTP 422."""
    process = team_service.create_team("test-team", user_id="anonymous")

    body = V1HumanInputBody(content="yes", message={"content": "original"})
    with pytest.raises(HTTPException) as exc_info:
        process_human_input(
            id=str(process.team_id), proxy="@Human", body=body, service=team_service
        )
    assert exc_info.value.status_code == 422
