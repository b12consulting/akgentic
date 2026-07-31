"""Shared fixtures for server tests.

Server-level fixtures (app, client, team_service, etc.) are provided by
the root tests/conftest.py and are automatically available to all subdirectories.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from akgentic.core.messages.message import UserMessage
from akgentic.team.models import PersistedEvent
from akgentic.team.ports import EventStore


def append_synthetic_events(
    store: EventStore,
    team_id: uuid.UUID,
    count: int,
    start_sequence: int = 1000,
) -> list[PersistedEvent]:
    """Append ``count`` events to a team's log and return them in sequence order.

    ``start_sequence`` sits far above anything the live PersistenceSubscriber
    writes, so these events are always the log's tail regardless of what the
    running team persists concurrently.
    """
    events = []
    for offset in range(count):
        event = PersistedEvent(
            team_id=team_id,
            sequence=start_sequence + offset,
            event=UserMessage(content=f"synthetic-{offset}"),
            timestamp=datetime.now(UTC),
        )
        store.save_event(event)
        events.append(event)
    return events
