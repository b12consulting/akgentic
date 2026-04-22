"""Bridge between CommandRegistry handlers and TUI widget output."""

from __future__ import annotations

import asyncio
import io
import logging
from contextlib import redirect_stdout
from typing import TYPE_CHECKING

from akgentic.infra.cli.client import ApiClient, ApiError
from akgentic.infra.cli.renderers import RichRenderer
from akgentic.infra.cli.repl_commands import CommandRegistry

if TYPE_CHECKING:
    from akgentic.infra.cli.client import EventInfo
    from akgentic.infra.cli.event_router import EventRouter
    from akgentic.infra.cli.tui.app import ChatApp
    from akgentic.infra.cli.tui.colors import AgentColorRegistry

_log = logging.getLogger(__name__)


class _NoOpRenderer(RichRenderer):
    """Renderer stub that captures render_error calls to a buffer."""

    def __init__(self, error_buffer: list[str]) -> None:
        from rich.console import Console

        super().__init__(console=Console(file=io.StringIO(), width=80))
        self._error_buffer = error_buffer

    def render_error(self, content: str) -> None:
        """Capture errors instead of printing them."""
        self._error_buffer.append(content)

    def render_border(self, style: str = "bright_black") -> None:
        """No-op in TUI."""

    def render_status_bar(self, *_args: object) -> None:
        """No-op in TUI."""

    def render_connection_status(self, _status: str) -> None:
        """No-op in TUI."""

    def render_system_message(self, _msg: str) -> None:
        """No-op in TUI."""

    def render_history_separator(self) -> None:
        """No-op in TUI."""


class _TuiSession:
    """Lightweight adapter satisfying the ChatSession interface for command handlers.

    Bridges TUI state (from ChatApp) to the handler signature so existing handlers
    work without modification.
    """

    def __init__(self, app: ChatApp, registry: CommandRegistry) -> None:
        self._app = app
        self.client = app._client  # noqa: SLF001
        self.conn = app._connection_manager  # noqa: SLF001
        self.command_registry = registry
        self._error_buffer: list[str] = []
        self.renderer = _NoOpRenderer(self._error_buffer)
        self._receive_task = None
        self._message_buffer: list[str] = []

    @property
    def team_id(self) -> str:
        """Forward team_id from ChatApp."""
        return self._app._team_id  # noqa: SLF001

    @team_id.setter
    def team_id(self, value: str) -> None:
        self._app._team_id = value  # noqa: SLF001

    @property
    def _state(self) -> object:
        """Stub for session state access."""
        return _MinimalState(self._app._team_id)  # noqa: SLF001

    def _fetch_team_info(self) -> None:
        """No-op stub -- TUI uses StatusHeader.update_team() instead."""

    def _render_event(self, _event: object) -> bool:
        """No-op stub -- TUI uses EventRouter.to_widget() instead."""
        return False


class _MinimalState:
    """Minimal state object exposing team_id for command handlers."""

    def __init__(self, team_id: str) -> None:
        self.team_id = team_id


