"""Tool CRUD subcommands for the catalog CLI."""

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
from akgentic.catalog.models.queries import ToolQuery
from akgentic.catalog.models.tool import ToolEntry

if TYPE_CHECKING:
    from akgentic.catalog.cli.main import GlobalState

__all__ = ["tool_app"]

logger = logging.getLogger(__name__)

err_console = Console(stderr=True)

tool_app = typer.Typer(name="tool", help="Manage tool catalog entries.")


def _state(ctx: typer.Context) -> GlobalState:
    """Retrieve global state from context."""
    from akgentic.catalog.cli.main import get_state

    return get_state(ctx)


def _load_entry_from_yaml(file: Path) -> ToolEntry:
    """Load a ToolEntry from a YAML file.

    Args:
        file: Path to the YAML file.

    Returns:
        A validated ToolEntry instance.

    Raises:
        typer.Exit: If the file cannot be read or parsed.
    """
    try:
        data = yaml.safe_load(file.read_text())
    except (OSError, yaml.YAMLError) as exc:
        err_console.print(f"[red]Error reading file:[/red] {exc}")
        raise typer.Exit(code=1) from exc
    try:
        return ToolEntry.model_validate(data)
    except (ValidationError, ValueError) as exc:
        err_console.print(f"[red]Validation error:[/red] {exc}")
        raise typer.Exit(code=1) from exc


@tool_app.command("list")
def list_tools(ctx: typer.Context) -> None:
    """List all tool entries."""
    state = _state(ctx)
    _, tool_catalog, _, _ = build_catalogs_from_state(state)
    entries = tool_catalog.list()
    render(entries, state.format)


@tool_app.command("get")
def get_tool(ctx: typer.Context, tool_id: str = typer.Argument(help="Tool ID")) -> None:
    """Display a single tool entry."""
    state = _state(ctx)
    _, tool_catalog, _, _ = build_catalogs_from_state(state)
    entry = tool_catalog.get(tool_id)
    if entry is None:
        err_console.print(f"[red]Not found:[/red] Tool '{tool_id}' not found")
        raise typer.Exit(code=1)
    render(entry, state.format)


@tool_app.command("create")
def create_tool(
    ctx: typer.Context,
    yaml_file: Path = typer.Argument(help="Path to YAML file with tool data"),
) -> None:
    """Create a new tool entry from a YAML file."""
    state = _state(ctx)
    entry = _load_entry_from_yaml(yaml_file)
    _, tool_catalog, _, _ = build_catalogs_from_state(state)
    try:
        created_id = tool_catalog.create(entry)
    except CatalogValidationError as exc:
        err_console.print(f"[red]Validation error:[/red] {exc}")
        for err in exc.errors:
            err_console.print(f"  - {err}")
        raise typer.Exit(code=1) from exc
    err_console.print(f"[green]Created tool:[/green] {created_id}")


@tool_app.command("update")
def update_tool(
    ctx: typer.Context,
    tool_id: str = typer.Argument(help="Tool ID to update"),
    yaml_file: Path = typer.Argument(help="Path to YAML file with updated tool data"),
) -> None:
    """Update an existing tool entry from a YAML file."""
    state = _state(ctx)
    entry = _load_entry_from_yaml(yaml_file)
    _, tool_catalog, _, _ = build_catalogs_from_state(state)
    try:
        tool_catalog.update(tool_id, entry)
    except EntryNotFoundError as exc:
        err_console.print(f"[red]Not found:[/red] {exc}")
        raise typer.Exit(code=1) from exc
    except CatalogValidationError as exc:
        err_console.print(f"[red]Validation error:[/red] {exc}")
        for err in exc.errors:
            err_console.print(f"  - {err}")
        raise typer.Exit(code=1) from exc
    err_console.print(f"[green]Updated tool:[/green] {tool_id}")


@tool_app.command("delete")
def delete_tool(
    ctx: typer.Context,
    tool_id: str = typer.Argument(help="Tool ID to delete"),
) -> None:
    """Delete a tool entry."""
    state = _state(ctx)
    _, tool_catalog, _, _ = build_catalogs_from_state(state)
    try:
        tool_catalog.delete(tool_id)
    except EntryNotFoundError as exc:
        err_console.print(f"[red]Not found:[/red] {exc}")
        raise typer.Exit(code=1) from exc
    except CatalogValidationError as exc:
        err_console.print(f"[red]Validation error:[/red] {exc}")
        for err in exc.errors:
            err_console.print(f"  - {err}")
        raise typer.Exit(code=1) from exc
    err_console.print(f"[green]Deleted tool:[/green] {tool_id}")


@tool_app.command("search")
def search_tools(
    ctx: typer.Context,
    tool_class: str | None = typer.Option(
        None, "--class", help="Filter by fully qualified tool class"
    ),
    name: str | None = typer.Option(None, "--name", help="Filter by tool name"),
) -> None:
    """Search tool entries by class or name."""
    state = _state(ctx)
    _, tool_catalog, _, _ = build_catalogs_from_state(state)
    query = ToolQuery(tool_class=tool_class, name=name)
    entries = tool_catalog.search(query)
    render(entries, state.format)
