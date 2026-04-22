"""Slash command registry and dispatcher for in-session REPL commands.

Renamed from ``commands.py`` (Story 21.4 Task 0 — Option A) to free the
``commands`` name for the new top-level Typer command subpackage
(:mod:`akgentic.infra.cli.commands`) housing ``login`` / ``logout``. Python
does not permit a module and a subpackage with the same name at the same
level, so the legacy REPL slash-command registry moved here.
"""

from __future__ import annotations

import asyncio
import builtins
import json as json_mod
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from akgentic.infra.cli.client import ApiError
from akgentic.infra.cli.ws_client import WsConnectionError

if TYPE_CHECKING:
    from akgentic.infra.cli.repl import ChatSession

# Type alias for command handlers: async def handler(args: str, session: ChatSession) -> None
CommandHandler = Callable[[str, "ChatSession"], Awaitable[None]]


@dataclass
class SlashCommand:
    """A registered slash command."""

    name: str
    handler: CommandHandler
    help_text: str
    usage: str


class CommandRegistry:
    """Registry and dispatcher for slash commands."""

    def __init__(self) -> None:
        self._commands: dict[str, SlashCommand] = {}

    def register(
        self,
        name: str,
        handler: CommandHandler,
        help_text: str,
        usage: str,
    ) -> None:
        """Register a slash command."""
        self._commands[name] = SlashCommand(
            name=name, handler=handler, help_text=help_text, usage=usage
        )

    async def dispatch(self, line: str, session: ChatSession) -> bool:
        """Parse and dispatch a slash command.

        Returns True if a command was handled (or an unknown /command was caught),
        False if the line is not a command (no / prefix).
        """
        stripped = line.strip()
        if not stripped.startswith("/"):
            return False

        parts = stripped.split(maxsplit=1)
        cmd_name = parts[0][1:]  # strip leading /
        args = parts[1] if len(parts) > 1 else ""

        if cmd_name in self._commands:
            await self._commands[cmd_name].handler(args, session)
            return True

        print(f"Unknown command: /{cmd_name}. Type /help for available commands.")
        return True

    @property
    def commands(self) -> dict[str, SlashCommand]:
        """Return registered commands."""
        return self._commands


# -- Built-in commands --


async def _help_handler(args: str, session: ChatSession) -> None:
    """List all registered commands."""
    registry = session.command_registry
    print("Available commands:")
    for cmd in registry.commands.values():
        print(f"  /{cmd.name:<12s} {cmd.help_text}")
        if cmd.usage:
            print(f"  {'':12s}   Usage: {cmd.usage}")


# -- Team inspection commands --


async def _teams_handler(args: str, session: ChatSession) -> None:
    """List all teams with status indicators."""
    loop = asyncio.get_running_loop()
    try:
        teams = await loop.run_in_executor(None, session.client.list_teams)
    except ApiError as exc:
        session.renderer.render_error(f"Error fetching teams: {exc.detail}")
        return

    if not teams:
        print("No teams found.")
        return

    print(f"{'Team ID':<38s} {'Name':<20s} {'Status':<10s}")
    print("-" * 68)
    for t in teams:
        marker = " (current)" if t.team_id == session.team_id else ""
        print(f"{t.team_id:<38s} {t.name:<20s} {t.status:<10s}{marker}")


async def _create_handler(args: str, session: ChatSession) -> None:
    """Create a team from a catalog entry and auto-switch to it."""
    catalog_entry_id = args.strip()
    if not catalog_entry_id:
        print("Usage: /create <catalog_entry>")
        return

    loop = asyncio.get_running_loop()
    try:
        team = await loop.run_in_executor(None, session.client.create_team, catalog_entry_id)
    except ApiError as exc:
        session.renderer.render_error(f"Error creating team: {exc.detail}")
        return

    print(f"Created team: {team.name} ({team.team_id})")
    await _switch_handler(team.team_id, session)


async def _delete_handler(args: str, session: ChatSession) -> None:
    """Delete a team after confirmation."""
    target_id = args.strip() or session.team_id
    loop = asyncio.get_running_loop()

    try:
        team = await loop.run_in_executor(None, session.client.get_team, target_id)
    except ApiError as exc:
        session.renderer.render_error(f"Error fetching team info: {exc.detail}")
        return

    prompt_text = f"Delete team {team.name} ({target_id})? All event history will be lost. [y/N] "
    try:
        response = await loop.run_in_executor(None, builtins.input, prompt_text)
    except (EOFError, KeyboardInterrupt):
        print("\nAborted.")
        return

    if response.strip().lower() != "y":
        print("Aborted.")
        return

    try:
        await loop.run_in_executor(None, session.client.delete_team, target_id)
    except ApiError as exc:
        session.renderer.render_error(f"Error deleting team: {exc.detail}")
        return

    print(f"Team {team.name} ({target_id}) deleted.")
    if target_id == session.team_id:
        print("Current team deleted. Use /switch or /teams to select another team.")


