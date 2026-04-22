"""Floating indicator for unread messages when scrolled up."""

from __future__ import annotations

from rich.console import RenderableType
from rich.text import Text
from textual.message import Message
from textual.reactive import reactive
from textual.widgets import Static


class ScrollIndicator(Static):
    """Overlay badge showing unread message count; click to scroll to bottom."""

    DEFAULT_CSS = """
    ScrollIndicator {
        layer: overlay;
        dock: bottom;
        offset-y: -4;
        height: 1;
        width: auto;
        min-width: 24;
        content-align: center middle;
        text-align: center;
        background: $accent;
        color: $text;
        display: none;
    }
    """

    count: reactive[int] = reactive(0)

    class ScrollToBottom(Message):
        """Posted when the user clicks the indicator to scroll down."""

    def watch_count(self, value: int) -> None:
        """Show/hide based on unread count."""
        self.display = value > 0
        if value > 0:
            self.refresh()

    def render(self) -> RenderableType:
        """Render the unread count badge."""
        n = self.count
        label = "new message" if n == 1 else "new messages"
        return Text(f"\u2193 {n} {label}", style="bold")

    def on_click(self) -> None:
        """Scroll to bottom when clicked."""
        self.post_message(self.ScrollToBottom())
