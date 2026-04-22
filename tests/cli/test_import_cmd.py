"""Tests for the ``ak-catalog import`` command."""

from __future__ import annotations

from pathlib import Path

import yaml
from typer.testing import CliRunner

from akgentic.catalog.cli.main import app
from tests.cli.conftest import make_dirs

runner = CliRunner()


def _write_entries_file(tmp_path: Path, content: str) -> Path:
    """Write a Python entries file to tmp_path and return its path."""
    py_file = tmp_path / "entries.py"
    py_file.write_text(content)
    return py_file


TEMPLATE_ENTRIES_FILE = """\
from akgentic.catalog.models.template import TemplateEntry

entries = [
    TemplateEntry(id="test-tmpl", template="Hello {name}"),
]
"""

MIXED_ENTRIES_FILE = """\
from akgentic.catalog.models.template import TemplateEntry
from akgentic.catalog.models.tool import ToolEntry
from akgentic.catalog.models.agent import AgentEntry
from akgentic.catalog.models.team import TeamEntry, TeamMemberSpec
from akgentic.tool.search import SearchTool, WebSearch

entries = [
    TemplateEntry(id="mix-tmpl", template="Hello {name}"),
    ToolEntry(
        id="mix-tool",
        tool_class="akgentic.tool.search.SearchTool",
        tool=SearchTool(
            name="search", description="Web search",
            web_search=WebSearch(max_results=5),
        ),
    ),
    AgentEntry.model_validate({
        "id": "mix-agent",
        "tool_ids": [],
        "card": {
            "role": "Tester",
            "description": "Test agent",
            "skills": ["testing"],
            "agent_class": "akgentic.agent.BaseAgent",
            "config": {"name": "@Tester", "role": "Tester"},
            "routes_to": [],
        },
    }),
    TeamEntry(
        id="mix-team",
        name="Mix Team",
        entry_point="mix-agent",
        message_types=["akgentic.core.messages.UserMessage"],
        members=[TeamMemberSpec(agent_id="mix-agent", headcount=1)],
    ),
]
"""


class TestImportDiscovery:
    """Test that import discovers entries by type (AC-1)."""

    def test_import_templates_only(self, tmp_path: Path) -> None:
        make_dirs(tmp_path)
        py_file = _write_entries_file(tmp_path, TEMPLATE_ENTRIES_FILE)
        result = runner.invoke(app, ["--catalog-dir", str(tmp_path), "import", str(py_file)])
        assert result.exit_code == 0, result.output
        assert "Created template" in result.output
        assert "test-tmpl" in result.output

    def test_import_mixed_types(self, tmp_path: Path) -> None:
        make_dirs(tmp_path)
        py_file = _write_entries_file(tmp_path, MIXED_ENTRIES_FILE)
        result = runner.invoke(app, ["--catalog-dir", str(tmp_path), "import", str(py_file)])
        assert result.exit_code == 0, result.output
        assert "Created template" in result.output
        assert "Created tool" in result.output
        assert "Created agent" in result.output
        assert "Created team" in result.output


class TestImportResolutionOrder:
    """Test that entries are dispatched in resolution order (AC-2)."""

    def test_resolution_order_in_output(self, tmp_path: Path) -> None:
        """Templates+tools should appear before agents, agents before teams."""
        make_dirs(tmp_path)
        py_file = _write_entries_file(tmp_path, MIXED_ENTRIES_FILE)
        result = runner.invoke(app, ["--catalog-dir", str(tmp_path), "import", str(py_file)])
        assert result.exit_code == 0, result.output
        lines = result.output.split("\n")
        created_lines = [line for line in lines if "Created" in line]
        # Template and tool should come before agent and team
        tmpl_idx = next(i for i, ln in enumerate(created_lines) if "template" in ln)
        tool_idx = next(i for i, ln in enumerate(created_lines) if "tool" in ln)
        agent_idx = next(i for i, ln in enumerate(created_lines) if "agent" in ln)
        team_idx = next(i for i, ln in enumerate(created_lines) if "team" in ln)
        assert tmpl_idx < agent_idx
        assert tool_idx < agent_idx
        assert agent_idx < team_idx