async def _info_handler(args: str, session: ChatSession) -> None:
    """Show team details."""
    target_id = args.strip() or session.team_id
    loop = asyncio.get_running_loop()
    try:
        team = await loop.run_in_executor(None, session.client.get_team, target_id)
    except ApiError as exc:
        session.renderer.render_error(f"Error fetching team info: {exc.detail}")
        return

    print(f"Name: {team.name}")
    print(f"Team ID: {team.team_id}")
    print(f"Status: {team.status}")
    print(f"User ID: {team.user_id}")
    print(f"Created: {team.created_at}")
    print(f"Updated: {team.updated_at}")


async def _events_handler(args: str, session: ChatSession) -> None:
    """Show raw team events."""
    limit = 20
    if args.strip():
        try:
            limit = int(args.strip())
        except ValueError:
            print("Usage: /events [N]  — N must be a positive integer.")
            return
        if limit <= 0:
            print("Usage: /events [N]  — N must be a positive integer.")
            return

    loop = asyncio.get_running_loop()
    try:
        events = await loop.run_in_executor(None, session.client.get_events, session.team_id)
    except ApiError as exc:
        session.renderer.render_error(f"Error fetching events: {exc.detail}")
        return

    for evt in events[-limit:]:
        print(json_mod.dumps(evt.model_dump(), indent=2, default=str))


async def _agents_handler(args: str, session: ChatSession) -> None:
    """List team members with roles and state."""
    from akgentic.core.messages.orchestrator import StartMessage

    loop = asyncio.get_running_loop()
    try:
        events = await loop.run_in_executor(None, session.client.get_events, session.team_id)
    except ApiError as exc:
        session.renderer.render_error(f"Error fetching agents: {exc.detail}")
        return

    seen: dict[str, str] = {}
    for e in events:
        if isinstance(e.event, StartMessage):
            name = e.event.config.name
            role = e.event.config.role
            if name and name != "orchestrator":
                seen[name] = role

    if not seen:
        print("No agents found.")
        return

    print("Team agents:")
    for name, role in seen.items():
        print(f"  {name:<16s} ({role})")


# -- History command --


async def _history_handler(args: str, session: ChatSession) -> None:
    """Show recent messages."""
    limit = 20
    if args.strip():
        try:
            limit = int(args.strip())
        except ValueError:
            print("Usage: /history [N]  — N must be a positive integer.")
            return
        if limit <= 0:
            print("Usage: /history [N]  — N must be a positive integer.")
            return

    loop = asyncio.get_running_loop()
    try:
        events = await loop.run_in_executor(None, session.client.get_events, session.team_id)
    except ApiError as exc:
        session.renderer.render_error(f"Error fetching history: {exc.detail}")
        return

    # Pass typed Message instances to the rendering pipeline
    from akgentic.core.messages.message import Message
    from akgentic.core.messages.orchestrator import ErrorMessage, EventMessage, SentMessage

    displayable = [
        e.event for e in events if isinstance(e.event, (SentMessage, ErrorMessage, EventMessage))
    ]
    for evt in displayable[-limit:]:
        if isinstance(evt, Message):
            session._render_event(evt)


# -- Workspace commands --


async def _files_handler(args: str, session: ChatSession) -> None:
    """Show workspace file tree."""
    loop = asyncio.get_running_loop()
    try:
        tree = await loop.run_in_executor(None, session.client.workspace_tree, session.team_id)
    except ApiError as exc:
        session.renderer.render_error(f"Error fetching file tree: {exc.detail}")
        return

    if not tree.entries:
        print("(empty workspace)")
        return
    for entry in tree.entries:
        prefix = "D " if entry.is_dir else "  "
        suffix = f"  ({entry.size} bytes)" if not entry.is_dir else ""
        print(f"{prefix}{entry.name}{suffix}")


async def _read_handler(args: str, session: ChatSession) -> None:
    """Read a file from the workspace."""
    path = args.strip()
    if not path:
        print("Usage: /read <path>")
        return

    loop = asyncio.get_running_loop()
    try:
        data = await loop.run_in_executor(
            None, session.client.workspace_read, session.team_id, path
        )
    except ApiError as exc:
        session.renderer.render_error(f"Error reading file {path}: {exc.detail}")
        return

    try:
        print(data.decode("utf-8"), end="")
    except UnicodeDecodeError:
        print(f"(binary file, {len(data)} bytes)")


