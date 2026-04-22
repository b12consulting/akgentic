"""Output rendering for the team CLI.

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

# Column definitions for Process list view: (field_path, header_label)
_TABLE_COLUMNS: list[tuple[str, str]] = [
    ("team_id", "Team ID"),
    ("team_card.name", "Name"),
    ("status", "Status"),
    ("created_at", "Created"),
    ("updated_at", "Updated"),
]


class OutputFormat(StrEnum):
    """CLI output format options."""

    table = "table"
    json = "json"
    yaml = "yaml"


def _get_field_value(model: BaseModel, dotted_path: str) -> str:
    """Resolve a dotted field path on a Pydantic model to a string value.

    Args:
        model: The Pydantic model instance.
        dotted_path: A dot-separated field path (e.g. ``"team_card.name"``).

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
    return str(obj) if obj is not None else ""


def _render_list_table(entries: Sequence[BaseModel]) -> None:
    """Render a list of Process entries as a Rich table.

    Team IDs are truncated to 8 characters for display readability.

    Args:
        entries: Sequence of Process model instances.
    """
    if not entries:
        console.print("[dim]No teams found.[/dim]")
        return

    table = Table(title="Teams")
    for _, header in _TABLE_COLUMNS:
        table.add_column(header)

    for entry in entries:
        row: list[str] = []
        for field_path, _ in _TABLE_COLUMNS:
            value = _get_field_value(entry, field_path)
            if field_path == "team_id":
                value = value[:8]
            row.append(value)
        table.add_row(*row)

    console.print(table)


def _render_detail_table(
    entry: BaseModel,
    *,
    event_count: int = 0,
    agent_state_count: int = 0,
) -> None:
    """Render a single Process entry as a Rich key-value detail table.

    Args:
        entry: A Process model instance.
        event_count: Number of persisted events for the team.
        agent_state_count: Number of agent state snapshots for the team.
    """
    table = Table(title="Team Detail", show_header=False)
    table.add_column("Field", style="bold")
    table.add_column("Value")

    data = entry.model_dump(mode="json")
    team_card = data.get("team_card", {})
    agent_cards = team_card.get("agent_cards", {})

    table.add_row("team_id", str(data.get("team_id", "")))
    table.add_row("name", str(team_card.get("name", "")))
    table.add_row("description", str(team_card.get("description", "")))
    table.add_row("status", str(data.get("status", "")))
    table.add_row("user_id", str(data.get("user_id", "")))
    table.add_row("user_email", str(data.get("user_email", "")))
    table.add_row("created_at", str(data.get("created_at", "")))
    table.add_row("updated_at", str(data.get("updated_at", "")))
    table.add_row("member_count", str(len(agent_cards)))
    table.add_row("event_count", str(event_count))
    table.add_row("agent_state_count", str(agent_state_count))

    console.print(table)


def _render_json(
    entries: Sequence[BaseModel] | BaseModel,
    *,
    event_count: int | None = None,
    agent_state_count: int | None = None,
) -> None:
    """Render entries as JSON to stdout.

    Args:
        entries: A single entry or sequence of entries to render.
        event_count: Optional event count for detail view.
        agent_state_count: Optional agent state count for detail view.
    """
    data: dict[str, Any] | list[dict[str, Any]]
    if isinstance(entries, BaseModel):
        data = entries.model_dump(mode="json")
        if event_count is not None:
            data["event_count"] = event_count
        if agent_state_count is not None:
            data["agent_state_count"] = agent_state_count
    else:
        data = [e.model_dump(mode="json") for e in entries]
    console.print(json.dumps(data, indent=2))


def _render_yaml(
    entries: Sequence[BaseModel] | BaseModel,
    *,
    event_count: int | None = None,
    agent_state_count: int | None = None,
) -> None:
    """Render entries as YAML to stdout.

    Args:
        entries: A single entry or sequence of entries to render.
        event_count: Optional event count for detail view.
        agent_state_count: Optional agent state count for detail view.
    """
    data: dict[str, Any] | list[dict[str, Any]]
    if isinstance(entries, BaseModel):
        data = entries.model_dump(mode="json")
        if event_count is not None:
            data["event_count"] = event_count
        if agent_state_count is not None:
            data["agent_state_count"] = agent_state_count
    else:
        data = [e.model_dump(mode="json") for e in entries]
    console.print(yaml.dump(data, default_flow_style=False).rstrip())


def render(
    entries: Sequence[BaseModel] | BaseModel,
    fmt: OutputFormat,
    *,
    event_count: int | None = None,
    agent_state_count: int | None = None,
) -> None:
    """Render team entries in the requested format.

    Args:
        entries: A single entry or sequence of entries to render.
        fmt: The output format (table, json, or yaml).
        event_count: Optional event count for inspect detail view.
        agent_state_count: Optional agent state count for inspect detail view.
    """
    if fmt == OutputFormat.json:
        _render_json(
            entries, event_count=event_count, agent_state_count=agent_state_count
        )
    elif fmt == OutputFormat.yaml:
        _render_yaml(
            entries, event_count=event_count, agent_state_count=agent_state_count
        )
    elif isinstance(entries, BaseModel):
        _render_detail_table(
            entries,
            event_count=event_count if event_count is not None else 0,
            agent_state_count=agent_state_count if agent_state_count is not None else 0,
        )
    else:
        _render_list_table(entries)
