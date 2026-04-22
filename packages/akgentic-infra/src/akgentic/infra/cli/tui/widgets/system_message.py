"""System message and history separator widgets for conversation display."""

from __future__ import annotations

from rich.console import RenderableType
from rich.rule import Rule
from rich.text import Text
from textual.widgets import Static


class SystemMessage(Static):
    """Dim status text for system events."""

    DEFAULT_CSS = """
    SystemMessage {
        margin: 0 0 0 0;
        padding: 0 1;
    }
    """

    def __init__(self, content: str) -> None:
        self._content = content
        super().__init__()

    def render(self) -> RenderableType:
        """Render dim status text."""
        return Text(self._content, style="dim")


class HistorySeparator(Static):
    """Divider between replayed history and live events."""

    DEFAULT_CSS = """
    HistorySeparator {
        margin: 1 0;
    }
    """

    def render(self) -> RenderableType:
        """Render a dim horizontal rule labeled 'history'."""
        return Rule("history", style="dim")
