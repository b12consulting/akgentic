"""Event routing and dispatch for chat events."""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from typing import TYPE_CHECKING

from akgentic.core.messages.message import Message
from akgentic.core.messages.orchestrator import ErrorMessage, EventMessage, SentMessage
from akgentic.infra.cli.renderers import RichRenderer
from akgentic.llm.event import ToolCallEvent, ToolReturnEvent

if TYPE_CHECKING:
    from textual.widget import Widget

    from akgentic.infra.cli.tui.colors import AgentColorRegistry


class EventRouter:
    """Route typed Message instances and dispatch to the renderer."""

    def __init__(
        self,
        renderer: RichRenderer,
        on_human_input: Callable[[str, str], None] | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self._renderer = renderer
        self._on_human_input = on_human_input
        self._log = logger or logging.getLogger(__name__)
        self._last_agent_color: str | None = None

    def route(self, event: Message) -> bool:
        """Classify and render a typed event. Returns True if rendered.

        Malformed events are logged at DEBUG and skipped -- never raised.
        """
        try:
            if isinstance(event, SentMessage):
                return self._handle_sent_message(event)
            if isinstance(event, ErrorMessage):
                return self._handle_error_message(event)
            if isinstance(event, EventMessage):
                return self._handle_event_message(event)
            return False
        except Exception as exc:  # noqa: BLE001
            self._log.debug("Malformed event skipped: %s", exc)
            return False

    def _handle_error_message(self, event: ErrorMessage) -> bool:
        """Render an ErrorMessage event."""
        self._renderer.render_error(event.exception_value)
        return True

    def _handle_sent_message(self, event: SentMessage) -> bool:
        """Render a SentMessage -- agent response with sender and content."""
        sender_name = event.sender.name if event.sender else "agent"
        content = getattr(event.message, "content", "")
        if content:
            self._renderer.render_agent_message(sender_name, str(content))
            return True
        return False

    def _handle_event_message(self, event: EventMessage) -> bool:
        """Handle EventMessage -- inspect nested event for tool calls / human input."""
        nested = event.event

        if isinstance(nested, ToolCallEvent):
            self._handle_tool_call(nested)
            return True

        if isinstance(nested, ToolReturnEvent):
            return False

        # Duck-type check for HumanInput / RequestInput
        if hasattr(nested, "prompt") or hasattr(nested, "content"):
            prompt_text = str(
                getattr(nested, "prompt", None) or getattr(nested, "content", "Input requested")
            )
            self._renderer.render_human_input_request(prompt_text)
            if self._on_human_input is not None:
                self._notify_human_input(event)
            return True

        return False

    def _handle_tool_call(self, tool_event: ToolCallEvent) -> None:
        """Extract and render a tool call event."""
        tool_name = tool_event.tool_name
        raw_input = tool_event.arguments
        if isinstance(raw_input, dict | list):
            tool_input = json.dumps(raw_input, indent=2)
        else:
            tool_input = str(raw_input)
        raw_output = getattr(tool_event, "result", None)
        if raw_output is None:
            tool_output = None
        elif isinstance(raw_output, dict | list):
            tool_output = json.dumps(raw_output, indent=2)
        else:
            tool_output = str(raw_output)
        self._renderer.render_tool_call(tool_name, tool_input, tool_output)

    def _notify_human_input(self, event: Message) -> None:
        """Extract message_id and agent_name from the event and invoke callback."""
        raw_id = str(event.id)
        if not raw_id:
            return
        sender_name = event.sender.name if event.sender else "Agent"
        if self._on_human_input is not None:
            self._on_human_input(raw_id, sender_name)

    def to_widget(
        self,
        event: Message,
        color_registry: AgentColorRegistry,
    ) -> Widget | None:
        """Parse a typed event and return the appropriate Textual widget.

        Returns ``None`` for unrecognized or malformed events.
        This method does NOT invoke callbacks or use the renderer -- it is
        a pure event-to-widget translation for TUI usage.
        """
        try:
            if isinstance(event, ErrorMessage):
                return self._error_to_widget(event)
            if isinstance(event, SentMessage):
                return self._sent_to_widget(event, color_registry)
            if isinstance(event, EventMessage):
                return self._event_to_widget(event)
        except Exception as exc:  # noqa: BLE001
            self._log.debug("to_widget: malformed event skipped: %s", exc)
        return None

    def _error_to_widget(self, event: ErrorMessage) -> Widget:
        """Convert an ErrorMessage event to an ErrorWidget."""
        from akgentic.infra.cli.tui.widgets.error import ErrorWidget

        return ErrorWidget(content=event.exception_value)

    def _sent_to_widget(
        self,
        event: SentMessage,
        color_registry: AgentColorRegistry,
    ) -> Widget | None:
        """Convert a SentMessage event to an AgentMessage widget."""
        from akgentic.infra.cli.tui.widgets.agent_message import AgentMessage

        sender = event.sender.name if event.sender else "agent"
        content = getattr(event.message, "content", "")
        if not content:
            return None

        recipient: str | None = event.recipient.name if event.recipient else None

        timestamp: str | None = None
        ts = event.message.timestamp or event.timestamp
        if ts is not None:
            timestamp = ts.strftime("%H:%M")

        color: str = color_registry.get(sender)
        self._last_agent_color = color
        return AgentMessage(
            sender=sender,
            content=str(content),
            color=color,
            timestamp=timestamp,
            recipient=recipient,
        )

    def _event_to_widget(self, event: EventMessage) -> Widget | None:
        """Convert an EventMessage event to a ToolCallWidget or HumanInputPrompt."""
        nested = event.event

        if isinstance(nested, ToolCallEvent):
            return self._tool_call_to_widget(nested)

        if isinstance(nested, ToolReturnEvent):
            return None

        # Duck-type check for HumanInput / RequestInput
        if hasattr(nested, "prompt") or hasattr(nested, "content"):
            from akgentic.infra.cli.tui.widgets.human_input import HumanInputPrompt

            prompt_text = str(
                getattr(nested, "prompt", None) or getattr(nested, "content", "Input requested")
            )
            return HumanInputPrompt(prompt_text=prompt_text)
        return None

    def _tool_call_to_widget(self, tool_event: ToolCallEvent) -> Widget:
        """Convert a ToolCallEvent dataclass to a ToolCallWidget."""
        from akgentic.infra.cli.tui.widgets.tool_call import ToolCallWidget

        tool_name = tool_event.tool_name
        raw_input = tool_event.arguments
        if isinstance(raw_input, dict | list):
            tool_input = json.dumps(raw_input, indent=2)
        else:
            tool_input = str(raw_input)
        raw_output = getattr(tool_event, "result", None)
        if raw_output is None:
            tool_output = None
        elif isinstance(raw_output, dict | list):
            tool_output = json.dumps(raw_output, indent=2)
        else:
            tool_output = str(raw_output)
        return ToolCallWidget(
            tool_name=tool_name,
            tool_input=tool_input,
            tool_output=tool_output,
            agent_color=self._last_agent_color,
        )
