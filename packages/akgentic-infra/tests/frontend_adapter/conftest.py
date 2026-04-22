"""Shared fixtures and helpers for frontend adapter tests."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import MagicMock

from akgentic.team.models import Process, TeamCard, TeamStatus

_NOW = datetime(2026, 3, 28, 12, 0, 0, tzinfo=UTC)
_TEAM_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")


def make_team_card(name: str = "Test Team") -> MagicMock:
    """Create a minimal TeamCard mock with entry_point for V1 translation."""
    entry_point = MagicMock()
    entry_point.card.config.name = "@Orchestrator"
    entry_point.card.role = "orchestrator"
    card = MagicMock(spec=TeamCard)
    card.name = name
    card.entry_point = entry_point
    return card


def make_process(
    team_id: uuid.UUID = _TEAM_ID,
    status: TeamStatus = TeamStatus.RUNNING,
    name: str = "Test Team",
) -> Process:
    """Create a Process fixture."""
    team_card = make_team_card(name)
    return Process(
        team_id=team_id,
        team_card=team_card,
        status=status,
        user_id="anonymous",
        created_at=_NOW,
        updated_at=_NOW,
    )
