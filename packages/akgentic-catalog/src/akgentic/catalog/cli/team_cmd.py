"""Team CRUD subcommands for the catalog CLI."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

import typer
import yaml
from pydantic import ValidationError
from rich.console import Console

from akgentic.catalog.cli._catalog import build_catalogs_from_state
from akgentic.catalog.cli._output import render
from akgentic.catalog.models.errors import CatalogValidationError, EntryNotFoundError
from akgentic.catalog.models.queries import TeamQuery
from akgentic.catalog.models.team import TeamEntry

if TYPE_CHECKING:
    from akgentic.catalog.cli.main import GlobalState

__all__ = ["team_app"]

logger = logging.getLogger(__name__)

err_console = Console(stderr=True)

team_app = typer.Typer(name="team", help="Manage team catalog entries.")


def _state(ctx: typer.Context) -> GlobalState:
    """Retrieve global state from context."""
    from akgentic.catalog.cli.main import get_state

    return get_state(ctx)


def _load_entry_from_yaml(file: Path) -> TeamEntry:
    """Load a TeamEntry from a YAML file.

    Args:
        file: Path to the YAML file.

    Returns:
        A validated TeamEntry instance.

    Raises:
        typer.Exit: If the file cannot be read or parsed.
    """
    try:
        data = yaml.safe_load(file.read_text())
    except (OSError, yaml.YAMLError) as exc:
        err_console.print(f"[red]Error reading file:[/red] {exc}")
        raise typer.Exit(code=1) from exc
    try:
        return TeamEntry.model_validate(data)
    except ValidationError as exc:
        err_console.print(f"[red]Validation error:[/red] {exc}")
        raise typer.Exit(code=1) from exc


@team_app.command("list")
def list_teams(ctx: typer.Context) -> None:
    """List all team entries."""
    state = _state(ctx)
    _, _, _, team_catalog = build_catalogs_from_state(state)
    entries = team_catalog.list()
    render(entries, state.format)


@team_app.command("get")
def get_team(ctx: typer.Context, team_id: str = typer.Argument(help="Team ID")) -> None:
    """Display a single team entry."""
    state = _state(ctx)
    _, _, _, team_catalog = build_catalogs_from_state(state)
    entry = team_catalog.get(team_id)
    if entry is None:
        err_console.print(f"[red]Not found:[/red] Team '{team_id}' not found")
        raise typer.Exit(code=1)
    render(entry, state.format)


@team_app.command("create")
def create_team(
    ctx: typer.Context,
    yaml_file: Path = typer.Argument(help="Path to YAML file with team data"),
) -> None:
    """Create a new team entry from a YAML file."""
    state = _state(ctx)
    entry = _load_entry_from_yaml(yaml_file)
    _, _, _, team_catalog = build_catalogs_from_state(state)
    try:
        created_id = team_catalog.create(entry)
    except CatalogValidationError as exc:
        err_console.print(f"[red]Validation error:[/red] {exc}")
        for err in exc.errors:
            err_console.print(f"  - {err}")
        raise typer.Exit(code=1) from exc
    err_console.print(f"[green]Created team:[/green] {created_id}")


@team_app.command("update")
def update_team(
    ctx: typer.Context,
    team_id: str = typer.Argument(help="Team ID to update"),
    yaml_file: Path = typer.Argument(help="Path to YAML file with updated team data"),
) -> None:
    """Update an existing team entry from a YAML file."""
    state = _state(ctx)
    entry = _load_entry_from_yaml(yaml_file)
    _, _, _, team_catalog = build_catalogs_from_state(state)
    try:
        team_catalog.update(team_id, entry)
    except EntryNotFoundError as exc:
        err_console.print(f"[red]Not found:[/red] {exc}")
        raise typer.Exit(code=1) from exc
    except CatalogValidationError as exc:
        err_console.print(f"[red]Validation error:[/red] {exc}")
        for err in exc.errors:
            err_console.print(f"  - {err}")
        raise typer.Exit(code=1) from exc
    err_console.print(f"[green]Updated team:[/green] {team_id}")


@team_app.command("delete")
def delete_team(
    ctx: typer.Context,
    team_id: str = typer.Argument(help="Team ID to delete"),
) -> None:
    """Delete a team entry."""
    state = _state(ctx)
    _, _, _, team_catalog = build_catalogs_from_state(state)
    try:
        team_catalog.delete(team_id)
    except EntryNotFoundError as exc:
        err_console.print(f"[red]Not found:[/red] {exc}")
        raise typer.Exit(code=1) from exc
    except CatalogValidationError as exc:
        err_console.print(f"[red]Validation error:[/red] {exc}")
        for err in exc.errors:
            err_console.print(f"  - {err}")
        raise typer.Exit(code=1) from exc
    err_console.print(f"[green]Deleted team:[/green] {team_id}")


@team_app.command("search")
def search_teams(
    ctx: typer.Context,
    name: str | None = typer.Option(None, "--name", help="Filter by team name"),
    description: str | None = typer.Option(
        None, "--description", help="Filter by description substring"
    ),
    agent_id: str | None = typer.Option(
        None, "--agent-id", help="Filter by agent ID in team members"
    ),
) -> None:
    """Search team entries by name, description, or agent membership."""
    state = _state(ctx)
    _, _, _, team_catalog = build_catalogs_from_state(state)
    query = TeamQuery(name=name, description=description, agent_id=agent_id)
    entries = team_catalog.search(query)
    render(entries, state.format)
