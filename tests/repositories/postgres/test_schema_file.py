"""Shape tests for ``schema.toml`` (AC #3).

These tests parse the file with ``tomllib`` directly — no Nagra or Postgres
container needed — so they run in every environment.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

SCHEMA_PATH = (
    Path(__file__).parents[3]
    / "src"
    / "akgentic"
    / "catalog"
    / "repositories"
    / "postgres"
    / "schema.toml"
)

EXPECTED_TABLES = {"template_entries", "tool_entries", "agent_entries", "team_entries"}


def _load_schema() -> dict[str, object]:
    with SCHEMA_PATH.open("rb") as fh:
        return tomllib.load(fh)


def test_schema_file_exists() -> None:
    assert SCHEMA_PATH.exists(), f"schema.toml missing at {SCHEMA_PATH}"


def test_schema_has_exactly_four_top_level_tables() -> None:
    schema = _load_schema()
    assert set(schema.keys()) == EXPECTED_TABLES


def test_each_table_has_natural_key_id() -> None:
    schema = _load_schema()
    for table in EXPECTED_TABLES:
        table_block = schema[table]
        assert isinstance(table_block, dict)
        assert table_block["natural_key"] == ["id"], f"{table} natural_key mismatch"


def test_each_table_has_exactly_id_and_data_columns() -> None:
    schema = _load_schema()
    for table in EXPECTED_TABLES:
        table_block = schema[table]
        assert isinstance(table_block, dict)
        columns = table_block["columns"]
        assert isinstance(columns, dict)
        assert columns == {"id": "str", "data": "json"}, f"{table} columns mismatch"
