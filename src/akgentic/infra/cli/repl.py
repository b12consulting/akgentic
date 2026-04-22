"""Interactive chat REPL for team communication."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from enum import Enum
from typing import Any

from prompt_toolkit import PromptSession
from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.history import InMemoryHistory
from pydantic import BaseModel

from akgentic.core.messages.message import Message
from akgentic.infra.cli.client import ApiClient, ApiError
from akgentic.infra.cli.connection import ConnectionManager, ConnectionState
from akgentic.infra.cli.event_router import EventRouter
from akgentic.infra.cli.formatters import OutputFormat
from akgentic.infra.cli.renderers import RichRenderer
from akgentic.infra.cli.repl_commands import CommandRegistry, build_default_registry
from akgentic.infra.cli.team_selector import TeamSelector as TeamSelector  # re-export
from akgentic.infra.cli.ws_client import WsConnectionError


class InputMode(Enum):
    """Input mode for the REPL session."""

    CHAT = "chat"
    REPLY = "reply"


class ReplyContext(BaseModel):
    """Context for an in-progress reply to an agent."""

    reply_id: str
    agent_name: str
    prompt: str


class SessionState(BaseModel):
    """Structured session state for the chat REPL."""

    team_id: str
    team_name: str = "(unknown)"
    team_status: str = "?"
    input_mode: InputMode = InputMode.CHAT
    reply_context: ReplyContext | None = None
    connection_state: ConnectionState = ConnectionState.CONNECTING


class ChatSession:
    """REPL session combining REST message sending with WebSocket event streaming."""

    def __init__(
        self,
        client: ApiClient,
        conn: ConnectionManager,
        team_id: str,
        fmt: OutputFormat,
        *,
        server_url: str = "http://localhost:8000",
        api_key: str | None = None,
        renderer: RichRenderer | None = None,
    ) -> None:
        self.client = client
        self.conn = conn
        self._state = SessionState(team_id=team_id)
        self.fmt = fmt
        self.server_url = server_url
        self.api_key = api_key
        self.renderer = renderer or RichRenderer()
        self.command_registry: CommandRegistry = build_default_registry()
        self._running = True
        self._receive_task: asyncio.Task[None] | None = None
        self._message_buffer: list[str] = []
        self._prompt_session: PromptSession[str] = PromptSession(
            history=InMemoryHistory(),
            completer=_SlashCompleter(self.command_registry),
        )
        self._event_router = EventRouter(self.renderer, logger=logging.getLogger(__name__))

        def _on_conn_state_change(new_state: ConnectionState) -> None:
            self._state = self._state.model_copy(update={"connection_state": new_state})
            if new_state == ConnectionState.CONNECTED and self._message_buffer:
                self._flush_message_buffer()

        self.conn._on_state_change = _on_conn_state_change

    @property
    def team_id(self) -> str:
        """Team ID from session state."""
        return self._state.team_id

    @team_id.setter
    def team_id(self, value: str) -> None:
        self._state = self._state.model_copy(update={"team_id": value})

    def _fetch_team_info(self) -> None:
        """Fetch and cache team name and status for the status bar."""
        try:
            team = self.client.get_team(self._state.team_id)
            self._state = self._state.model_copy(
                update={"team_name": team.name, "team_status": team.status}
            )
        except ApiError:
            pass  # Keep defaults "(unknown)" and "?"

    async def run(self) -> None:
        """Main REPL loop: connect WS, read input, stream events."""
        async with self.conn:
            self._fetch_team_info()
            self.renderer.render_border()

            self._receive_task = asyncio.create_task(self._receive_loop())
            try:
                await self._input_loop()
            except KeyboardInterrupt:
                pass
            finally:
                self._running = False
                if self._receive_task is not None:
                    self._receive_task.cancel()
                    try:
                        await self._receive_task
                    except asyncio.CancelledError:
                        pass
                self.renderer.render_border()
                self.renderer.render_system_message("Session closed.")

    def _get_prompt(self) -> str:
        """Build dynamic prompt based on connection state and input mode."""
        if self._state.connection_state == ConnectionState.DISCONNECTED:
            return "[disconnected] > "
        if self._state.connection_state == ConnectionState.RECONNECTING:
            return "[reconnecting...] > "
        if self._state.input_mode == InputMode.REPLY and self._state.reply_context:
            return f"Reply to {self._state.reply_context.agent_name}: "
        return "> "

    def _read_input(self) -> str:
        """Render bottom border, prompt for input, then status bar below."""
        self.renderer.render_border()
        line = self._prompt_session.prompt(self._get_prompt())
        self.renderer.render_border()
        self.renderer.render_status_bar(
            self._state.team_name,
            self._state.team_id,
            self._state.team_status,
        )
        return line

    def _check_connection_gate(self, line: str) -> bool:
        """Check connection state and handle DISCONNECTED/RECONNECTING. Returns True if blocked."""
        conn_state = self._state.connection_state
        if conn_state == ConnectionState.DISCONNECTED:
            self.renderer.render_error("Not connected. Use /reconnect to restore connection.")
            return True
        if conn_state == ConnectionState.RECONNECTING:
            self.renderer.render_system_message(
                "Reconnecting... message will be sent when connection is restored."
            )
            self._message_buffer.append(line)
            return True
        return False

    async def _handle_reply(self, line: str) -> None:
        """Send a reply to the pending human-input request."""
        loop = asyncio.get_running_loop()
        ctx = self._state.reply_context
        if ctx is None:
            return
        try:
            await loop.run_in_executor(
                None, self.client.human_input, self._state.team_id, line, ctx.reply_id
            )
            # Clear only after successful send
            self._state = self._state.model_copy(
                update={"input_mode": InputMode.CHAT, "reply_context": None}
            )
        except ApiError:
            self.renderer.render_error("Error sending reply. Try again.")

    async def _input_loop(self) -> None:
        """Read user input and send messages."""
        loop = asyncio.get_running_loop()
        while self._running:
            try:
                line = await loop.run_in_executor(None, self._read_input)
            except (EOFError, KeyboardInterrupt):
                break

            if not line.strip():
                continue

            if line.strip() == "/quit":
                break

            # Try dispatching as a slash command
            if await self.command_registry.dispatch(line, self):
                continue

            # Route as human-input reply if pending
            if self._state.input_mode == InputMode.REPLY and self._state.reply_context:
                if self._check_connection_gate(line):
                    continue
                await self._handle_reply(line)
                continue

            # Connection-aware message sending
            if self._check_connection_gate(line):
                continue

            # Send message via REST API (run in executor to avoid blocking)
            try:
                await self._send_or_mention(loop, line)
            except ApiError:
                self.renderer.render_error("Error sending message.")

    async def _send_or_mention(self, loop: asyncio.AbstractEventLoop, line: str) -> None:
        """Send a message, routing @mentions to a specific agent."""
        s = line.strip()
        if s.startswith("@") and " " in s:
            parts = s.split(None, 1)
            await loop.run_in_executor(
                None,
                self.client.send_message_to,
                self._state.team_id,
                parts[0],
                parts[1],
            )
        else:
            await loop.run_in_executor(None, self.client.send_message, self._state.team_id, line)

    async def _receive_loop(self) -> None:
        """Background coroutine: read WebSocket events and render them."""
        while self._running:
            try:
                event = await self.conn.receive_event()
                self._render_event(event)
            except asyncio.CancelledError:
                raise
            except WsConnectionError as exc:
                self.renderer.render_error(f"Connection lost: {exc.reason}")
                break

    def _flush_message_buffer(self) -> None:
        """Send all buffered messages that were queued during RECONNECTING state."""
        while self._message_buffer:
            msg = self._message_buffer.pop(0)
            try:
                self.client.send_message(self._state.team_id, msg)
            except ApiError:
                self.renderer.render_error(f"Failed to send buffered message: {msg[:50]}")

    def _render_event(self, event: Message) -> bool:
        """Format and render a single event. Returns True if something was rendered."""

        def _set_pending(message_id: str, agent_name: str) -> None:
            self._state = self._state.model_copy(
                update={
                    "input_mode": InputMode.REPLY,
                    "reply_context": ReplyContext(
                        reply_id=message_id, agent_name=agent_name, prompt=""
                    ),
                }
            )

        self._event_router._on_human_input = _set_pending
        return self._event_router.route(event)


class _SlashCompleter(Completer):
    """Auto-complete slash commands from the command registry."""

    def __init__(self, registry: CommandRegistry) -> None:
        self._registry = registry

    def get_completions(self, document: Any, complete_event: Any) -> Any:  # noqa: ANN401
        text = document.text_before_cursor
        if not text.startswith("/"):
            return
        partial = text[1:]  # strip leading /
        for cmd in self._registry.commands.values():
            if cmd.name.startswith(partial):
                yield Completion(
                    f"/{cmd.name}",
                    start_position=-len(text),
                    display=f"/{cmd.name}",
                    display_meta=cmd.help_text,
                )


# -- Backward-compatible re-exports and wrappers --


def _render_event_impl(
    event: Message,
    renderer: RichRenderer,
    on_human_input: Callable[[str, str], None] | None = None,
) -> bool:
    """Backward-compatible wrapper -- delegates to EventRouter."""
    router = EventRouter(renderer, on_human_input=on_human_input)
    return router.route(event)


_default_renderer = RichRenderer()
_default_event_router = EventRouter(_default_renderer)


def _print_event(event: Message) -> bool:
    """Backward-compatible module-level event printer."""
    return _default_event_router.route(event)
