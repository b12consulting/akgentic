"""Output rendering for the catalog CLI.

Supports three output formats: Rich table (default), JSON, and YAML.
"""

from __future__ import annotations

import json
import logging
from enum import StrEnum
from typing import TYPE_CHECKING, Any

import yaml
from pydantic import BaseModel
from rich.console import Console
from rich.table import Table

if TYPE_CHECKING:
    from collections.abc import Sequence

__all__ = ["OutputFormat", "render"]

logger = logging.getLogger(__name__)

console = Console()

# Column definitions per entry type (field_name, header_label, optional transform)
_TABLE_COLUMNS: dict[str, list[tuple[str, str]]] = {
    "TemplateEntry": [
        ("id", "ID"),
        ("template", "Template"),
        ("placeholders", "Placeholders"),
    ],
    "ToolEntry": [
        ("id", "ID"),
        ("tool_class", "Tool Class"),
        ("tool.name", "Name"),
    ],
    "AgentEntry": [
        ("id", "ID"),
        ("card.role", "Role"),
        ("card.description", "Description"),
    ],
    "TeamEntry": [
        ("id", "ID"),
        ("name", "Name"),
        ("entry_point", "Entry Point"),
    ],
}


class OutputFormat(StrEnum):
    """CLI output format options."""

    table = "table"
    json = "json"
    yaml = "yaml"


def _get_field_value(model: BaseModel, dotted_path: str) -> str:
    """Resolve a dotted field path on a Pydantic model to a string value.

    Args:
        model: The Pydantic model instance.
        dotted_path: A dot-separated field path (e.g. ``"tool.name"``).

    Returns:
        String representation of the resolved value.
    """
    obj: Any = model
    for part in dotted_path.split("."):
        if isinstance(obj, BaseModel):
            obj = getattr(obj, part, None)
        elif isinstance(obj, dict):
            obj = obj.get(part)
        else:
            return ""
    if isinstance(obj, list):
        return ", ".join(str(v) for v in obj)
    return str(obj) if obj is not None else ""


def _truncate(text: str, max_len: int = 60) -> str:
    """Truncate a string to *max_len* characters, adding ellipsis if needed."""
    if len(text) <= max_len:
        return text
    return text[: max_len - 3] + "..."


def _render_table(entries: Sequence[BaseModel]) -> None:
    """Render a list of entries as a Rich table."""
    if not entries:
        console.print("[dim]No entries found.[/dim]")
        return

    type_name = type(entries[0]).__name__
    columns = _TABLE_COLUMNS.get(type_name)
    if columns is None:
        # Fallback: render as JSON
        _render_json(entries)
        return

    table = Table(title=type_name)
    for _, header in columns:
        table.add_column(header)

    for entry in entries:
        row: list[str] = []
        for field_path, _ in columns:
            value = _get_field_value(entry, field_path)
            if field_path == "template":
                value = _truncate(value)
            row.append(value)
        table.add_row(*row)

    console.print(table)


def _render_single_table(entry: BaseModel) -> None:
    """Render a single entry as a Rich key-value table."""
    table = Table(title=type(entry).__name__, show_header=False)
    table.add_column("Field", style="bold")
    table.add_column("Value")

    data = entry.model_dump()
    for key, value in data.items():
        if isinstance(value, dict):
            value = json.dumps(value, indent=2)
        elif isinstance(value, list):
            value = ", ".join(str(v) for v in value)
        else:
            value = str(value)
        table.add_row(key, value)

    console.print(table)


def _render_json(entries: Sequence[BaseModel] | BaseModel) -> None:
    """Render entries as JSON to stdout."""
    data: dict[str, Any] | list[dict[str, Any]]
    if isinstance(entries, BaseModel):
        data = entries.model_dump()
    else:
        data = [e.model_dump() for e in entries]
    console.print(json.dumps(data, indent=2))


def _render_yaml(entries: Sequence[BaseModel] | BaseModel) -> None:
    """Render entries as YAML to stdout."""
    data: dict[str, Any] | list[dict[str, Any]]
    if isinstance(entries, BaseModel):
        data = entries.model_dump()
    else:
        data = [e.model_dump() for e in entries]
    console.print(yaml.dump(data, default_flow_style=False).rstrip())


def render(
    entries: Sequence[BaseModel] | BaseModel,
    fmt: OutputFormat,
) -> None:
    """Render catalog entries in the requested format.

    Args:
        entries: A single entry or sequence of entries to render.
        fmt: The output format (table, json, or yaml).
    """
    if fmt == OutputFormat.json:
        _render_json(entries)
    elif fmt == OutputFormat.yaml:
        _render_yaml(entries)
    elif isinstance(entries, BaseModel):
        _render_single_table(entries)
    else:
        _render_table(entries)