class TuiCommandAdapter:
    """Bridges CommandRegistry handlers to TUI widget output.

    - Output-producing commands: captures print() via redirect_stdout,
      mounts captured text as SystemMessage widgets.
    - Error output: captures render_error() calls, mounts ErrorWidget.
    - Overridden commands: /switch, /reconnect, /quit, /delete get
      TUI-specific implementations.
    """

    # Commands that need TUI-specific behavior (worker restart, StatusHeader, etc.)
    _TUI_OVERRIDES = frozenset(
        {
            "quit",
            "switch",
            "reconnect",
            "delete",
            "create",
            "stop",
            "restore",
            "history",
        }
    )

    def __init__(self, registry: CommandRegistry) -> None:
        self._registry = registry

    async def dispatch(self, line: str, app: ChatApp) -> bool:
        """Dispatch a slash command line, mounting results as TUI widgets.

        Returns True if the command was handled, False if not a command.
        """
        stripped = line.strip()
        if not stripped.startswith("/"):
            return False

        parts = stripped.split(maxsplit=1)
        cmd_name = parts[0][1:]
        args = parts[1] if len(parts) > 1 else ""

        # TUI-overridden commands
        if cmd_name in self._TUI_OVERRIDES:
            return await self._dispatch_override(cmd_name, args, app)

        # Generic dispatch: capture stdout + renderer errors
        return await self._dispatch_generic(cmd_name, args, app)

    async def _dispatch_override(
        self,
        cmd_name: str,
        args: str,
        app: ChatApp,
    ) -> bool:
        """Route to TUI-specific command handlers."""
        if cmd_name == "quit":
            return await self._handle_quit(app)
        if cmd_name == "switch":
            return await self._handle_switch(args, app)
        if cmd_name == "reconnect":
            return await self._handle_reconnect(app)
        if cmd_name == "delete":
            return await self._handle_delete(args, app)
        if cmd_name == "create":
            return await self._handle_create(args, app)
        if cmd_name == "stop":
            return await self._handle_stop(app)
        if cmd_name == "history":
            return await self._handle_history(args, app)
        return await self._handle_restore(args, app)

    async def _dispatch_generic(
        self,
        cmd_name: str,
        args: str,
        app: ChatApp,
    ) -> bool:
        """Dispatch via CommandRegistry with stdout capture."""
        if cmd_name not in self._registry.commands:
            await self._mount_system(
                app,
                f"Unknown command: /{cmd_name}. Type /help for commands.",
            )
            return True

        session = _TuiSession(app, self._registry)
        buffer = io.StringIO()
        try:
            with redirect_stdout(buffer):
                await self._registry.commands[cmd_name].handler(
                    args,
                    session,  # type: ignore[arg-type]
                )
        except ApiError as exc:
            await self._mount_error(app, f"Command error: {exc.detail}")
            return True
        except Exception as exc:  # noqa: BLE001
            await self._mount_error(app, f"Command failed: {exc}")
            return True

        # Mount captured errors first
        for err in session._error_buffer:
            await self._mount_error(app, err)

        # Mount captured stdout
        output = buffer.getvalue().strip()
        if output:
            await self._mount_system(app, output)

        return True

    async def _handle_quit(self, app: ChatApp) -> bool:
        """Handle /quit by exiting the app."""
        app.exit()
        return True

    async def _handle_switch(self, args: str, app: ChatApp) -> bool:
        """Handle /switch <team_id> with worker restart and StatusHeader update."""
        new_team_id = args.strip()
        if not new_team_id:
            await self._mount_system(app, "Usage: /switch <team_id>")
            return True

        conn = app._connection_manager  # noqa: SLF001
        if conn is None:
            await self._mount_error(app, "No connection manager available.")
            return True

        try:
            await conn.switch_team(new_team_id)
        except Exception as exc:  # noqa: BLE001
            await self._mount_error(app, f"Switch failed: {exc}")
            return True

        # Update app state
        app._team_id = new_team_id  # noqa: SLF001

        # Try to fetch team info for StatusHeader update
        client = app._client  # noqa: SLF001
        team_name = new_team_id
        team_status = "running"
        if client is not None:
            try:
                team_info = client.get_team(new_team_id)
                team_name = team_info.name
                team_status = team_info.status
                app._team_name = team_name  # noqa: SLF001
                app._team_status = team_status  # noqa: SLF001
            except ApiError as exc:
                _log.debug("Failed to fetch team info after switch: %s", exc.detail)

        # Update StatusHeader
        from akgentic.infra.cli.tui.widgets.status_header import StatusHeader

        try:
            app.query_one(StatusHeader).update_team(team_name, new_team_id, team_status)
        except Exception:  # noqa: BLE001
            _log.debug("StatusHeader not available for team update")

        await self._mount_system(app, f"Switched to team {team_name} ({new_team_id})")

        # Restart streaming worker (exclusive=True auto-cancels previous)
        app.stream_events()

        return True

    async def _handle_reconnect(self, app: ChatApp) -> bool:
        """Handle /reconnect by triggering ConnectionManager.connect()."""
        conn = app._connection_manager  # noqa: SLF001
        if conn is None:
            await self._mount_error(app, "No connection manager available.")
            return True

        from akgentic.infra.cli.tui.widgets.status_header import StatusHeader

        try:
            await conn.connect()
        except Exception as exc:  # noqa: BLE001
            try:
                app.query_one(StatusHeader).update_connection("disconnected")
            except Exception:  # noqa: BLE001
                _log.debug("StatusHeader not available for connection update")
            await self._mount_error(app, f"Reconnection failed: {exc}")
            return True

        try:
            app.query_one(StatusHeader).update_connection("connected")
        except Exception:  # noqa: BLE001
            _log.debug("StatusHeader not available for connection update")

        await self._mount_system(app, "Reconnected to server.")

        # Restart streaming
        app.stream_events()

        return True

    async def _handle_delete(self, args: str, app: ChatApp) -> bool:
        """Handle /delete with two-step confirmation flow.

        /delete <team_id> -> warning message
        /delete confirm <team_id> -> actual deletion
        """
        parts = args.strip().split(maxsplit=1)
        client = app._client  # noqa: SLF001

        if not parts or not parts[0]:
            target_id = app._team_id  # noqa: SLF001
            await self._mount_system(
                app,
                f"To delete team {target_id}, type: /delete confirm {target_id}",
            )
            return True

        if parts[0] == "confirm":
            target_id = parts[1].strip() if len(parts) > 1 else app._team_id  # noqa: SLF001
            return await self._execute_delete(target_id, app, client)

        # First call with team_id -- show warning
        target_id = parts[0]
        team_label = target_id
        if client is not None:
            try:
                team_info = client.get_team(target_id)
                team_label = f"{team_info.name} ({target_id})"
            except ApiError:
                pass

        await self._mount_system(
            app,
            f"Warning: Delete team {team_label}? All event history will be lost.\n"
            f"To confirm, type: /delete confirm {target_id}",
        )
        return True

    async def _execute_delete(
        self,
        target_id: str,
        app: ChatApp,
        client: ApiClient | None,
    ) -> bool:
        """Execute the actual team deletion."""
        if client is None:
            await self._mount_error(app, "No API client available.")
            return True

        try:
            client.delete_team(target_id)
        except ApiError as exc:
            await self._mount_error(app, f"Error deleting team: {exc.detail}")
            return True

        from akgentic.infra.cli.tui.widgets.status_header import StatusHeader

        await self._mount_system(app, f"Team {target_id} deleted.")
        if target_id == app._team_id:  # noqa: SLF001
            await self._mount_system(
                app, "Current team deleted. Use /switch or /teams to select another team."
            )
            try:
                app.query_one(StatusHeader).update_team("(deleted)", target_id, "deleted")
            except Exception:  # noqa: BLE001
                _log.debug("StatusHeader not available for delete update")

        return True

    async def _handle_create(self, args: str, app: ChatApp) -> bool:
        """Handle /create <catalog_entry> with StatusHeader update and auto-switch."""
        catalog_entry = args.strip()
        if not catalog_entry:
            await self._mount_system(app, "Usage: /create <catalog_entry>")
            return True

        client = app._client  # noqa: SLF001
        if client is None:
            await self._mount_error(app, "No API client available.")
            return True

        try:
            team = client.create_team(catalog_entry)
        except ApiError as exc:
            await self._mount_error(app, f"Error creating team: {exc.detail}")
            return True

        await self._mount_system(app, f"Created team: {team.name} ({team.team_id})")

        # Auto-switch to the new team
        return await self._handle_switch(team.team_id, app)

    async def _handle_stop(self, app: ChatApp) -> bool:
        """Handle /stop with StatusHeader update."""
        client = app._client  # noqa: SLF001
        if client is None:
            await self._mount_error(app, "No API client available.")
            return True

        try:
            client.stop_team(app._team_id)  # noqa: SLF001
        except ApiError as exc:
            await self._mount_error(app, f"Error stopping team: {exc.detail}")
            return True

        app._team_status = "stopped"  # noqa: SLF001

        from akgentic.infra.cli.tui.widgets.status_header import StatusHeader

        try:
            app.query_one(StatusHeader).update_team(
                app._team_name,  # noqa: SLF001
                app._team_id,  # noqa: SLF001
                "stopped",
            )
        except Exception:  # noqa: BLE001
            _log.debug("StatusHeader not available for stop update")

        await self._mount_system(app, f"Team {app._team_id} stopped.")  # noqa: SLF001
        return True

    async def _handle_restore(self, args: str, app: ChatApp) -> bool:
        """Handle /restore [team_id] with StatusHeader update and auto-switch."""
        target_id = args.strip() or app._team_id  # noqa: SLF001
        client = app._client  # noqa: SLF001
        if client is None:
            await self._mount_error(app, "No API client available.")
            return True

        try:
            client.restore_team(target_id)
        except ApiError as exc:
            await self._mount_error(app, f"Error restoring team: {exc.detail}")
            return True

        await self._mount_system(app, f"Team {target_id} restored. Live events resumed.")

        # Auto-switch to restored team
        return await self._handle_switch(target_id, app)

    async def _handle_history(self, args: str, app: ChatApp) -> bool:
        """Handle /history by fetching events and mounting them as TUI widgets."""
        limit = self._parse_history_limit(args)
        if limit is None:
            await self._mount_system(app, "Usage: /history [N] — N must be a positive integer.")
            return True

        client = app._client  # noqa: SLF001
        if client is None:
            await self._mount_error(app, "No API client available.")
            return True

        loop = asyncio.get_running_loop()
        try:
            events = await loop.run_in_executor(
                None,
                client.get_events,
                app._team_id,  # noqa: SLF001
            )
        except ApiError as exc:
            await self._mount_error(app, f"Error fetching history: {exc.detail}")
            return True

        event_router = app._event_router  # noqa: SLF001
        color_registry = app._color_registry  # noqa: SLF001
        if event_router is None:
            await self._mount_system(app, "No event router available for history rendering.")
            return True

        displayed = await self._render_history_events(
            app, events[-limit:], event_router, color_registry
        )
        if displayed == 0:
            await self._mount_system(app, "No displayable history events found.")

        return True

    @staticmethod
    def _parse_history_limit(args: str) -> int | None:
        """Parse the optional limit argument for /history. Returns None on invalid input."""
        stripped = args.strip()
        if not stripped:
            return 20
        try:
            value = int(stripped)
        except ValueError:
            return None
        return value if value > 0 else None

    @staticmethod
    async def _render_history_events(
        app: ChatApp,
        events: list[EventInfo],
        event_router: EventRouter,
        color_registry: AgentColorRegistry,
    ) -> int:
        """Render history events as TUI widgets. Returns count of displayed widgets."""
        from textual.containers import VerticalScroll

        from akgentic.core.messages.message import Message

        conversation = app.query_one("#conversation", VerticalScroll)
        displayed = 0
        for e in events:
            if not isinstance(e.event, Message):
                continue
            widget = event_router.to_widget(e.event, color_registry)
            if widget is not None:
                await conversation.mount(widget)
                widget.scroll_visible(animate=False)
                displayed += 1
        return displayed

    async def _mount_system(self, app: ChatApp, text: str) -> None:
        """Mount a SystemMessage widget in the conversation area."""
        from textual.containers import VerticalScroll

        from akgentic.infra.cli.tui.widgets.system_message import SystemMessage

        conversation = app.query_one("#conversation", VerticalScroll)
        widget = SystemMessage(content=text)
        await conversation.mount(widget)
        widget.scroll_visible(animate=False)

    async def _mount_error(self, app: ChatApp, text: str) -> None:
        """Mount an ErrorWidget in the conversation area."""
        from textual.containers import VerticalScroll

        from akgentic.infra.cli.tui.widgets.error import ErrorWidget

        conversation = app.query_one("#conversation", VerticalScroll)
        widget = ErrorWidget(content=text)
        await conversation.mount(widget)
        widget.scroll_visible(animate=False)
