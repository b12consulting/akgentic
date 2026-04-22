"""Shared fixture factories for CLI/server response models.

Each factory creates a real Pydantic model instance, then returns
``model.model_dump()``. This guarantees the dict shape always matches the
real serialization contract.

Usage::

    from tests.fixtures.models import make_team_info

    def test_something():
        team = make_team_info(name="my-team")
        # team is a plain dict matching TeamInfo.model_dump()
"""

from __future__ import annotations

from typing import Any

from akgentic.infra.cli.client import EventInfo, TeamInfo

# ---------------------------------------------------------------------------
# CLI model factories
# ---------------------------------------------------------------------------


def make_team_info(**overrides: Any) -> dict[str, Any]:
    """Create a ``TeamInfo`` fixture dict from a real model instance."""
    defaults: dict[str, Any] = {
        "team_id": "team-001",
        "name": "Test Team",
        "status": "running",
        "user_id": "user-001",
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-01T00:00:00Z",
    }
    defaults.update(overrides)
    return TeamInfo(**defaults).model_dump()


def make_event_info(**overrides: Any) -> dict[str, Any]:
    """Create an ``EventInfo`` fixture dict from a real model instance."""
    defaults: dict[str, Any] = {
        "team_id": "team-001",
        "sequence": 1,
        "event": {"__model__": "SentMessage", "content": "test"},
        "timestamp": "2026-01-01T00:00:00Z",
    }
    defaults.update(overrides)
    return EventInfo(**defaults).model_dump()
