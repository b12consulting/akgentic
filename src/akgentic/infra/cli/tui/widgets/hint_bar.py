"""Context-sensitive keyboard hints bar at screen bottom."""

from __future__ import annotations

from rich.console import RenderableType
from rich.text import Text
from textual.widgets import Static


class HintBar(Static):
    """Context-sensitive keyboard hints bar at screen bottom."""

    DEFAULT_CSS = """
    HintBar {
        height: 1;
        background: $surface;
        padding: 0 1;
    }
    """

    def __init__(self, hints: str = "") -> None:
        super().__init__()
        self._hints = (
            hints or "@mention  /command  Enter: send  Click: copy  Esc: teams  /quit: exit"
        )

    def render(self) -> RenderableType:
        """Render hint text."""
        return Text(self._hints, style="dim")

    def update_hints(self, hints: str) -> None:
        """Update hint text and re-render."""
        self._hints = hints
        self.refresh()
