"""Output formatting for the ak-infra CLI."""

from __future__ import annotations

import enum
import json
from typing import Any

import yaml


class OutputFormat(enum.StrEnum):
    """Supported CLI output formats."""

    table = "table"
    json = "json"
    yaml = "yaml"


def _cell_str(val: object) -> str:
    """Convert a cell value to a display string, serializing dicts as compact JSON."""
    if isinstance(val, dict):
        return json.dumps(val, default=str)
    return str(val)


def format_table(rows: list[dict[str, Any]], columns: list[str]) -> str:
    """Format rows as an aligned text table."""
    if not rows:
        return "(no results)"

    # Compute column widths
    widths: dict[str, int] = {col: len(col) for col in columns}
    for row in rows:
        for col in columns:
            widths[col] = max(widths[col], len(_cell_str(row.get(col, ""))))

    # Header
    header = "  ".join(col.upper().ljust(widths[col]) for col in columns)
    separator = "  ".join("-" * widths[col] for col in columns)

    # Rows
    lines = [header, separator]
    for row in rows:
        line = "  ".join(_cell_str(row.get(col, "")).ljust(widths[col]) for col in columns)
        lines.append(line)

    return "\n".join(lines)


def format_json(data: object) -> str:
    """Format data as indented JSON."""
    return json.dumps(data, indent=2, default=str)


def format_yaml(data: object) -> str:
    """Format data as YAML."""
    return yaml.dump(data, default_flow_style=False, sort_keys=False)


def format_output(
    data: object,
    fmt: OutputFormat,
    columns: list[str] | None = None,
) -> str:
    """Dispatch to the appropriate formatter."""
    if fmt == OutputFormat.json:
        return format_json(data)
    if fmt == OutputFormat.yaml:
        return format_yaml(data)
    # table
    if isinstance(data, list) and columns:
        return format_table(data, columns)
    if isinstance(data, dict) and columns:
        return format_table([data], columns)
    # Fallback for table without columns — use json
    return format_json(data)
