"""Multi-line chat input with Enter-to-send, history, and slash completion."""

from __future__ import annotations

from typing import TYPE_CHECKING

from textual import events
from textual.message import Message
from textual.reactive import reactive
from textual.widgets import TextArea

if TYPE_CHECKING:
    from akgentic.infra.cli.repl_commands import CommandRegistry


class ChatInput(TextArea):
    """Multi-line chat input with Enter-to-send, history navigation, and slash completion."""

    DEFAULT_CSS = """
    ChatInput {
        height: auto;
        max-height: 10;
        min-height: 1;
    }
    """

    input_mode: reactive[str] = reactive("chat")

    class Submitted(Message):
        """Fired when user presses Enter (without Shift)."""

        def __init__(self, text: str) -> None:
            self.text = text
            super().__init__()

    class PaletteRequested(Message):
        """Fired when the command palette should be shown."""

    class PaletteDismissed(Message):
        """Fired when the command palette should be hidden."""

    class PaletteFilterChanged(Message):
        """Fired when the palette filter text changes."""

        def __init__(self, filter_text: str) -> None:
            self.filter_text = filter_text
            super().__init__()

    class PaletteNavigate(Message):
        """Fired when user navigates the palette with arrow keys."""

        def __init__(self, direction: str) -> None:
            self.direction = direction
            super().__init__()

    class PaletteSelect(Message):
        """Fired when user selects a palette command (Tab)."""

    class PaletteSelectAndSubmit(Message):
        """Fired when user selects a palette command and submits (Enter on palette)."""

    def __init__(
        self,
        command_registry: CommandRegistry | None = None,
    ) -> None:
        super().__init__(language=None, soft_wrap=True, show_line_numbers=False)
        self._history: list[str] = []
        self._history_idx: int = -1
        self._browsing_history: bool = False
        self._reply_target: str = ""
        self._command_registry = command_registry
        self._palette_visible: bool = False
        self._suppress_palette: bool = False

    def watch_input_mode(self, mode: str) -> None:
        """Update border title based on input mode."""
        labels = {
            "chat": "> ",
            "reply": f"Reply to @{self._reply_target}: ",
            "disconnected": "\\[disconnected] > ",
            "reconnecting": "\\[reconnecting...] > ",
        }
        self.border_title = labels.get(mode, "> ")

    def set_command_text(self, cmd: str) -> None:
        """Set input text to a selected command (called by app after palette select)."""
        self._suppress_palette = True
        self.text = f"/{cmd} "
        self.move_cursor_to_end()

    def _submit_text(self) -> None:
        """Submit current text, add to history, and clear input."""
        text = self.text.strip()
        if text:
            self._history.append(text)
            self._history_idx = -1
            self._browsing_history = False
            self.post_message(self.Submitted(text))
            self.clear()

    def _handle_palette_key(self, event: events.Key) -> bool:
        """Handle key events when the palette is visible. Returns True if handled."""
        if not self._palette_visible:
            return False
        if event.key == "escape":
            event.prevent_default()
            self._palette_visible = False
            self.post_message(self.PaletteDismissed())
            return True
        if event.key == "tab":
            event.prevent_default()
            self.post_message(self.PaletteSelect())
            return True
        if event.key == "up":
            event.prevent_default()
            self.post_message(self.PaletteNavigate("up"))
            return True
        if event.key == "down":
            event.prevent_default()
            self.post_message(self.PaletteNavigate("down"))
            return True
        if event.key == "enter":
            event.prevent_default()
            self.post_message(self.PaletteSelectAndSubmit())
            return True
        return False

    def _handle_history_key(self, event: events.Key) -> bool:
        """Handle up/down arrow keys for history navigation. Returns True if handled."""
        if event.key == "up" and (not self.text or self._browsing_history):
            event.prevent_default()
            if self._history:
                if self._history_idx < 0:
                    self._history_idx = len(self._history) - 1
                elif self._history_idx > 0:
                    self._history_idx -= 1
                self._browsing_history = True
                self.text = self._history[self._history_idx]
                self.move_cursor_to_end()
            return True
        if event.key == "down" and self._browsing_history:
            event.prevent_default()
            if self._history_idx < len(self._history) - 1:
                self._history_idx += 1
                self.text = self._history[self._history_idx]
                self.move_cursor_to_end()
            else:
                self._history_idx = -1
                self._browsing_history = False
                self.text = ""
            return True
        return False

    async def _on_key(self, event: events.Key) -> None:
        if self._handle_palette_key(event):
            return
        if event.key == "enter":
            event.prevent_default()
            self._submit_text()
            if self._palette_visible:
                self._palette_visible = False
                self.post_message(self.PaletteDismissed())
            return
        if self._handle_history_key(event):
            return
        # For non-navigation keys, reset history browsing
        if event.key not in {"up", "down", "enter", "shift+enter", "escape", "tab"}:
            self._browsing_history = False

    def _on_text_area_changed(self, _event: TextArea.Changed) -> None:
        """React to text changes for palette show/dismiss/filter."""
        if self._suppress_palette:
            self._suppress_palette = False
            return
        text = self.text
        # Only show palette for command name completion (before the first space).
        if text.startswith("/") and " " not in text and self._command_registry is not None:
            if not self._palette_visible:
                self._palette_visible = True
                self.post_message(self.PaletteRequested())
            self.post_message(self.PaletteFilterChanged(text[1:]))
        else:
            if self._palette_visible:
                self._palette_visible = False
                self.post_message(self.PaletteDismissed())

    def move_cursor_to_end(self) -> None:
        """Move cursor to the end of the text."""
        lines = self.text.split("\n")
        last_line = len(lines) - 1
        last_col = len(lines[-1])
        self.move_cursor((last_line, last_col))
