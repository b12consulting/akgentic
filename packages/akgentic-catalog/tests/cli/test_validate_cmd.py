"""Tests for the ``ak-catalog validate`` command."""

from __future__ import annotations

from pathlib import Path

import yaml
from typer.testing import CliRunner

from akgentic.catalog.cli.main import app
from tests.cli.conftest import agent_data, make_dirs, seed_agent, seed_team, team_data

runner = CliRunner()


def _seed_template(catalog_dir: Path, template_id: str, template: str = "Hello {name}") -> None:
    """Seed a template entry directly in the catalog directory."""
    data = {"id": template_id, "template": template}
    (catalog_dir / "templates" / f"{template_id}.yaml").write_text(
        yaml.dump(data, default_flow_style=False)
    )


def _seed_tool(catalog_dir: Path, tool_id: str) -> None:
    """Seed a tool entry directly in the catalog directory."""
    data = {
        "id": tool_id,
        "tool_class": "akgentic.tool.search.SearchTool",
        "tool": {
            "name": "search",
            "description": "Web search",
            "web_search": {"max_results": 5},
        },
    }
    (catalog_dir / "tools" / f"{tool_id}.yaml").write_text(
        yaml.dump(data, default_flow_style=False)
    )


def _seed_agent_with_tool_ids(
    catalog_dir: Path,
    agent_id: str,
    tool_ids: list[str],
) -> None:
    """Seed an agent entry with specific tool_ids."""
    data = agent_data(agent_id)
    data["tool_ids"] = tool_ids
    (catalog_dir / "agents" / f"{agent_id}.yaml").write_text(
        yaml.dump(data, default_flow_style=False)
    )


def _seed_agent_with_routes(
    catalog_dir: Path,
    agent_id: str,
    routes_to: list[str],
    role: str = "engineer",
) -> None:
    """Seed an agent entry with specific routes_to."""
    data = agent_data(agent_id, role=role)
    data["card"]["routes_to"] = routes_to
    (catalog_dir / "agents" / f"{agent_id}.yaml").write_text(
        yaml.dump(data, default_flow_style=False)
    )


def _seed_agent_with_template_ref(
    catalog_dir: Path,
    agent_id: str,
    template_ref: str,
    params: dict[str, str] | None = None,
) -> None:
    """Seed an agent with a @template reference in its prompt config."""
    data = agent_data(agent_id)
    data["card"]["config"]["prompt"] = {
        "template": template_ref,
        "params": params or {},
    }
    (catalog_dir / "agents" / f"{agent_id}.yaml").write_text(
        yaml.dump(data, default_flow_style=False)
    )


class TestValidateAll:
    """Test validate on all catalogs (AC-4)."""

    def test_validate_clean_catalog(self, tmp_path: Path) -> None:
        """All entries with valid cross-refs → exit 0."""
        make_dirs(tmp_path)
        _seed_template(tmp_path, "tmpl-1")
        _seed_tool(tmp_path, "tool-1")
        seed_agent(tmp_path, "agent-1")
        seed_team(
            tmp_path,
            "team-1",
            entry_point="agent-1",
            members=[{"agent_id": "agent-1", "headcount": 1}],
        )
        result = runner.invoke(app, ["--catalog-dir", str(tmp_path), "validate"])
        assert result.exit_code == 0, result.output
        assert "Found 0 errors" in result.output

    def test_validate_empty_catalog(self, tmp_path: Path) -> None:
        """Empty catalog → exit 0, no errors."""
        make_dirs(tmp_path)
        result = runner.invoke(app, ["--catalog-dir", str(tmp_path), "validate"])
        assert result.exit_code == 0, result.output
        assert "Found 0 errors" in result.output


