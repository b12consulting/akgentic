"""Agent message widget for conversation display."""

from __future__ import annotations

from rich.console import Group, RenderableType
from rich.markdown import Markdown
from rich.text import Text
from textual.widgets import Static


class AgentMessage(Static):
    """A single agent message in the conversation."""

    DEFAULT_CSS = """
    AgentMessage {
        margin: 0 0 1 0;
        padding: 0 1;
    }
    """

    def __init__(
        self,
        sender: str,
        content: str,
        color: str,
        timestamp: str | None = None,
        recipient: str | None = None,
    ) -> None:
        self._sender = sender
        self._content = content
        self._color = color
        self._timestamp = timestamp
        self._recipient = recipient
        super().__init__()

    def on_mount(self) -> None:
        """Apply colored left border matching the agent's assigned color."""
        self.styles.border_left = ("tall", self._color)

    def render(self) -> RenderableType:
        """Render sender name with color, optional recipient, timestamp, and markdown body."""
        name = self._sender.lstrip("@")
        header = Text(f"[@{name}]", style=f"bold {self._color}")
        if self._recipient:
            recipient_name = self._recipient.lstrip("@")
            header.append(" \u2192 ", style="dim")
            header.append(f"[@{recipient_name}]", style="bold")
        if self._timestamp:
            header.append(f"  {self._timestamp}", style="dim")
        body = Markdown(self._content)
        return Group(header, body)

    def on_click(self) -> None:
        """Copy message content to clipboard on click."""
        self.app.copy_to_clipboard(self._content)
        self.app.notify("Copied to clipboard", timeout=2)
