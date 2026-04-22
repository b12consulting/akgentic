"""Shape tests for ``schema.toml`` (AC #3).

Parse the file with ``tomllib`` directly — no Nagra or Postgres container
needed — so they run in every environment.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

SCHEMA_PATH = (
    Path(__file__).parents[3]
    / "src"
    / "akgentic"
    / "team"
    / "repositories"
    / "postgres"
    / "schema.toml"
)

EXPECTED_TABLES = {"team_process_entries", "event_entries", "agent_state_entries"}


def _load_schema() -> dict[str, object]:
    with SCHEMA_PATH.open("rb") as fh:
        return tomllib.load(fh)


def test_schema_file_exists() -> None:
    assert SCHEMA_PATH.exists(), f"schema.toml missing at {SCHEMA_PATH}"


def test_schema_has_exactly_three_top_level_tables() -> None:
    schema = _load_schema()
    assert set(schema.keys()) == EXPECTED_TABLES


def test_team_process_entries_shape() -> None:
    schema = _load_schema()
    table = schema["team_process_entries"]
    assert isinstance(table, dict)
    assert table["natural_key"] == ["id"]
    columns = table["columns"]
    assert isinstance(columns, dict)
    assert columns == {"id": "str", "data": "json"}


def test_event_entries_shape() -> None:
    schema = _load_schema()
    table = schema["event_entries"]
    assert isinstance(table, dict)
    assert table["natural_key"] == ["team_id", "sequence"]
    columns = table["columns"]
    assert isinstance(columns, dict)
    assert columns == {"team_id": "str", "sequence": "int", "data": "json"}


def test_agent_state_entries_shape() -> None:
    schema = _load_schema()
    table = schema["agent_state_entries"]
    assert isinstance(table, dict)
    assert table["natural_key"] == ["team_id", "agent_id"]
    columns = table["columns"]
    assert isinstance(columns, dict)
    assert columns == {"team_id": "str", "agent_id": "str", "data": "json"}
