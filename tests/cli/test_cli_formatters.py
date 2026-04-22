"""Tests for akgentic.infra.cli.formatters."""

from __future__ import annotations

import json

import yaml

from akgentic.infra.cli.formatters import (
    OutputFormat,
    format_json,
    format_output,
    format_table,
    format_yaml,
)


class TestFormatTable:
    def test_basic_table(self) -> None:
        rows = [
            {"name": "alpha", "status": "running"},
            {"name": "beta", "status": "stopped"},
        ]
        result = format_table(rows, ["name", "status"])
        lines = result.splitlines()
        assert len(lines) == 4  # header + separator + 2 rows
        assert "NAME" in lines[0]
        assert "STATUS" in lines[0]
        assert "alpha" in lines[2]
        assert "beta" in lines[3]

    def test_empty_rows(self) -> None:
        assert format_table([], ["a", "b"]) == "(no results)"

    def test_missing_keys(self) -> None:
        rows = [{"name": "x"}]
        result = format_table(rows, ["name", "missing_col"])
        assert "x" in result

    def test_nested_dict_values(self) -> None:
        rows = [{"name": "evt1", "data": {"type": "started", "ts": 123}}]
        result = format_table(rows, ["name", "data"])
        # dict values should be JSON-serialized, not Python repr
        assert '"type"' in result
        assert "{'type'" not in result

    def test_column_alignment(self) -> None:
        rows = [{"a": "short", "b": "x"}, {"a": "x", "b": "longvalue"}]
        result = format_table(rows, ["a", "b"])
        lines = result.splitlines()
        # All lines should have the same length (padded)
        assert len(set(len(line.rstrip()) for line in lines)) <= 2  # header might differ slightly


class TestFormatJson:
    def test_dict(self) -> None:
        data = {"key": "value", "num": 42}
        result = format_json(data)
        parsed = json.loads(result)
        assert parsed == data

    def test_list(self) -> None:
        data = [{"a": 1}, {"a": 2}]
        result = format_json(data)
        parsed = json.loads(result)
        assert parsed == data

    def test_datetime_serialization(self) -> None:
        from datetime import datetime

        data = {"ts": datetime(2025, 1, 1, 12, 0)}
        result = format_json(data)
        assert "2025-01-01" in result


class TestFormatYaml:
    def test_dict(self) -> None:
        data = {"key": "value", "num": 42}
        result = format_yaml(data)
        parsed = yaml.safe_load(result)
        assert parsed == data

    def test_list(self) -> None:
        data = [{"a": 1}, {"a": 2}]
        result = format_yaml(data)
        parsed = yaml.safe_load(result)
        assert parsed == data


class TestFormatOutput:
    def test_json_format(self) -> None:
        data = {"x": 1}
        result = format_output(data, OutputFormat.json)
        assert json.loads(result) == data

    def test_yaml_format(self) -> None:
        data = {"x": 1}
        result = format_output(data, OutputFormat.yaml)
        assert yaml.safe_load(result) == data

    def test_table_format_with_list(self) -> None:
        rows = [{"a": "1", "b": "2"}]
        result = format_output(rows, OutputFormat.table, columns=["a", "b"])
        assert "A" in result
        assert "B" in result

    def test_table_format_with_dict(self) -> None:
        data = {"a": "1", "b": "2"}
        result = format_output(data, OutputFormat.table, columns=["a", "b"])
        assert "1" in result

    def test_table_fallback_no_columns(self) -> None:
        data = {"a": 1}
        result = format_output(data, OutputFormat.table)
        # Falls back to JSON
        assert json.loads(result) == data