class TestValidateSpecific:
    """Test validate with --catalog filter (AC-5)."""

    def test_validate_agents_only(self, tmp_path: Path) -> None:
        """--catalog agents: only agent cross-refs checked."""
        make_dirs(tmp_path)
        seed_agent(tmp_path, "agent-1")
        # Seed team with broken member — should NOT be reported
        seed_team(
            tmp_path,
            "team-1",
            entry_point="nonexistent",
            members=[{"agent_id": "nonexistent", "headcount": 1}],
        )
        result = runner.invoke(
            app, ["--catalog-dir", str(tmp_path), "validate", "--catalog", "agents"]
        )
        assert result.exit_code == 0, result.output
        assert "0 teams" in result.output  # teams not counted

    def test_validate_templates_only(self, tmp_path: Path) -> None:
        """--catalog templates: just counts templates, no cross-ref errors."""
        make_dirs(tmp_path)
        _seed_template(tmp_path, "tmpl-1")
        # Seed agent with broken tool ref — should NOT be reported
        _seed_agent_with_tool_ids(tmp_path, "bad-agent", ["missing-tool"])
        result = runner.invoke(
            app, ["--catalog-dir", str(tmp_path), "validate", "--catalog", "templates"]
        )
        assert result.exit_code == 0, result.output
        assert "1 templates" in result.output

    def test_validate_invalid_catalog_option(self, tmp_path: Path) -> None:
        """Invalid --catalog value → exit 1."""
        make_dirs(tmp_path)
        result = runner.invoke(
            app, ["--catalog-dir", str(tmp_path), "validate", "--catalog", "invalid"]
        )
        assert result.exit_code == 1
        assert "Invalid catalog" in result.output


class TestValidateErrors:
    """Test that validate detects various cross-reference errors (AC-4)."""

    def test_validate_detects_missing_tool(self, tmp_path: Path) -> None:
        """Agent with invalid tool_id → error reported."""
        make_dirs(tmp_path)
        _seed_agent_with_tool_ids(tmp_path, "bad-agent", ["nonexistent-tool"])
        result = runner.invoke(app, ["--catalog-dir", str(tmp_path), "validate"])
        assert result.exit_code == 1
        assert "nonexistent-tool" in result.output

    def test_validate_detects_missing_template_ref(self, tmp_path: Path) -> None:
        """Agent with @template ref to nonexistent template → error reported."""
        make_dirs(tmp_path)
        _seed_agent_with_template_ref(tmp_path, "tmpl-agent", "@missing-tmpl", {"name": "X"})
        result = runner.invoke(app, ["--catalog-dir", str(tmp_path), "validate"])
        assert result.exit_code == 1
        assert "missing-tmpl" in result.output

    def test_validate_detects_broken_routes_to(self, tmp_path: Path) -> None:
        """Agent with invalid routes_to target → error reported."""
        make_dirs(tmp_path)
        _seed_agent_with_routes(tmp_path, "router", ["@NonExistentAgent"])
        result = runner.invoke(app, ["--catalog-dir", str(tmp_path), "validate"])
        assert result.exit_code == 1
        assert "route target" in result.output.lower()

    def test_validate_detects_missing_agent_in_team(self, tmp_path: Path) -> None:
        """Team with invalid member agent_id → error reported."""
        make_dirs(tmp_path)
        seed_team(
            tmp_path,
            "bad-team",
            entry_point="missing-agent",
            members=[{"agent_id": "missing-agent", "headcount": 1}],
        )
        result = runner.invoke(app, ["--catalog-dir", str(tmp_path), "validate"])
        assert result.exit_code == 1
        assert "missing-agent" in result.output

    def test_validate_detects_entry_point_not_in_members(self, tmp_path: Path) -> None:
        """Team entry_point not in members → error reported."""
        make_dirs(tmp_path)
        seed_agent(tmp_path, "agent-1")
        data = team_data("bad-team", entry_point="other-agent")
        data["members"] = [{"agent_id": "agent-1", "headcount": 1}]
        (tmp_path / "teams" / "bad-team.yaml").write_text(yaml.dump(data, default_flow_style=False))
        result = runner.invoke(app, ["--catalog-dir", str(tmp_path), "validate"])
        assert result.exit_code == 1
        assert "entry_point" in result.output

    def test_validate_with_catalog_agents_only(self, tmp_path: Path) -> None:
        """--catalog agents: team errors not reported."""
        make_dirs(tmp_path)
        seed_agent(tmp_path, "agent-1")
        seed_team(
            tmp_path,
            "bad-team",
            entry_point="missing-agent",
            members=[{"agent_id": "missing-agent", "headcount": 1}],
        )
        result = runner.invoke(
            app, ["--catalog-dir", str(tmp_path), "validate", "--catalog", "agents"]
        )
        assert result.exit_code == 0, result.output
        assert "missing-agent" not in result.output
