"""Tests for LocalIngestion — community-tier InteractionChannelIngestion."""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock

import pytest

from akgentic.infra.adapters.community.local_ingestion import LocalIngestion
from akgentic.infra.protocols.channels import InteractionChannelIngestion
from akgentic.infra.server.services.team_service import TeamService


def _make_mock_service() -> MagicMock:
    """Create a MagicMock TeamService with predictable return values."""
    mock = MagicMock(spec=TeamService)
    return mock


def _make_process_stub(team_id: uuid.UUID) -> MagicMock:
    """Create a stub Process with the given team_id."""
    process = MagicMock()
    process.team_id = team_id
    return process


async def test_local_ingestion_satisfies_protocol() -> None:
    """LocalIngestion structurally satisfies InteractionChannelIngestion."""
    mock_service = _make_mock_service()
    ingestion = LocalIngestion(mock_service)
    assert isinstance(ingestion, InteractionChannelIngestion)


async def test_route_reply_calls_send_message() -> None:
    """route_reply() delegates to team_service.send_message()."""
    mock_service = _make_mock_service()
    ingestion = LocalIngestion(mock_service)
    team_id = uuid.uuid4()

    await ingestion.route_reply(team_id, "hello team")

    mock_service.send_message.assert_called_once_with(team_id, "hello team")


async def test_route_reply_with_original_message_id() -> None:
    """route_reply() with original_message_id still calls send_message."""
    mock_service = _make_mock_service()
    ingestion = LocalIngestion(mock_service)
    team_id = uuid.uuid4()

    await ingestion.route_reply(team_id, "threaded reply", original_message_id="msg-123")

    mock_service.send_message.assert_called_once_with(team_id, "threaded reply")


async def test_initiate_team_creates_and_sends() -> None:
    """initiate_team() calls create_team then send_message, returns new team_id."""
    mock_service = _make_mock_service()
    new_team_id = uuid.uuid4()
    mock_service.create_team.return_value = _make_process_stub(new_team_id)
    ingestion = LocalIngestion(mock_service)

    result = await ingestion.initiate_team("first message", "user-42", "catalog-entry-1")

    mock_service.create_team.assert_called_once_with("catalog-entry-1", user_id="user-42")
    mock_service.send_message.assert_called_once_with(new_team_id, "first message")
    assert result == new_team_id


async def test_initiate_team_returns_correct_uuid() -> None:
    """initiate_team() returns the UUID from the created process."""
    mock_service = _make_mock_service()
    expected_id = uuid.uuid4()
    mock_service.create_team.return_value = _make_process_stub(expected_id)
    ingestion = LocalIngestion(mock_service)

    result = await ingestion.initiate_team("msg", "user-1", "entry-1")

    assert result == expected_id
    assert isinstance(result, uuid.UUID)


async def test_route_reply_propagates_value_error() -> None:
    """route_reply() propagates ValueError from team_service.send_message()."""
    mock_service = _make_mock_service()
    mock_service.send_message.side_effect = ValueError("Team not found")
    ingestion = LocalIngestion(mock_service)
    team_id = uuid.uuid4()

    with pytest.raises(ValueError, match="Team not found"):
        await ingestion.route_reply(team_id, "hello")
