"""Custom Textual messages for connection state propagation."""

from __future__ import annotations

from textual.message import Message

from akgentic.infra.cli.connection import ConnectionState


class ConnectionStateChanged(Message):
    """Posted when ConnectionManager state changes."""

    def __init__(self, state: ConnectionState) -> None:
        self.state = state
        super().__init__()
