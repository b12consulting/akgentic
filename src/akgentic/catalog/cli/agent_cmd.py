"""Agent CRUD subcommands for the catalog CLI."""

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
from akgentic.catalog.models.agent import AgentEntry
from akgentic.catalog.models.errors import CatalogValidationError, EntryNotFoundError
from akgentic.catalog.models.queries import AgentQuery

if TYPE_CHECKING:
    from akgentic.catalog.cli.main import GlobalState

__all__ = ["agent_app"]

logger = logging.getLogger(__name__)

err_console = Console(stderr=True)

agent_app = typer.Typer(name="agent", help="Manage agent catalog entries.")


def _state(ctx: typer.Context) -> GlobalState:
    """Retrieve global state from context."""
    from akgentic.catalog.cli.main import get_state

    return get_state(ctx)


def _load_entry_from_yaml(file: Path) -> AgentEntry:
    """Load an AgentEntry from a YAML file.

    Args:
        file: Path to the YAML file.

    Returns:
        A validated AgentEntry instance.

    Raises:
        typer.Exit: If the file cannot be read or parsed.
    """
    try:
        data = yaml.safe_load(file.read_text())
    except (OSError, yaml.YAMLError) as exc:
        err_console.print(f"[red]Error reading file:[/red] {exc}")
        raise typer.Exit(code=1) from exc
    try:
        return AgentEntry.model_validate(data)
    except (ValidationError, ValueError) as exc:
        err_console.print(f"[red]Validation error:[/red] {exc}")
        raise typer.Exit(code=1) from exc


@agent_app.command("list")
def list_agents(ctx: typer.Context) -> None:
    """List all agent entries."""
    state = _state(ctx)
    _, _, agent_catalog, _ = build_catalogs_from_state(state)
    entries = agent_catalog.list()
    render(entries, state.format)


@agent_app.command("get")
def get_agent(ctx: typer.Context, agent_id: str = typer.Argument(help="Agent ID")) -> None:
    """Display a single agent entry."""
    state = _state(ctx)
    _, _, agent_catalog, _ = build_catalogs_from_state(state)
    entry = agent_catalog.get(agent_id)
    if entry is None:
        err_console.print(f"[red]Not found:[/red] Agent '{agent_id}' not found")
        raise typer.Exit(code=1)
    render(entry, state.format)


@agent_app.command("create")
def create_agent(
    ctx: typer.Context,
    yaml_file: Path = typer.Argument(help="Path to YAML file with agent data"),
) -> None:
    """Create a new agent entry from a YAML file."""
    state = _state(ctx)
    entry = _load_entry_from_yaml(yaml_file)
    _, _, agent_catalog, _ = build_catalogs_from_state(state)
    try:
        created_id = agent_catalog.create(entry)
    except CatalogValidationError as exc:
        err_console.print(f"[red]Validation error:[/red] {exc}")
        for err in exc.errors:
            err_console.print(f"  - {err}")
        raise typer.Exit(code=1) from exc
    err_console.print(f"[green]Created agent:[/green] {created_id}")


@agent_app.command("update")
def update_agent(
    ctx: typer.Context,
    agent_id: str = typer.Argument(help="Agent ID to update"),
    yaml_file: Path = typer.Argument(help="Path to YAML file with updated agent data"),
) -> None:
    """Update an existing agent entry from a YAML file."""
    state = _state(ctx)
    entry = _load_entry_from_yaml(yaml_file)
    _, _, agent_catalog, _ = build_catalogs_from_state(state)
    try:
        agent_catalog.update(agent_id, entry)
    except EntryNotFoundError as exc:
        err_console.print(f"[red]Not found:[/red] {exc}")
        raise typer.Exit(code=1) from exc
    except CatalogValidationError as exc:
        err_console.print(f"[red]Validation error:[/red] {exc}")
        for err in exc.errors:
            err_console.print(f"  - {err}")
        raise typer.Exit(code=1) from exc
    err_console.print(f"[green]Updated agent:[/green] {agent_id}")


@agent_app.command("delete")
def delete_agent(
    ctx: typer.Context,
    agent_id: str = typer.Argument(help="Agent ID to delete"),
) -> None:
    """Delete an agent entry."""
    state = _state(ctx)
    _, _, agent_catalog, _ = build_catalogs_from_state(state)
    try:
        agent_catalog.delete(agent_id)
    except EntryNotFoundError as exc:
        err_console.print(f"[red]Not found:[/red] {exc}")
        raise typer.Exit(code=1) from exc
    except CatalogValidationError as exc:
        err_console.print(f"[red]Validation error:[/red] {exc}")
        for err in exc.errors:
            err_console.print(f"  - {err}")
        raise typer.Exit(code=1) from exc
    err_console.print(f"[green]Deleted agent:[/green] {agent_id}")


@agent_app.command("search")
def search_agents(
    ctx: typer.Context,
    role: str | None = typer.Option(None, "--role", help="Filter by agent role"),
    skill: str | None = typer.Option(None, "--skill", help="Filter by agent skill"),
    description: str | None = typer.Option(
        None, "--description", help="Filter by description substring"
    ),
) -> None:
    """Search agent entries by role, skill, or description."""
    state = _state(ctx)
    _, _, agent_catalog, _ = build_catalogs_from_state(state)
    query = AgentQuery(
        role=role,
        skills=[skill] if skill else None,
        description=description,
    )
    entries = agent_catalog.search(query)
    render(entries, state.format)
