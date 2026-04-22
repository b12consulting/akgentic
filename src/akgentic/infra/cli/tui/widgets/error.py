"""Error message widget for conversation display."""

from __future__ import annotations

from rich.console import RenderableType
from rich.text import Text
from textual.widgets import Static


class ErrorWidget(Static):
    """Red error message display."""

    DEFAULT_CSS = """
    ErrorWidget {
        margin: 0 0 1 0;
        padding: 0 1;
    }
    """

    def __init__(self, content: str) -> None:
        self._content = content
        super().__init__()

    def render(self) -> RenderableType:
        """Render red error message with [error] prefix."""
        return Text(f"[error] {self._content}", style="bold red")
