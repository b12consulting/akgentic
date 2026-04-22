"""Human input prompt widget for conversation display."""

from __future__ import annotations

from rich.console import RenderableType
from rich.panel import Panel
from textual.widgets import Static


class HumanInputPrompt(Static):
    """Yellow-bordered panel showing agent's input request."""

    DEFAULT_CSS = """
    HumanInputPrompt {
        margin: 0 0 1 0;
        padding: 0 1;
    }
    """

    def __init__(self, prompt_text: str) -> None:
        self._prompt_text = prompt_text
        super().__init__()

    def render(self) -> RenderableType:
        """Render a yellow-bordered panel with the prompt text."""
        return Panel(
            self._prompt_text,
            title="Human Input Required",
            border_style="bold yellow",
        )
