"""Persistent top bar showing team info and connection state."""

from __future__ import annotations

from rich.console import RenderableType
from rich.text import Text
from textual.widgets import Static


class StatusHeader(Static):
    """Persistent top bar showing team info and connection state."""

    DEFAULT_CSS = """
    StatusHeader {
        dock: top;
        height: 1;
        background: $surface;
        padding: 0 1;
    }
    """

    def __init__(
        self,
        team_name: str = "(no team)",
        team_id: str = "",
        team_status: str = "running",
        connection_state: str = "connected",
    ) -> None:
        super().__init__()
        self._team_name = team_name
        self._team_id = team_id
        self._team_status = team_status
        self._connection_state = connection_state

    def render(self) -> RenderableType:
        """Render team info and connection state as a single-line bar."""
        status_icon = "\u25b6" if self._team_status == "running" else "\u23f8"
        status_colors = {"running": "green", "stopped": "yellow", "deleted": "red"}
        status_style = status_colors.get(self._team_status, "dim")
        conn_indicators: dict[str, tuple[str, str]] = {
            "connected": ("\u25cf", "green"),
            "disconnected": ("\u2716", "red"),
            "reconnecting": ("\u27f3", "yellow"),
            "connecting": ("\u27f3", "yellow"),
        }
        icon, color = conn_indicators.get(self._connection_state, ("?", "white"))
        sep = " \u2502 "
        parts = Text()
        parts.append(f"  {self._team_name}", style="bold")
        parts.append(sep, style="dim")
        parts.append(self._team_id, style="dim")
        parts.append(sep, style="dim")
        parts.append(f"{status_icon} {self._team_status}", style=status_style)
        parts.append(sep, style="dim")
        parts.append(f"{icon} {self._connection_state}", style=color)
        return parts

    def update_connection(self, state: str) -> None:
        """Update connection state and re-render."""
        self._connection_state = state
        self.refresh()

    def update_team(
        self,
        team_name: str,
        team_id: str,
        team_status: str,
    ) -> None:
        """Update team info and re-render."""
        self._team_name = team_name
        self._team_id = team_id
        self._team_status = team_status
        self.refresh()
