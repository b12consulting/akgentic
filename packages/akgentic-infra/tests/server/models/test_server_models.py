"""Tests for REST API request/response models."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from akgentic.infra.server.models import (
    CreateTeamRequest,
    EventListResponse,
    EventResponse,
    HumanInputRequest,
    SendMessageRequest,
    TeamListResponse,
    TeamResponse,
)


def test_create_team_request_minimal() -> None:
    """CreateTeamRequest requires only catalog_entry_id."""
    req = CreateTeamRequest(catalog_entry_id="test-team")
    assert req.catalog_entry_id == "test-team"
    assert req.params == {}


def test_create_team_request_with_params() -> None:
    """CreateTeamRequest accepts optional params."""
    req = CreateTeamRequest(
        catalog_entry_id="test-team",
        params={"key": "value"},
    )
    assert req.params == {"key": "value"}


def test_team_response_serialization() -> None:
    """TeamResponse serializes all fields correctly."""
    tid = uuid.uuid4()
    now = datetime.now(tz=UTC)
    resp = TeamResponse(
        team_id=tid,
        name="Test",
        status="running",
        user_id="anonymous",
        created_at=now,
        updated_at=now,
    )
    data = resp.model_dump(mode="json")
    assert data["team_id"] == str(tid)
    assert data["status"] == "running"


def test_team_list_response_empty() -> None:
    """TeamListResponse can hold an empty list."""
    resp = TeamListResponse(teams=[])
    assert resp.teams == []


def test_team_list_response_with_items() -> None:
    """TeamListResponse serializes a list of TeamResponses."""
    tid = uuid.uuid4()
    now = datetime.now(tz=UTC)
    item = TeamResponse(
        team_id=tid,
        name="A",
        status="running",
        user_id="u",
        created_at=now,
        updated_at=now,
    )
    resp = TeamListResponse(teams=[item])
    assert len(resp.teams) == 1
    assert resp.teams[0].team_id == tid


def test_send_message_request() -> None:
    """SendMessageRequest requires content."""
    req = SendMessageRequest(content="hello")
    assert req.content == "hello"


def test_human_input_request() -> None:
    """HumanInputRequest requires content and message_id."""
    req = HumanInputRequest(content="yes", message_id="msg-123")
    assert req.content == "yes"
    assert req.message_id == "msg-123"


def test_event_response_serialization() -> None:
    """EventResponse serializes all fields correctly."""
    tid = uuid.uuid4()
    now = datetime.now(tz=UTC)
    resp = EventResponse(
        team_id=tid,
        sequence=1,
        event={"type": "UserMessage", "content": "hello"},
        timestamp=now,
    )
    data = resp.model_dump(mode="json")
    assert data["team_id"] == str(tid)
    assert data["sequence"] == 1
    assert data["event"]["type"] == "UserMessage"


def test_event_list_response_empty() -> None:
    """EventListResponse can hold an empty list."""
    resp = EventListResponse(events=[])
    assert resp.events == []


def test_event_list_response_with_items() -> None:
    """EventListResponse serializes a list of EventResponses."""
    tid = uuid.uuid4()
    now = datetime.now(tz=UTC)
    item = EventResponse(
        team_id=tid,
        sequence=0,
        event={"type": "test"},
        timestamp=now,
    )
    resp = EventListResponse(events=[item])
    assert len(resp.events) == 1
    assert resp.events[0].team_id == tid
