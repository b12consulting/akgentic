"""Typer CLI application for akgentic-infra.

Profile-driven auth (story 22.5, ADR-021 §Decision 1 / §Decision 2)
-------------------------------------------------------------------

The top-level ``@app.callback()`` resolves which HTTP client is handed to
:class:`ApiClient` per-invocation. Two branches:

* **No config file present** — the callback silently falls back to the legacy
  path and constructs ``ApiClient(base_url=server, api_key=api_key)`` exactly
  as it did before 22.5. No profile resolution runs. No ``OidcTokenProvider``
  is instantiated. No device-code traffic can occur. This is the
  backward-compat invariant (AC #1 / AC #5 / AC #9).

* **Config file present** — ``resolve_profile`` picks the active profile;
  :func:`build_http_client_with_auto_auth` returns an auth-wired
  ``httpx.Client``; that client is handed to :class:`ApiClient` via the
  pre-built-client constructor path.

Flag precedence (AC #4) uses **Shape P1** — we branch on ``--api-key`` and
``--server`` **before** calling the factory rather than mutating the client
post-hoc. ``--api-key`` fully preempts OIDC (escape hatch for pre-resolved
credentials); ``--server`` rewires the profile-driven client's base URL while
keeping auto-auth intact.
"""

from __future__ import annotations

import logging
import os
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Annotated

import httpx
import typer
from pydantic import BaseModel

from akgentic.infra.cli.auth import DeviceAuthorizationResponse
from akgentic.infra.cli.client import ApiClient, ApiError
from akgentic.infra.cli.commands import catalog as catalog_command
from akgentic.infra.cli.commands import channel as channel_command
from akgentic.infra.cli.commands import login as login_command
from akgentic.infra.cli.commands import logout as logout_command
from akgentic.infra.cli.config import (
    ConfigFileNotFoundError,
    ProfileConfig,
    ProfileConfigError,
    load_config,
    resolve_profile,
)
from akgentic.infra.cli.formatters import OutputFormat, format_output
from akgentic.infra.cli.http import build_http_client_with_auto_auth
from akgentic.infra.cli.renderers import RichRenderer
from akgentic.infra.cli.tui.app import ChatApp

app = typer.Typer(name="ak-infra", help="Akgentic Infrastructure CLI")
team_app = typer.Typer(name="team", help="Manage agent teams")
workspace_app = typer.Typer(name="workspace", help="Manage team workspace files")

app.add_typer(team_app, name="team")
app.add_typer(workspace_app, name="workspace")

# Register top-level login/logout commands (Story 22.4).
login_command.register(app)
logout_command.register(app)
catalog_command.register(app)
channel_command.register(app)


# Default server — kept as a module constant so tests can compare against it
# to detect whether the user actually supplied ``--server``. (Typer does not
# easily distinguish "default" from "explicit default value" without sentinels;
# we use this value-comparison approach.)
_DEFAULT_SERVER = "http://localhost:8000"

# -- test seams (monkeypatched by tests; production leaves them ``None``) --
#
# These mirror the pattern established in :mod:`akgentic.infra.cli.commands.login`
# (story 22.4) and :mod:`commands.logout`. Production leaves all of them ``None``
# and the callback falls through to the real ``~/.akgentic/`` paths.
_CONFIG_PATH_OVERRIDE: Path | None = None
_CREDENTIALS_DIR_OVERRIDE: Path | None = None
# Optional factory swap for tests that want to inject a fully pre-built client
# without threading transport through every layer. Production leaves ``None``
# and the callback calls :func:`build_http_client_with_auto_auth` directly.
_HTTP_CLIENT_FACTORY_OVERRIDE: Callable[..., httpx.Client] | None = None


# -- shared state --


class _State:
    """Holds resolved global options for commands."""

    client: ApiClient
    fmt: OutputFormat
    server: str
    api_key: str | None
    # The resolved profile name on the config-file branch, else ``None`` on
    # the legacy no-config path. Stored for future debug / observability
    # hooks; not exposed via any command in this story.
    profile_name: str | None = None


