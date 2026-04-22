"""Shared fixtures and helpers for CLI tests."""

from __future__ import annotations

import asyncio
import io
from typing import Any
from unittest.mock import AsyncMock, MagicMock, PropertyMock

from rich.console import Console

from akgentic.infra.cli.client import (
    EventInfo,
    TeamInfo,
    WorkspaceEntry,
    WorkspaceTreeInfo,
    WorkspaceUploadInfo,
)
from akgentic.infra.cli.connection import ConnectionState
from akgentic.infra.cli.formatters import OutputFormat
from akgentic.infra.cli.renderers import RichRenderer
from akgentic.infra.cli.repl import ChatSession


def mock_client(**overrides: Any) -> MagicMock:
    """Build a mock ApiClient with sensible defaults for CLI tests."""
    mock = MagicMock()
    mock.list_teams.return_value = [
        TeamInfo(
            team_id="t1",
            name="Team 1",
            status="running",
            user_id="u1",
            created_at="2025-01-01",
            updated_at="2025-01-01",
        ),
    ]
    mock.get_team.return_value = TeamInfo(
        team_id="t1",
        name="Team 1",
        status="running",
        user_id="u1",
        created_at="2025-01-01",
        updated_at="2025-01-02",
    )
    mock.create_team.return_value = TeamInfo(
        team_id="new",
        name="New Team",
        status="created",
        user_id="u1",
        created_at="2025-01-01",
        updated_at="2025-01-01",
    )
    mock.delete_team.return_value = None
    mock.restore_team.return_value = TeamInfo(
        team_id="t1",
        name="Team 1",
        status="running",
        user_id="u1",
        created_at="2025-01-01",
        updated_at="2025-01-03",
    )
    mock.get_events.return_value = [
        EventInfo(
            team_id="t1",
            sequence=1,
            timestamp="2025-01-01T00:00:00",
            event={"type": "started"},
        ),
    ]
    mock.send_message.return_value = None
    mock.human_input.return_value = None
    mock.workspace_tree.return_value = WorkspaceTreeInfo(
        team_id="t1",
        path="/",
        entries=[
            WorkspaceEntry(name="docs", is_dir=True, size=0),
            WorkspaceEntry(name="readme.md", is_dir=False, size=42),
        ],
    )
    mock.list_catalog_teams.return_value = []
    mock.workspace_read.return_value = b"file content"
    mock.workspace_upload.return_value = WorkspaceUploadInfo(path="readme.md", size=12)
    for k, v in overrides.items():
        setattr(mock, k, v)
    return mock


def mock_ws() -> AsyncMock:
    """Build a mock WsClient for CLI tests (legacy helper)."""
    ws = AsyncMock()
    ws.__aenter__ = AsyncMock(return_value=ws)
    ws.__aexit__ = AsyncMock(return_value=None)
    ws.receive_event = AsyncMock(side_effect=asyncio.CancelledError)
    ws.url = "ws://localhost:8000/ws/t1"
    ws.close = AsyncMock()
    ws.connect = AsyncMock(return_value=ws)
    return ws


def mock_conn(team_id: str = "t1") -> AsyncMock:
    """Build a mock ConnectionManager for CLI tests."""
    conn = AsyncMock()
    conn.__aenter__ = AsyncMock(return_value=conn)
    conn.__aexit__ = AsyncMock(return_value=None)
    conn.receive_event = AsyncMock(side_effect=asyncio.CancelledError)
    conn.close = AsyncMock()
    conn.connect = AsyncMock()
    conn.switch_team = AsyncMock()
    # Use PropertyMock for properties
    type(conn).state = PropertyMock(return_value=ConnectionState.CONNECTED)
    type(conn).team_id = PropertyMock(return_value=team_id)
    return conn


def captured_renderer() -> tuple[RichRenderer, io.StringIO]:
    """Build a RichRenderer that captures output to a StringIO buffer."""
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=True, width=120, no_color=True)
    return RichRenderer(console=console), buf


def make_session(
    client: MagicMock | None = None,
    conn: AsyncMock | None = None,
    team_id: str = "t1",
    renderer: RichRenderer | None = None,
) -> ChatSession:
    """Create a ChatSession with mocked dependencies."""
    if client is None:
        client = mock_client()
    if conn is None:
        conn = mock_conn(team_id)
    return ChatSession(
        client,
        conn,
        team_id,
        OutputFormat.table,
        server_url="http://localhost:8000",
        api_key="test-key",
        renderer=renderer,
    )
