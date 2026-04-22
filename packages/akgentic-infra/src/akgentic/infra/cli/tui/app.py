"""Top-level Textual application for ak-infra chat."""

from __future__ import annotations

import logging
import uuid
from pathlib import Path
from typing import TYPE_CHECKING

from textual import events, work
from textual.app import App, ComposeResult
from textual.containers import Container, VerticalScroll
from textual.widget import Widget
from textual.widgets import Static

from akgentic.core.messages.orchestrator import ProcessedMessage, ReceivedMessage
from akgentic.infra.cli.client import ApiError
from akgentic.infra.cli.connection import ConnectionState
from akgentic.infra.cli.tui.colors import AgentColorRegistry
from akgentic.infra.cli.tui.command_adapter import TuiCommandAdapter
from akgentic.infra.cli.tui.messages import ConnectionStateChanged
from akgentic.infra.cli.tui.widgets.chat_input import ChatInput
from akgentic.infra.cli.tui.widgets.command_palette import CommandPalette
from akgentic.infra.cli.tui.widgets.hint_bar import HintBar
from akgentic.infra.cli.tui.widgets.scroll_indicator import ScrollIndicator
from akgentic.infra.cli.tui.widgets.status_header import StatusHeader

_log = logging.getLogger(__name__)

if TYPE_CHECKING:
    from akgentic.infra.cli.client import ApiClient
    from akgentic.infra.cli.connection import ConnectionManager
    from akgentic.infra.cli.event_router import EventRouter
    from akgentic.infra.cli.repl_commands import CommandRegistry

_CSS_PATH = Path(__file__).parent / "styles" / "chat.tcss"


