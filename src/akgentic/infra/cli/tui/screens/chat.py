"""Main chat layout with four docked zones."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import VerticalScroll
from textual.screen import Screen
from textual.widgets import Static

from akgentic.infra.cli.tui.widgets.chat_input import ChatInput
from akgentic.infra.cli.tui.widgets.hint_bar import HintBar
from akgentic.infra.cli.tui.widgets.status_header import StatusHeader


class ChatScreen(Screen[None]):
    """Main chat layout with four docked zones."""

    CSS_PATH = "../styles/chat.tcss"

    def __init__(
        self,
        team_name: str = "(no team)",
        team_id: str = "",
        team_status: str = "running",
    ) -> None:
        super().__init__()
        self._team_name = team_name
        self._team_id = team_id
        self._team_status = team_status

    def compose(self) -> ComposeResult:
        """Compose the four-zone layout."""
        yield StatusHeader(
            team_name=self._team_name,
            team_id=self._team_id,
            team_status=self._team_status,
        )
        yield VerticalScroll(
            Static(
                "Welcome to Akgentic Chat. Send a message to begin.",
                id="welcome",
            ),
            id="conversation",
        )
        yield ChatInput()
        yield HintBar()
