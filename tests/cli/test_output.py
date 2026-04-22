"""Tests for the CLI output rendering."""

from __future__ import annotations

import json

import yaml
from typer.testing import CliRunner

from akgentic.catalog.cli._output import OutputFormat, render
from akgentic.catalog.cli.main import app
from tests.conftest import make_template, make_tool

runner = CliRunner()


class TestRenderTable:
    """Table format rendering (smoke tests via direct render call)."""

    def test_render_templates_table(self) -> None:
        entries = [make_template(id="t1"), make_template(id="t2", template="Hello {name}")]
        render(entries, OutputFormat.table)

    def test_render_tools_table(self) -> None:
        entries = [make_tool(id="tool-1"), make_tool(id="tool-2", name="fetch")]
        render(entries, OutputFormat.table)

    def test_render_empty_list(self) -> None:
        render([], OutputFormat.table)

    def test_render_single_template_table(self) -> None:
        entry = make_template(id="t1", template="Hello {name}")
        render(entry, OutputFormat.table)


class TestRenderJson:
    """JSON format rendering."""

    def test_render_templates_json(self) -> None:
        entries = [make_template(id="t1"), make_template(id="t2", template="Hello {name}")]
        render(entries, OutputFormat.json)

    def test_render_single_template_json(self) -> None:
        entry = make_template(id="t1", template="Hello {name}")
        render(entry, OutputFormat.json)

    def test_render_tools_json(self) -> None:
        entries = [make_tool(id="tool-1")]
        render(entries, OutputFormat.json)


class TestRenderYaml:
    """YAML format rendering."""

    def test_render_templates_yaml(self) -> None:
        entries = [make_template(id="t1"), make_template(id="t2", template="Hello {name}")]
        render(entries, OutputFormat.yaml)

    def test_render_single_template_yaml(self) -> None:
        entry = make_template(id="t1", template="Hello {name}")
        render(entry, OutputFormat.yaml)


class TestOutputFormatsViaCliRunner:
    """Output format integration via CLI runner."""

    def test_default_table_format(self, tmp_path: object) -> None:
        from pathlib import Path

        tmp = Path(str(tmp_path))
        for name in ("templates", "tools", "agents", "teams"):
            (tmp / name).mkdir(exist_ok=True)

        (tmp / "templates" / "t1.yaml").write_text(
            yaml.dump({"id": "t1", "template": "Hello {name}"}, default_flow_style=False)
        )

        result = runner.invoke(app, ["--catalog-dir", str(tmp), "template", "list"])
        assert result.exit_code == 0
        assert "t1" in result.output

    def test_json_format(self, tmp_path: object) -> None:
        from pathlib import Path

        tmp = Path(str(tmp_path))
        for name in ("templates", "tools", "agents", "teams"):
            (tmp / name).mkdir(exist_ok=True)

        (tmp / "templates" / "t1.yaml").write_text(
            yaml.dump({"id": "t1", "template": "Hello {name}"}, default_flow_style=False)
        )

        result = runner.invoke(
            app, ["--catalog-dir", str(tmp), "--format", "json", "template", "list"]
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert isinstance(data, list)
        assert data[0]["id"] == "t1"

    def test_yaml_format(self, tmp_path: object) -> None:
        from pathlib import Path

        tmp = Path(str(tmp_path))
        for name in ("templates", "tools", "agents", "teams"):
            (tmp / name).mkdir(exist_ok=True)

        (tmp / "templates" / "t1.yaml").write_text(
            yaml.dump({"id": "t1", "template": "Hello {name}"}, default_flow_style=False)
        )

        result = runner.invoke(
            app, ["--catalog-dir", str(tmp), "--format", "yaml", "template", "list"]
        )
        assert result.exit_code == 0
        data = yaml.safe_load(result.output)
        assert isinstance(data, list)
        assert data[0]["id"] == "t1"