class ChatApp(App[None]):
    """Top-level Textual application for ak-infra chat."""

    TITLE = "Akgentic Chat"
    CSS_PATH = _CSS_PATH

    LAYERS = ("default", "overlay")

    def __init__(
        self,
        team_name: str = "(no team)",
        team_id: str = "",
        team_status: str = "running",
        connection_manager: ConnectionManager | None = None,
        event_router: EventRouter | None = None,
        command_registry: CommandRegistry | None = None,
        client: ApiClient | None = None,
    ) -> None:
        super().__init__()
        self._team_name = team_name
        self._team_id = team_id
        self._team_status = team_status
        self._connection_manager = connection_manager
        self._event_router = event_router
        self._command_registry = command_registry
        self._client = client
        self._color_registry = AgentColorRegistry()
        self._tui_adapter = TuiCommandAdapter(command_registry) if command_registry else None
        self._pending_messages: dict[uuid.UUID, str] = {}

    def compose(self) -> ComposeResult:
        """Compose the four-zone layout."""
        yield StatusHeader(
            team_name=self._team_name,
            team_id=self._team_id,
            team_status=self._team_status,
        )
        yield VerticalScroll(
            Static(
                "Welcome to Akgentic Chat. Send a message to begin.",
                id="welcome",
            ),
            id="conversation",
        )
        yield ScrollIndicator()
        with Container(id="input-area"):
            yield ChatInput(command_registry=self._command_registry)
            yield HintBar()

    # -- Command palette management (mounted at app level as overlay) --

    _palette: CommandPalette | None = None

    def on_chat_input_palette_requested(self, _event: ChatInput.PaletteRequested) -> None:
        """Show the command palette overlay."""
        if self._palette is not None or self._command_registry is None:
            return
        self._palette = CommandPalette(self._command_registry.commands)
        self.mount(self._palette)

    def on_chat_input_palette_dismissed(self, _event: ChatInput.PaletteDismissed) -> None:
        """Hide the command palette overlay."""
        if self._palette is not None:
            self._palette.remove()
            self._palette = None

    def on_chat_input_palette_filter_changed(self, event: ChatInput.PaletteFilterChanged) -> None:
        """Update the palette filter text."""
        if self._palette is not None:
            self._palette.filter_text = event.filter_text

    def on_chat_input_palette_navigate(self, event: ChatInput.PaletteNavigate) -> None:
        """Navigate the palette up/down."""
        if self._palette is not None:
            if event.direction == "up":
                self._palette.move_up()
            else:
                self._palette.move_down()

    def on_chat_input_palette_select(self, _event: ChatInput.PaletteSelect) -> None:
        """Select the highlighted palette command (Tab)."""
        if self._palette is not None:
            cmd = self._palette.selected_command
            self._palette.remove()
            self._palette = None
            if cmd is not None:
                self.query_one(ChatInput).set_command_text(cmd)

    def on_chat_input_palette_select_and_submit(
        self, _event: ChatInput.PaletteSelectAndSubmit
    ) -> None:
        """Select the highlighted palette command and submit."""
        if self._palette is not None:
            cmd = self._palette.selected_command
            self._palette.remove()
            self._palette = None
            if cmd is not None:
                chat_input = self.query_one(ChatInput)
                chat_input.set_command_text(cmd)
                chat_input._submit_text()  # noqa: SLF001

    # -- ESC key: return to team select screen --

    def on_key(self, event: events.Key) -> None:
        """Handle app-level key events."""
        if event.key == "escape":
            # Don't intercept ESC when a screen overlay is active
            if len(self.screen_stack) > 1:
                return
            # If command palette is open, let ChatInput handle it
            chat_input = self.query_one(ChatInput)
            if chat_input._palette_visible:  # noqa: SLF001
                return
            # Return to team select screen
            event.prevent_default()
            self._switch_team()

    @work(exclusive=False)
    async def _switch_team(self) -> None:
        """Push TeamSelectScreen from chat view and switch to the selected team."""
        from akgentic.infra.cli.tui.screens.team_select import TeamSelectScreen

        team_id = await self.push_screen_wait(TeamSelectScreen(client=self._client))
        if team_id is None:
            return
        if team_id == "__quit__":
            self.exit()
            return
        if self._client is not None:
            team_info = self._client.get_team(team_id)
            self._team_id = team_id
            self._team_name = team_info.name
            self._team_status = team_info.status
            self.query_one(StatusHeader).update_team(team_info.name, team_id, team_info.status)
        else:
            self._team_id = team_id
        if self._connection_manager is not None:
            from akgentic.infra.cli.connection import ConnectionManager as ConnMgr

            self._connection_manager = ConnMgr(
                server_url=self._connection_manager._server_url,  # noqa: SLF001
                team_id=team_id,
                api_key=self._connection_manager._api_key,  # noqa: SLF001
            )
            self._connection_manager._on_state_change = self._on_conn_state_change
        self._clear_conversation()
        self.query_one(ChatInput).focus()
        self.stream_events()

    def on_mount(self) -> None:
        """Wire connection manager callback and start streaming."""
        if not self._team_id:
            self._select_team()
            return
        if self._connection_manager is not None:
            self._connection_manager._on_state_change = self._on_conn_state_change
        self.query_one(ChatInput).focus()
        self.stream_events()

    @work(exclusive=True)
    async def _select_team(self) -> None:
        """Push TeamSelectScreen and wait for result."""
        from akgentic.infra.cli.tui.screens.team_select import TeamSelectScreen

        team_id = await self.push_screen_wait(TeamSelectScreen(client=self._client))
        if team_id is None or team_id == "__quit__":
            self.exit()
            return
        # Fetch team info and update header
        if self._client is not None:
            team_info = self._client.get_team(team_id)
            self._team_id = team_id
            self._team_name = team_info.name
            self._team_status = team_info.status
            self.query_one(StatusHeader).update_team(team_info.name, team_id, team_info.status)
        else:
            self._team_id = team_id
        if self._connection_manager is not None:
            from akgentic.infra.cli.connection import ConnectionManager as ConnMgr

            self._connection_manager = ConnMgr(
                server_url=self._connection_manager._server_url,  # noqa: SLF001
                team_id=team_id,
                api_key=self._connection_manager._api_key,  # noqa: SLF001
            )
            self._connection_manager._on_state_change = self._on_conn_state_change
        self._clear_conversation()
        self.query_one(ChatInput).focus()
        self.stream_events()

    def _on_conn_state_change(self, state: ConnectionState) -> None:
        """Callback for ConnectionManager -- post as Textual message."""
        self.post_message(ConnectionStateChanged(state))

    def on_connection_state_changed(
        self,
        event: ConnectionStateChanged,
    ) -> None:
        """Forward connection state changes to child widgets."""
        state_str = event.state.value
        try:
            self.query_one(StatusHeader).update_connection(state_str)
        except Exception:  # noqa: BLE001
            _log.debug("StatusHeader not available for connection state update")
        try:
            chat_input = self.query_one(ChatInput)
            if state_str == "disconnected":
                chat_input.input_mode = "disconnected"
            elif state_str == "reconnecting":
                chat_input.input_mode = "reconnecting"
            elif state_str == "connected":
                chat_input.input_mode = "chat"
        except Exception:  # noqa: BLE001
            _log.debug("ChatInput not available for connection state update")

    def _is_at_bottom(self, conversation: VerticalScroll) -> bool:
        """Check if the conversation is scrolled to (or near) the bottom."""
        return conversation.scroll_y >= conversation.max_scroll_y - 2

    async def _mount_event_widget(
        self,
        widget: Widget,
        conversation: VerticalScroll,
    ) -> None:
        """Mount an event widget with remove-and-remount thinking indicator cycle."""
        is_thinking = len(self._pending_messages) > 0
        if is_thinking:
            self._remove_thinking_indicator(conversation)
        at_bottom = self._is_at_bottom(conversation)
        await conversation.mount(widget)
        if is_thinking:
            await self._mount_thinking_indicator(conversation)
        if at_bottom:
            widget.scroll_visible(animate=False)
        else:
            self.query_one(ScrollIndicator).count += 1

    def on_scroll_indicator_scroll_to_bottom(self, _event: ScrollIndicator.ScrollToBottom) -> None:
        """Handle click on scroll indicator — jump to bottom."""
        conversation = self.query_one("#conversation", VerticalScroll)
        conversation.scroll_end(animate=False)
        self.query_one(ScrollIndicator).count = 0

    def _clear_conversation(self) -> None:
        """Clear conversation area and reset related state for team switch."""
        self._pending_messages.clear()
        conversation = self.query_one("#conversation", VerticalScroll)
        conversation.remove_children()
        self._color_registry.reset()
        self.query_one(ScrollIndicator).count = 0

    @work(exclusive=True)
    async def stream_events(self) -> None:
        """Background worker: stream WebSocket events and mount widgets."""
        if self._connection_manager is None or self._event_router is None:
            return
        from akgentic.infra.cli.ws_client import WsConnectionError

        if self._connection_manager.state == ConnectionState.DISCONNECTED:
            try:
                await self._connection_manager.connect()
            except WsConnectionError:
                pass

        conversation = self.query_one("#conversation", VerticalScroll)
        if not conversation.query(".welcome-msg, #welcome"):
            welcome = Static(
                "Welcome to Akgentic Chat. Send a message to begin.",
                classes="welcome-msg",
            )
            await conversation.mount(welcome)

        while True:
            try:
                event_data = await self._connection_manager.receive_event()
                _log.debug("WS event received: %s", event_data)
                if isinstance(event_data, ReceivedMessage):
                    await self._on_received_message(event_data, conversation)
                    continue
                if isinstance(event_data, ProcessedMessage):
                    self._on_processed_message(event_data, conversation)
                    continue
                widget = self._event_router.to_widget(event_data, self._color_registry)
                _log.debug("to_widget result: %s", type(widget).__name__ if widget else None)
                if widget is not None:
                    await self._mount_event_widget(widget, conversation)
            except WsConnectionError as exc:
                _log.debug("WS connection error in stream loop: %s", exc)
                self._remove_thinking_indicator(conversation)
                self._pending_messages.clear()
                break

        from akgentic.infra.cli.tui.widgets.system_message import SystemMessage

        msg = SystemMessage(content="Connection lost. Use /reconnect to try again.")
        await conversation.mount(msg)
        msg.scroll_visible(animate=False)

    def _remove_thinking_indicator(self, conversation: VerticalScroll) -> None:
        """Remove ThinkingIndicator if present."""
        from akgentic.infra.cli.tui.widgets.thinking import ThinkingIndicator

        indicators = conversation.query(ThinkingIndicator)
        for indicator in indicators:
            indicator.remove()

    async def _on_received_message(
        self, msg: ReceivedMessage, conversation: VerticalScroll
    ) -> None:
        """Handle ReceivedMessage telemetry -- track pending agent processing."""
        agent_name = msg.sender.name if msg.sender else "Agent"
        was_empty = len(self._pending_messages) == 0
        self._pending_messages[msg.message_id] = agent_name
        if was_empty:
            await self._mount_thinking_indicator(conversation)
        else:
            self._update_thinking_indicator()

    def _on_processed_message(
        self, msg: ProcessedMessage, conversation: VerticalScroll
    ) -> None:
        """Handle ProcessedMessage telemetry -- remove pending agent processing."""
        self._pending_messages.pop(msg.message_id, None)
        if not self._pending_messages:
            self._remove_thinking_indicator(conversation)
        else:
            self._update_thinking_indicator()

    async def _mount_thinking_indicator(self, conversation: VerticalScroll) -> None:
        """Mount a ThinkingIndicator at the bottom of the conversation."""
        from akgentic.infra.cli.tui.widgets.thinking import ThinkingIndicator

        indicator = ThinkingIndicator()
        indicator.update_agents(list(self._pending_messages.values()))
        await conversation.mount(indicator)
        indicator.scroll_visible(animate=False)

    def _update_thinking_indicator(self) -> None:
        """Update the displayed agent names on the existing ThinkingIndicator."""
        from akgentic.infra.cli.tui.widgets.thinking import ThinkingIndicator

        indicators = self.query(ThinkingIndicator)
        if indicators:
            indicators[0].update_agents(list(self._pending_messages.values()))

    async def on_chat_input_submitted(self, event: ChatInput.Submitted) -> None:
        """Handle user message submission."""
        text = event.text
        if text.startswith("/"):
            # Slash commands -- do NOT mount ThinkingIndicator
            if self._tui_adapter is not None:
                await self._tui_adapter.dispatch(text, self)
            return
        conversation = self.query_one("#conversation", VerticalScroll)

        # Remove welcome placeholder on first message
        welcome_nodes = conversation.query("#welcome, .welcome-msg")
        for node in welcome_nodes:
            node.remove()

        # Parse @mention for directed messages
        agent_name: str | None = None
        send_text = text
        stripped = text.strip()
        if stripped.startswith("@") and " " in stripped:
            parts = stripped.split(None, 1)
            agent_name = parts[0]
            send_text = parts[1]

        # Send message via API in background thread
        self._send_message(send_text, agent_name=agent_name)

    @work(thread=True)
    def _send_message(self, text: str, agent_name: str | None = None) -> None:
        """Send a user message via the REST API in a background thread."""
        if self._client is not None:
            try:
                if agent_name is not None:
                    self._client.send_message_to(self._team_id, agent_name, text)
                else:
                    self._client.send_message(self._team_id, text)
            except ApiError as exc:
                self.call_from_thread(self._mount_send_error, str(exc.detail))

    def _mount_send_error(self, detail: str) -> None:
        """Schedule ErrorWidget mount for a failed message send (called from thread)."""
        self.run_worker(self._do_mount_send_error(detail), exclusive=False)

    async def _do_mount_send_error(self, detail: str) -> None:
        """Mount an ErrorWidget for a failed message send."""
        from akgentic.infra.cli.tui.widgets.error import ErrorWidget

        conversation = self.query_one("#conversation", VerticalScroll)
        widget = ErrorWidget(content=f"Failed to send message: {detail}")
        await conversation.mount(widget)
        widget.scroll_visible(animate=False)