async def _upload_handler(args: str, session: ChatSession) -> None:
    """Upload a local file to the workspace."""
    local_path = args.strip()
    if not local_path:
        print("Usage: /upload <local_path>")
        return

    loop = asyncio.get_running_loop()
    p = Path(local_path)

    is_file = await loop.run_in_executor(None, p.is_file)
    if not is_file:
        print(f"Error: {local_path} is not a file or does not exist.")
        return

    file_data = await loop.run_in_executor(None, p.read_bytes)
    try:
        result = await loop.run_in_executor(
            None, session.client.workspace_upload, session.team_id, p.name, file_data
        )
    except ApiError as exc:
        session.renderer.render_error(f"Error uploading file {local_path}: {exc.detail}")
        return

    print(f"Uploaded {result.path} ({result.size} bytes)")


# -- Lifecycle commands --


async def _stop_handler(args: str, session: ChatSession) -> None:
    """Stop the team."""
    loop = asyncio.get_running_loop()
    try:
        await loop.run_in_executor(None, session.client.stop_team, session.team_id)
    except ApiError as exc:
        session.renderer.render_error(f"Error stopping team: {exc.detail}")
        return

    print(f"Team {session.team_id} stopped.")


async def _restore_handler(args: str, session: ChatSession) -> None:
    """Restore a stopped team, optionally by ID."""
    target_id = args.strip() or session.team_id
    loop = asyncio.get_running_loop()
    try:
        await loop.run_in_executor(None, session.client.restore_team, target_id)
    except ApiError as exc:
        session.renderer.render_error(f"Error restoring team: {exc.detail}")
        return

    print(f"Team {target_id} restored. Live events resumed.")

    # Reconnect WebSocket — either switch to the new team or
    # re-establish connection for the current team after restore
    await _switch_handler(target_id, session)


# -- Switch command --


async def _switch_handler(args: str, session: ChatSession) -> None:
    """Switch to a different team."""
    new_team_id = args.strip()
    if not new_team_id:
        print("Usage: /switch <team_id>")
        return

    try:
        await session.conn.switch_team(new_team_id)
    except WsConnectionError as exc:
        session.renderer.render_error(f"Switch failed: {exc.reason}")
        return

    session.team_id = new_team_id

    # Cancel old receive loop before starting new one
    if session._receive_task is not None:
        session._receive_task.cancel()
        try:
            await session._receive_task
        except asyncio.CancelledError:
            pass

    # Refresh team info for status bar
    session._fetch_team_info()
    session.renderer.render_border()

    session._receive_task = asyncio.create_task(session._receive_loop())


async def _reconnect_handler(args: str, session: ChatSession) -> None:
    """Manually trigger reconnection."""
    session.renderer.render_connection_status("reconnecting")
    try:
        await session.conn.connect()
        session.renderer.render_connection_status("connected")
    except WsConnectionError as exc:
        session.renderer.render_error(f"Reconnection failed: {exc.reason}")


async def _quit_handler(args: str, session: ChatSession) -> None:
    """Exit the session (TUI overrides this with app.exit())."""
    print("Goodbye.")


async def _catalog_handler(args: str, session: ChatSession) -> None:
    """List available team templates from the catalog."""
    loop = asyncio.get_running_loop()
    try:
        entries = await loop.run_in_executor(None, session.client.list_catalog_teams)
    except ApiError as exc:
        session.renderer.render_error(f"Error fetching catalog: {exc.detail}")
        return

    if not entries:
        print("No team templates found.")
        return

    print("Available team templates:")
    for entry in entries:
        print(f"  {entry.id:<20s} {entry.name:<24s} {entry.description}")


def build_default_registry() -> CommandRegistry:
    """Create a command registry with all built-in commands."""
    registry = CommandRegistry()
    registry.register("help", _help_handler, "Show available commands", "/help")
    registry.register("teams", _teams_handler, "List all teams", "/teams")
    registry.register(
        "create", _create_handler, "Create a team from catalog", "/create <catalog_entry>"
    )
    registry.register("catalog", _catalog_handler, "List team templates", "/catalog")
    registry.register("delete", _delete_handler, "Delete a team", "/delete [team_id]")
    registry.register("info", _info_handler, "Show team details", "/info [team_id]")
    registry.register("events", _events_handler, "Show raw team events", "/events [N]")
    registry.register("agents", _agents_handler, "List team agents", "/agents")
    registry.register("history", _history_handler, "Show recent messages", "/history [N]")
    registry.register("files", _files_handler, "Show workspace files", "/files")
    registry.register("read", _read_handler, "Read a workspace file", "/read <path>")
    registry.register("upload", _upload_handler, "Upload a file to workspace", "/upload <path>")
    registry.register("stop", _stop_handler, "Stop the team", "/stop")
    registry.register("restore", _restore_handler, "Restore a stopped team", "/restore [team_id]")
    registry.register("switch", _switch_handler, "Switch to another team", "/switch <team_id>")
    registry.register("reconnect", _reconnect_handler, "Reconnect to server", "/reconnect")
    registry.register("quit", _quit_handler, "Exit the chat", "/quit")
    return registry