_state = _State()


def _to_serializable(data: object) -> object:
    """Convert Pydantic models (or lists of them) to dicts for formatting."""
    if isinstance(data, list):
        return [item.model_dump() if isinstance(item, BaseModel) else item for item in data]
    if isinstance(data, BaseModel):
        return data.model_dump()
    return data


def _print(data: object, columns: list[str] | None = None) -> None:
    """Print formatted output using the current output format."""
    typer.echo(format_output(_to_serializable(data), _state.fmt, columns))


def _emit_user_code(auth: DeviceAuthorizationResponse) -> None:
    """Write device-code instructions to stderr.

    Mirrors the ``_emit_user_code`` helper in
    :mod:`akgentic.infra.cli.commands.login` — kept local here to avoid a
    circular import between :mod:`main` and :mod:`commands.login`.
    """
    message = (
        f"To authenticate, visit: {auth.verification_uri}\nAnd enter the code: {auth.user_code}\n"
    )
    if auth.verification_uri_complete:
        message += f"Or open directly: {auth.verification_uri_complete}\n"
    sys.stderr.write(message)
    sys.stderr.flush()


def _build_profile_driven_client(
    profile: ProfileConfig,
    profile_name: str,
) -> httpx.Client:
    """Construct an ``httpx.Client`` via the factory (or the test override)."""
    factory = _HTTP_CLIENT_FACTORY_OVERRIDE
    if factory is not None:
        return factory(
            profile,
            profile_name=profile_name,
            credentials_dir=_CREDENTIALS_DIR_OVERRIDE,
            on_user_code=_emit_user_code,
        )
    return build_http_client_with_auto_auth(
        profile,
        profile_name=profile_name,
        credentials_dir=_CREDENTIALS_DIR_OVERRIDE,
        on_user_code=_emit_user_code,
    )


def _configure_legacy(server: str, api_key: str | None, fmt: OutputFormat) -> None:
    """Install a legacy ``ApiClient(base_url=..., api_key=...)`` on ``_state``."""
    _state.client = ApiClient(base_url=server, api_key=api_key)
    _state.fmt = fmt
    _state.server = server
    _state.api_key = api_key
    _state.profile_name = None


def _configure_profile(
    *,
    profile: ProfileConfig,
    profile_name: str,
    server: str,
    api_key: str | None,
    fmt: OutputFormat,
    server_is_explicit: bool,
) -> None:
    """Wire ``_state`` from a resolved profile, honoring flag overrides.

    Flag precedence (AC #4 / Shape P1):

    * ``--api-key`` supplied → legacy ``ApiClient`` short-circuits OIDC entirely.
    * ``--server`` supplied → profile-driven client rebound to that base_url
      (``httpx.Client.base_url`` is a writable property on httpx ≥ 0.24 —
      documented in story 22.5 Dev Notes §"Latest Tech Information").
    * Neither flag → profile-driven client used verbatim.
    """
    if api_key is not None:
        # Pre-resolved bearer: escape hatch — bypass profile-driven auth.
        effective_server = server if server_is_explicit else str(profile.endpoint)
        _state.client = ApiClient(base_url=effective_server, api_key=api_key)
        _state.fmt = fmt
        _state.server = effective_server
        _state.api_key = api_key
        _state.profile_name = profile_name
        return

    http_client = _build_profile_driven_client(profile, profile_name)
    if server_is_explicit:
        # Override base_url on the factory-built client. httpx ≥ 0.24 accepts
        # ``str`` on the setter.
        http_client.base_url = server
        effective_server = server
    else:
        effective_server = str(profile.endpoint)
    _state.client = ApiClient(http_client=http_client)
    _state.fmt = fmt
    _state.server = effective_server
    _state.api_key = None
    _state.profile_name = profile_name


