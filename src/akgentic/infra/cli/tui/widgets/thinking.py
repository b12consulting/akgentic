"""Animated spinner shown while waiting for agent response."""

from __future__ import annotations

from rich.text import Text
from textual.widgets import Static


class ThinkingIndicator(Static):
    """Animated spinner shown while waiting for agent response."""

    DEFAULT_CSS = """
    ThinkingIndicator {
        margin: 0 0 1 0;
        padding: 0 1;
    }
    """

    _FRAMES = list("\u280b\u2819\u2839\u2838\u283c\u2834\u2826\u2827\u2807\u280f")
    _INTERVAL = 0.1

    def __init__(self) -> None:
        super().__init__(Text(f"{self._FRAMES[0]} Agent is thinking...", style="dim italic"))
        self._frame_idx: int = 0
        self._agent_names: list[str] = []

    def update_agents(self, agent_names: list[str]) -> None:
        """Update the list of agents currently thinking and refresh display."""
        self._agent_names = agent_names
        frame = self._FRAMES[self._frame_idx]
        self.update(Text(f"{frame} {self._format_agent_names()}", style="dim italic"))

    def _format_agent_names(self) -> str:
        """Format agent names into a human-readable thinking message."""
        if not self._agent_names:
            return "Agent is thinking..."
        cleaned = [f"@{n.lstrip('@')}" for n in self._agent_names]
        if len(cleaned) == 1:
            return f"{cleaned[0]} is thinking..."
        return f"{', '.join(cleaned[:-1])} and {cleaned[-1]} are thinking..."

    def on_mount(self) -> None:
        """Start the animation timer when mounted."""
        self.set_interval(self._INTERVAL, self._advance_frame)

    def _advance_frame(self) -> None:
        """Advance to the next spinner frame."""
        self._frame_idx = (self._frame_idx + 1) % len(self._FRAMES)
        frame = self._FRAMES[self._frame_idx]
        self.update(Text(f"{frame} {self._format_agent_names()}", style="dim italic"))
