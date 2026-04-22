"""Template CRUD subcommands for the catalog CLI."""

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
from akgentic.catalog.models.queries import TemplateQuery
from akgentic.catalog.models.template import TemplateEntry

if TYPE_CHECKING:
    from akgentic.catalog.cli.main import GlobalState

__all__ = ["template_app"]

logger = logging.getLogger(__name__)

err_console = Console(stderr=True)

template_app = typer.Typer(name="template", help="Manage template catalog entries.")


def _state(ctx: typer.Context) -> GlobalState:
    """Retrieve global state from context."""
    from akgentic.catalog.cli.main import get_state

    return get_state(ctx)


def _load_entry_from_yaml(file: Path) -> TemplateEntry:
    """Load a TemplateEntry from a YAML file.

    Args:
        file: Path to the YAML file.

    Returns:
        A validated TemplateEntry instance.

    Raises:
        typer.Exit: If the file cannot be read or parsed.
    """
    try:
        data = yaml.safe_load(file.read_text())
    except (OSError, yaml.YAMLError) as exc:
        err_console.print(f"[red]Error reading file:[/red] {exc}")
        raise typer.Exit(code=1) from exc
    try:
        return TemplateEntry.model_validate(data)
    except ValidationError as exc:
        err_console.print(f"[red]Validation error:[/red] {exc}")
        raise typer.Exit(code=1) from exc


@template_app.command("list")
def list_templates(ctx: typer.Context) -> None:
    """List all template entries."""
    state = _state(ctx)
    template_catalog, _, _, _ = build_catalogs_from_state(state)
    entries = template_catalog.list()
    render(entries, state.format)


@template_app.command("get")
def get_template(ctx: typer.Context, template_id: str = typer.Argument(help="Template ID")) -> None:
    """Display a single template entry."""
    state = _state(ctx)
    template_catalog, _, _, _ = build_catalogs_from_state(state)
    entry = template_catalog.get(template_id)
    if entry is None:
        err_console.print(f"[red]Not found:[/red] Template '{template_id}' not found")
        raise typer.Exit(code=1)
    render(entry, state.format)


@template_app.command("create")
def create_template(
    ctx: typer.Context,
    yaml_file: Path = typer.Argument(help="Path to YAML file with template data"),
) -> None:
    """Create a new template entry from a YAML file."""
    state = _state(ctx)
    entry = _load_entry_from_yaml(yaml_file)
    template_catalog, _, _, _ = build_catalogs_from_state(state)
    try:
        created_id = template_catalog.create(entry)
    except CatalogValidationError as exc:
        err_console.print(f"[red]Validation error:[/red] {exc}")
        for err in exc.errors:
            err_console.print(f"  - {err}")
        raise typer.Exit(code=1) from exc
    err_console.print(f"[green]Created template:[/green] {created_id}")


@template_app.command("update")
def update_template(
    ctx: typer.Context,
    template_id: str = typer.Argument(help="Template ID to update"),
    yaml_file: Path = typer.Argument(help="Path to YAML file with updated template data"),
) -> None:
    """Update an existing template entry from a YAML file."""
    state = _state(ctx)
    entry = _load_entry_from_yaml(yaml_file)
    template_catalog, _, _, _ = build_catalogs_from_state(state)
    try:
        template_catalog.update(template_id, entry)
    except EntryNotFoundError as exc:
        err_console.print(f"[red]Not found:[/red] {exc}")
        raise typer.Exit(code=1) from exc
    except CatalogValidationError as exc:
        err_console.print(f"[red]Validation error:[/red] {exc}")
        for err in exc.errors:
            err_console.print(f"  - {err}")
        raise typer.Exit(code=1) from exc
    err_console.print(f"[green]Updated template:[/green] {template_id}")


@template_app.command("delete")
def delete_template(
    ctx: typer.Context,
    template_id: str = typer.Argument(help="Template ID to delete"),
) -> None:
    """Delete a template entry."""
    state = _state(ctx)
    template_catalog, _, _, _ = build_catalogs_from_state(state)
    try:
        template_catalog.delete(template_id)
    except EntryNotFoundError as exc:
        err_console.print(f"[red]Not found:[/red] {exc}")
        raise typer.Exit(code=1) from exc
    except CatalogValidationError as exc:
        err_console.print(f"[red]Validation error:[/red] {exc}")
        for err in exc.errors:
            err_console.print(f"  - {err}")
        raise typer.Exit(code=1) from exc
    err_console.print(f"[green]Deleted template:[/green] {template_id}")


@template_app.command("search")
def search_templates(
    ctx: typer.Context,
    placeholder: str | None = typer.Option(None, "--placeholder", help="Filter by placeholder"),
) -> None:
    """Search template entries by placeholder name."""
    state = _state(ctx)
    template_catalog, _, _, _ = build_catalogs_from_state(state)
    query = TemplateQuery(placeholder=placeholder)
    entries = template_catalog.search(query)
    render(entries, state.format)