def _handle_no_config(
    *,
    profile_flag: str | None,
    server: str,
    api_key: str | None,
    fmt: OutputFormat,
) -> None:
    """Legacy fallback when ``~/.akgentic/config.yaml`` does not exist."""
    if profile_flag is not None:
        typer.echo(
            (
                "No config file at ~/.akgentic/config.yaml — --profile requires a "
                "config file. Create one with a 'profiles:' block or omit --profile "
                "to use the --server / --api-key flags."
            ),
            err=True,
        )
        raise typer.Exit(code=1)
    _configure_legacy(server, api_key, fmt)


# -- global callback --


@app.callback()
def main(
    server: Annotated[str, typer.Option("--server", help="Server base URL")] = _DEFAULT_SERVER,
    api_key: Annotated[
        str | None, typer.Option("--api-key", help="API key for authentication")
    ] = None,
    fmt: Annotated[
        OutputFormat, typer.Option("--format", help="Output format")
    ] = OutputFormat.table,
    profile: Annotated[
        str | None,
        typer.Option(
            "--profile",
            help=(
                "Profile name from ~/.akgentic/config.yaml "
                "(overrides AKGENTIC_PROFILE and default_profile)."
            ),
        ),
    ] = None,
) -> None:
    """Akgentic Infrastructure CLI — manage teams, messaging, and workspace."""
    server_is_explicit = server != _DEFAULT_SERVER

    try:
        config = load_config(_CONFIG_PATH_OVERRIDE)
    except ConfigFileNotFoundError:
        _handle_no_config(
            profile_flag=profile,
            server=server,
            api_key=api_key,
            fmt=fmt,
        )
        return
    except ProfileConfigError as exc:
        typer.echo(f"Cannot load profile config: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    try:
        profile_name, active_profile = resolve_profile(
            config,
            cli_profile=profile,
            env=os.environ,
        )
    except ProfileConfigError as exc:
        typer.echo(f"Cannot resolve profile: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    _configure_profile(
        profile=active_profile,
        profile_name=profile_name,
        server=server,
        api_key=api_key,
        fmt=fmt,
        server_is_explicit=server_is_explicit,
    )


# -- team commands --

_team_columns = ["team_id", "name", "status", "created_at"]
_team_detail_columns = ["team_id", "name", "status", "user_id", "created_at", "updated_at"]


@team_app.command("list")
def team_list() -> None:
    """List all teams."""
    teams = _state.client.list_teams()
    _print(teams, _team_columns)


@team_app.command("get")
def team_get(team_id: str) -> None:
    """Show team detail."""
    team = _state.client.get_team(team_id)
    _print(team, _team_detail_columns)


@team_app.command("create")
def team_create(catalog_entry: str) -> None:
    """Create a team from a catalog entry."""
    team = _state.client.create_team(catalog_entry)
    _print(team, _team_detail_columns)


@team_app.command("delete")
def team_delete(team_id: str) -> None:
    """Delete a team."""
    _state.client.delete_team(team_id)
    if _state.fmt == OutputFormat.table:
        typer.echo(f"Team {team_id} deleted.")
    else:
        _print({"team_id": team_id, "status": "deleted"})


@team_app.command("restore")
def team_restore(team_id: str) -> None:
    """Restore a stopped team."""
    team = _state.client.restore_team(team_id)
    _print(team, _team_detail_columns)


@team_app.command("events")
def team_events(team_id: str) -> None:
    """Show team events."""
    events = _state.client.get_events(team_id)
    _print(events, ["sequence", "timestamp", "event"])


# -- top-level message / reply --


@app.command("message")
def message(team_id: str, content: str) -> None:
    """Send a message to a team (non-interactive)."""
    _state.client.send_message(team_id, content)
    if _state.fmt == OutputFormat.table:
        typer.echo("Message sent.")
    else:
        _print({"team_id": team_id, "status": "sent"})


@app.command("reply")
def reply(
    team_id: str,
    content: str,
    message_id: Annotated[str, typer.Option("--message-id", help="Original message ID")],
) -> None:
    """Reply with human input to an agent request."""
    _state.client.human_input(team_id, content, message_id)
    if _state.fmt == OutputFormat.table:
        typer.echo("Reply sent.")
    else:
        _print({"team_id": team_id, "message_id": message_id, "status": "sent"})


# -- chat --


@app.command("chat")
def chat(
    team_id: Annotated[str | None, typer.Argument(help="Team ID to chat with")] = None,
    create: Annotated[
        str | None, typer.Option("--create", help="Create team from catalog entry first")
    ] = None,
) -> None:
    """Interactive chat REPL — connect to a team via WebSocket."""
    from akgentic.infra.cli.connection import ConnectionManager
    from akgentic.infra.cli.event_router import EventRouter
    from akgentic.infra.cli.repl_commands import build_default_registry

    renderer = RichRenderer()

    if create is not None:
        team = _state.client.create_team(create)
        team_id = team.team_id

    # Build shared dependencies for the TUI
    command_registry = build_default_registry()
    event_router = EventRouter(renderer)

    try:
        if team_id is not None:
            team_info = _state.client.get_team(team_id)
            conn = ConnectionManager(
                server_url=_state.server,
                team_id=team_id,
                api_key=_state.api_key,
            )
            tui_app = ChatApp(
                team_name=team_info.name,
                team_id=team_id,
                team_status=team_info.status,
                connection_manager=conn,
                event_router=event_router,
                command_registry=command_registry,
                client=_state.client,
            )
        else:
            # No team_id and no --create: let TeamSelectScreen handle it.
            # ConnectionManager will be created after team selection via /switch.
            conn = ConnectionManager(
                server_url=_state.server,
                team_id="",
                api_key=_state.api_key,
            )
            tui_app = ChatApp(
                connection_manager=conn,
                event_router=event_router,
                command_registry=command_registry,
                client=_state.client,
            )
        tui_app.run()
    except KeyboardInterrupt:
        pass
    except ApiError as exc:
        renderer.render_error(f"Server error: {exc}")
        raise typer.Exit(code=1) from exc
    except Exception as exc:
        renderer.render_error(f"Unexpected error: {exc}")
        logging.getLogger(__name__).exception("Unhandled exception in TUI")
        raise typer.Exit(code=1) from exc
    finally:
        _state.client.close()


# -- workspace commands --


@workspace_app.command("tree")
def workspace_tree(team_id: str) -> None:
    """Show workspace file tree."""
    tree = _state.client.workspace_tree(team_id)
    if _state.fmt != OutputFormat.table:
        _print(tree.model_dump())
        return
    if not tree.entries:
        typer.echo("(empty workspace)")
        return
    for entry in tree.entries:
        prefix = "📁 " if entry.is_dir else "   "
        suffix = f"  ({entry.size} bytes)" if not entry.is_dir else ""
        typer.echo(f"{prefix}{entry.name}{suffix}")


@workspace_app.command("read")
def workspace_read(team_id: str, path: str) -> None:
    """Read a file from the workspace."""
    data = _state.client.workspace_read(team_id, path)
    try:
        typer.echo(data.decode("utf-8"), nl=False)
    except UnicodeDecodeError:
        typer.echo(f"(binary file, {len(data)} bytes)")


@workspace_app.command("upload")
def workspace_upload(team_id: str, local_path: str) -> None:
    """Upload a local file to the workspace."""
    p = Path(local_path)
    if not p.is_file():
        typer.echo(f"Error: {local_path} is not a file", err=True)
        raise typer.Exit(code=1)
    file_data = p.read_bytes()
    result = _state.client.workspace_upload(team_id, p.name, file_data)
    typer.echo(f"Uploaded {result.path} ({result.size} bytes)")
