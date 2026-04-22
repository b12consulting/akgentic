"""Tests for the tool CLI commands."""

from __future__ import annotations

from pathlib import Path

import yaml
from typer.testing import CliRunner

from akgentic.catalog.cli.main import app

runner = CliRunner()

TOOL_CLASS = "akgentic.tool.search.SearchTool"


def _tool_data(tool_id: str, name: str = "search", description: str = "Search") -> dict:
    """Build a tool entry dict suitable for YAML serialization."""
    return {
        "id": tool_id,
        "tool_class": TOOL_CLASS,
        "tool": {"name": name, "description": description},
    }


def _write_tool_yaml(path: Path, tool_id: str, **kwargs: str) -> Path:
    """Write a tool YAML file and return its path."""
    data = _tool_data(tool_id, **kwargs)
    file = path / f"{tool_id}.yaml"
    file.write_text(yaml.dump(data, default_flow_style=False))
    return file


def _seed_tool(catalog_dir: Path, tool_id: str, **kwargs: str) -> None:
    """Seed a tool entry directly in the catalog directory."""
    data = _tool_data(tool_id, **kwargs)
    (catalog_dir / "tools" / f"{tool_id}.yaml").write_text(
        yaml.dump(data, default_flow_style=False)
    )


def _make_dirs(tmp_path: Path) -> None:
    for name in ("templates", "tools", "agents", "teams"):
        (tmp_path / name).mkdir(exist_ok=True)


class TestToolList:
    """tool list command."""

    def test_list_empty(self, tmp_path: Path) -> None:
        _make_dirs(tmp_path)
        result = runner.invoke(app, ["--catalog-dir", str(tmp_path), "tool", "list"])
        assert result.exit_code == 0
        assert "No entries found" in result.output

    def test_list_with_entries(self, tmp_path: Path) -> None:
        _make_dirs(tmp_path)
        _seed_tool(tmp_path, "t1", name="search")
        _seed_tool(tmp_path, "t2", name="fetch")
        result = runner.invoke(app, ["--catalog-dir", str(tmp_path), "tool", "list"])
        assert result.exit_code == 0
        assert "t1" in result.output
        assert "t2" in result.output


class TestToolGet:
    """tool get command."""

    def test_get_existing(self, tmp_path: Path) -> None:
        _make_dirs(tmp_path)
        _seed_tool(tmp_path, "t1")
        result = runner.invoke(app, ["--catalog-dir", str(tmp_path), "tool", "get", "t1"])
        assert result.exit_code == 0
        assert "t1" in result.output

    def test_get_nonexistent(self, tmp_path: Path) -> None:
        _make_dirs(tmp_path)
        result = runner.invoke(app, ["--catalog-dir", str(tmp_path), "tool", "get", "nope"])
        assert result.exit_code == 1
        assert "Not found" in result.output


class TestToolCreate:
    """tool create command."""

    def test_create_from_yaml(self, tmp_path: Path) -> None:
        _make_dirs(tmp_path)
        yaml_file = _write_tool_yaml(tmp_path, "new-t")
        result = runner.invoke(
            app, ["--catalog-dir", str(tmp_path), "tool", "create", str(yaml_file)]
        )
        assert result.exit_code == 0
        # Verify it was actually created
        get_result = runner.invoke(app, ["--catalog-dir", str(tmp_path), "tool", "get", "new-t"])
        assert get_result.exit_code == 0

    def test_create_duplicate(self, tmp_path: Path) -> None:
        _make_dirs(tmp_path)
        _seed_tool(tmp_path, "dup")
        yaml_file = _write_tool_yaml(tmp_path, "dup")
        result = runner.invoke(
            app, ["--catalog-dir", str(tmp_path), "tool", "create", str(yaml_file)]
        )
        assert result.exit_code == 1


class TestToolUpdate:
    """tool update command."""

    def test_update_existing(self, tmp_path: Path) -> None:
        _make_dirs(tmp_path)
        _seed_tool(tmp_path, "t1", name="old-name")
        input_dir = tmp_path / "input"
        input_dir.mkdir()
        yaml_file = _write_tool_yaml(input_dir, "t1", name="new-name")
        result = runner.invoke(
            app, ["--catalog-dir", str(tmp_path), "tool", "update", "t1", str(yaml_file)]
        )
        assert result.exit_code == 0

    def test_update_nonexistent(self, tmp_path: Path) -> None:
        _make_dirs(tmp_path)
        yaml_file = _write_tool_yaml(tmp_path, "nope")
        result = runner.invoke(
            app, ["--catalog-dir", str(tmp_path), "tool", "update", "nope", str(yaml_file)]
        )
        assert result.exit_code == 1


class TestToolDelete:
    """tool delete command."""

    def test_delete_existing(self, tmp_path: Path) -> None:
        _make_dirs(tmp_path)
        _seed_tool(tmp_path, "t1")
        result = runner.invoke(app, ["--catalog-dir", str(tmp_path), "tool", "delete", "t1"])
        assert result.exit_code == 0

    def test_delete_nonexistent(self, tmp_path: Path) -> None:
        _make_dirs(tmp_path)
        result = runner.invoke(app, ["--catalog-dir", str(tmp_path), "tool", "delete", "nope"])
        assert result.exit_code == 1
        assert "Not found" in result.output


class TestToolSearch:
    """tool search command."""

    def test_search_by_class(self, tmp_path: Path) -> None:
        _make_dirs(tmp_path)
        _seed_tool(tmp_path, "t1")
        result = runner.invoke(
            app,
            ["--catalog-dir", str(tmp_path), "tool", "search", "--class", TOOL_CLASS],
        )
        assert result.exit_code == 0
        assert "t1" in result.output

    def test_search_by_name(self, tmp_path: Path) -> None:
        _make_dirs(tmp_path)
        _seed_tool(tmp_path, "t1", name="search")
        _seed_tool(tmp_path, "t2", name="fetch")
        result = runner.invoke(
            app, ["--catalog-dir", str(tmp_path), "tool", "search", "--name", "search"]
        )
        assert result.exit_code == 0
        assert "t1" in result.output