class TestImportCreateUpdate:
    """Test create-or-update logic (AC-2)."""

    def test_import_updates_existing_entry(self, tmp_path: Path) -> None:
        make_dirs(tmp_path)
        # Seed an existing template
        (tmp_path / "templates" / "test-tmpl.yaml").write_text(
            yaml.dump({"id": "test-tmpl", "template": "Old {name}"})
        )
        py_file = _write_entries_file(tmp_path, TEMPLATE_ENTRIES_FILE)
        result = runner.invoke(app, ["--catalog-dir", str(tmp_path), "import", str(py_file)])
        assert result.exit_code == 0, result.output
        assert "Updated template" in result.output
        assert "test-tmpl" in result.output


class TestImportDryRun:
    """Test dry-run mode (AC-3)."""

    def test_dry_run_no_persist(self, tmp_path: Path) -> None:
        make_dirs(tmp_path)
        py_file = _write_entries_file(tmp_path, TEMPLATE_ENTRIES_FILE)
        result = runner.invoke(
            app, ["--catalog-dir", str(tmp_path), "import", str(py_file), "--dry-run"]
        )
        assert result.exit_code == 0, result.output
        assert "Dry run" in result.output
        assert "1 would be created" in result.output
        # Verify nothing was persisted
        assert not list((tmp_path / "templates").glob("*.yaml"))


class TestImportErrors:
    """Test error handling in import command."""

    def test_missing_file(self, tmp_path: Path) -> None:
        make_dirs(tmp_path)
        result = runner.invoke(
            app, ["--catalog-dir", str(tmp_path), "import", str(tmp_path / "nonexistent.py")]
        )
        assert result.exit_code == 1
        assert "not found" in result.output.lower() or "Error" in result.output

    def test_file_without_entries(self, tmp_path: Path) -> None:
        make_dirs(tmp_path)
        py_file = _write_entries_file(tmp_path, "x = 42\n")
        result = runner.invoke(app, ["--catalog-dir", str(tmp_path), "import", str(py_file)])
        assert result.exit_code == 1
        assert "entries" in result.output.lower()

    def test_entries_not_a_list(self, tmp_path: Path) -> None:
        make_dirs(tmp_path)
        py_file = _write_entries_file(tmp_path, 'entries = "not a list"\n')
        result = runner.invoke(app, ["--catalog-dir", str(tmp_path), "import", str(py_file)])
        assert result.exit_code == 1
        assert "list" in result.output.lower()

    def test_entries_empty_list(self, tmp_path: Path) -> None:
        make_dirs(tmp_path)
        py_file = _write_entries_file(tmp_path, "entries = []\n")
        result = runner.invoke(app, ["--catalog-dir", str(tmp_path), "import", str(py_file)])
        assert result.exit_code == 1
        assert "empty" in result.output.lower()

    def test_import_agent_with_bad_tool_ref(self, tmp_path: Path) -> None:
        """Agent referencing nonexistent tool — error reported, import continues."""
        make_dirs(tmp_path)
        content = """\
from akgentic.catalog.models.agent import AgentEntry

entries = [
    AgentEntry.model_validate({
        "id": "bad-agent",
        "tool_ids": ["nonexistent-tool"],
        "card": {
            "role": "Tester",
            "description": "Test agent",
            "skills": ["testing"],
            "agent_class": "akgentic.agent.BaseAgent",
            "config": {"name": "@Tester", "role": "Tester"},
            "routes_to": [],
        },
    }),
]
"""
        py_file = _write_entries_file(tmp_path, content)
        result = runner.invoke(app, ["--catalog-dir", str(tmp_path), "import", str(py_file)])
        # Should report errors but still exit (exit code 1 because of validation errors)
        assert result.exit_code == 1
        assert "nonexistent-tool" in result.output
