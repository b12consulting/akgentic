"""Dropdown command palette for slash command auto-completion."""

from __future__ import annotations

from typing import TYPE_CHECKING

from rich.text import Text
from textual.widget import Widget

if TYPE_CHECKING:
    from akgentic.infra.cli.repl_commands import SlashCommand


class CommandPalette(Widget):
    """Dropdown palette showing matching slash commands."""

    DEFAULT_CSS = """
    CommandPalette {
        layer: overlay;
        dock: bottom;
        offset-y: -3;
        height: auto;
        max-height: 8;
        width: 50%;
        min-width: 40;
        border: solid $accent;
        background: $surface;
        padding: 0 1;
    }
    """

    def __init__(self, commands: dict[str, SlashCommand]) -> None:
        super().__init__()
        self._commands = commands
        self._filter: str = ""
        self._filtered: list[SlashCommand] = list(commands.values())
        self._selected_idx: int = 0

    @property
    def filter_text(self) -> str:
        """Current filter string."""
        return self._filter

    @filter_text.setter
    def filter_text(self, value: str) -> None:
        """Update the filter and recompute the visible list."""
        self._filter = value
        if value:
            self._filtered = [cmd for cmd in self._commands.values() if cmd.name.startswith(value)]
        else:
            self._filtered = list(self._commands.values())
        # Clamp selected index
        if self._filtered:
            self._selected_idx = min(self._selected_idx, len(self._filtered) - 1)
        else:
            self._selected_idx = 0
        self.refresh()

    @property
    def selected_command(self) -> str | None:
        """Return the currently highlighted command name, or None."""
        if not self._filtered:
            return None
        return self._filtered[self._selected_idx].name

    def move_up(self) -> None:
        """Move highlight up."""
        if self._filtered and self._selected_idx > 0:
            self._selected_idx -= 1
            self.refresh()

    def move_down(self) -> None:
        """Move highlight down."""
        if self._filtered and self._selected_idx < len(self._filtered) - 1:
            self._selected_idx += 1
            self.refresh()

    def render(self) -> Text:
        """Render the filtered command list with highlight."""
        output = Text()
        if not self._filtered:
            output.append("(no matching commands)", style="dim")
            return output
        for i, cmd in enumerate(self._filtered):
            style = "reverse" if i == self._selected_idx else ""
            line = f"/{cmd.name:<16s} {cmd.help_text}"
            output.append(line, style=style)
            if i < len(self._filtered) - 1:
                output.append("\n")
        return output
