"""Tests for the template CLI commands."""

from __future__ import annotations

from pathlib import Path

import yaml
from typer.testing import CliRunner

from akgentic.catalog.cli.main import app

runner = CliRunner()


def _write_template_yaml(path: Path, template_id: str, template: str) -> Path:
    """Write a template YAML file and return its path."""
    data = {"id": template_id, "template": template}
    file = path / f"{template_id}.yaml"
    file.write_text(yaml.dump(data, default_flow_style=False))
    return file


def _seed_template(catalog_dir: Path, template_id: str, template: str) -> None:
    """Seed a template entry directly in the catalog directory."""
    data = {"id": template_id, "template": template}
    (catalog_dir / "templates" / f"{template_id}.yaml").write_text(
        yaml.dump(data, default_flow_style=False)
    )


class TestTemplateList:
    """template list command."""

    def test_list_empty(self, tmp_path: Path) -> None:
        result = runner.invoke(app, ["--catalog-dir", str(tmp_path), "template", "list"])
        assert result.exit_code == 0
        assert "No entries found" in result.output

    def test_list_with_entries(self, tmp_path: Path) -> None:
        for name in ("templates", "tools", "agents", "teams"):
            (tmp_path / name).mkdir(exist_ok=True)
        _seed_template(tmp_path, "t1", "Hello {name}")
        _seed_template(tmp_path, "t2", "You are {role}")
        result = runner.invoke(app, ["--catalog-dir", str(tmp_path), "template", "list"])
        assert result.exit_code == 0
        assert "t1" in result.output
        assert "t2" in result.output


class TestTemplateGet:
    """template get command."""

    def test_get_existing(self, tmp_path: Path) -> None:
        for name in ("templates", "tools", "agents", "teams"):
            (tmp_path / name).mkdir(exist_ok=True)
        _seed_template(tmp_path, "t1", "Hello {name}")
        result = runner.invoke(app, ["--catalog-dir", str(tmp_path), "template", "get", "t1"])
        assert result.exit_code == 0
        assert "t1" in result.output

    def test_get_nonexistent(self, tmp_path: Path) -> None:
        for name in ("templates", "tools", "agents", "teams"):
            (tmp_path / name).mkdir(exist_ok=True)
        result = runner.invoke(app, ["--catalog-dir", str(tmp_path), "template", "get", "nope"])
        assert result.exit_code == 1
        assert "Not found" in result.output


class TestTemplateCreate:
    """template create command."""

    def test_create_from_yaml(self, tmp_path: Path) -> None:
        for name in ("templates", "tools", "agents", "teams"):
            (tmp_path / name).mkdir(exist_ok=True)
        yaml_file = _write_template_yaml(tmp_path, "new-t", "Hello {world}")
        result = runner.invoke(
            app, ["--catalog-dir", str(tmp_path), "template", "create", str(yaml_file)]
        )
        assert result.exit_code == 0
        # Verify it was actually created
        get_result = runner.invoke(
            app, ["--catalog-dir", str(tmp_path), "template", "get", "new-t"]
        )
        assert get_result.exit_code == 0
        assert "new-t" in get_result.output

    def test_create_duplicate(self, tmp_path: Path) -> None:
        for name in ("templates", "tools", "agents", "teams"):
            (tmp_path / name).mkdir(exist_ok=True)
        _seed_template(tmp_path, "dup", "original")
        yaml_file = _write_template_yaml(tmp_path, "dup", "duplicate")
        result = runner.invoke(
            app, ["--catalog-dir", str(tmp_path), "template", "create", str(yaml_file)]
        )
        assert result.exit_code == 1


class TestTemplateUpdate:
    """template update command."""

    def test_update_existing(self, tmp_path: Path) -> None:
        for name in ("templates", "tools", "agents", "teams"):
            (tmp_path / name).mkdir(exist_ok=True)
        _seed_template(tmp_path, "t1", "old {text}")
        input_dir = tmp_path / "input"
        input_dir.mkdir(exist_ok=True)
        yaml_file = _write_template_yaml(input_dir, "t1", "new {text}")
        result = runner.invoke(
            app, ["--catalog-dir", str(tmp_path), "template", "update", "t1", str(yaml_file)]
        )
        assert result.exit_code == 0

    def test_update_nonexistent(self, tmp_path: Path) -> None:
        for name in ("templates", "tools", "agents", "teams"):
            (tmp_path / name).mkdir(exist_ok=True)
        yaml_file = _write_template_yaml(tmp_path, "nope", "text")
        result = runner.invoke(
            app, ["--catalog-dir", str(tmp_path), "template", "update", "nope", str(yaml_file)]
        )
        assert result.exit_code == 1


class TestTemplateDelete:
    """template delete command."""

    def test_delete_existing(self, tmp_path: Path) -> None:
        for name in ("templates", "tools", "agents", "teams"):
            (tmp_path / name).mkdir(exist_ok=True)
        _seed_template(tmp_path, "t1", "doomed")
        result = runner.invoke(app, ["--catalog-dir", str(tmp_path), "template", "delete", "t1"])
        assert result.exit_code == 0

    def test_delete_nonexistent(self, tmp_path: Path) -> None:
        for name in ("templates", "tools", "agents", "teams"):
            (tmp_path / name).mkdir(exist_ok=True)
        result = runner.invoke(app, ["--catalog-dir", str(tmp_path), "template", "delete", "nope"])
        assert result.exit_code == 1
        assert "Not found" in result.output


class TestTemplateSearch:
    """template search command."""

    def test_search_by_placeholder(self, tmp_path: Path) -> None:
        for name in ("templates", "tools", "agents", "teams"):
            (tmp_path / name).mkdir(exist_ok=True)
        _seed_template(tmp_path, "t1", "Hello {name}")
        _seed_template(tmp_path, "t2", "You are {role}. Do {task}")
        result = runner.invoke(
            app, ["--catalog-dir", str(tmp_path), "template", "search", "--placeholder", "name"]
        )
        assert result.exit_code == 0
        assert "t1" in result.output
