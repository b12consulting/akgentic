"""Tests for the agent CLI commands."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from typer.testing import CliRunner

from akgentic.catalog.cli.main import app
from tests.cli.conftest import agent_data, make_dirs, seed_agent, seed_team

runner = CliRunner()


def _write_agent_yaml(path: Path, agent_id: str, **kwargs: Any) -> Path:
    """Write an agent YAML file and return its path."""
    data = agent_data(agent_id, **kwargs)
    file = path / f"{agent_id}.yaml"
    file.write_text(yaml.dump(data, default_flow_style=False))
    return file


class TestAgentList:
    """agent list command."""

    def test_list_empty(self, tmp_path: Path) -> None:
        make_dirs(tmp_path)
        result = runner.invoke(app, ["--catalog-dir", str(tmp_path), "agent", "list"])
        assert result.exit_code == 0
        assert "No entries found" in result.output

    def test_list_with_entries(self, tmp_path: Path) -> None:
        make_dirs(tmp_path)
        seed_agent(tmp_path, "a1", role="Manager")
        seed_agent(tmp_path, "a2", role="Engineer")
        result = runner.invoke(app, ["--catalog-dir", str(tmp_path), "agent", "list"])
        assert result.exit_code == 0
        assert "a1" in result.output
        assert "a2" in result.output


class TestAgentGet:
    """agent get command."""

    def test_get_existing(self, tmp_path: Path) -> None:
        make_dirs(tmp_path)
        seed_agent(tmp_path, "a1", role="Manager")
        result = runner.invoke(app, ["--catalog-dir", str(tmp_path), "agent", "get", "a1"])
        assert result.exit_code == 0
        assert "a1" in result.output

    def test_get_nonexistent(self, tmp_path: Path) -> None:
        make_dirs(tmp_path)
        result = runner.invoke(app, ["--catalog-dir", str(tmp_path), "agent", "get", "nope"])
        assert result.exit_code == 1
        assert "Not found" in result.output


class TestAgentCreate:
    """agent create command."""

    def test_create_from_yaml(self, tmp_path: Path) -> None:
        make_dirs(tmp_path)
        yaml_file = _write_agent_yaml(tmp_path, "new-agent", role="Researcher")
        result = runner.invoke(
            app, ["--catalog-dir", str(tmp_path), "agent", "create", str(yaml_file)]
        )
        assert result.exit_code == 0
        # Verify it was actually created
        get_result = runner.invoke(
            app, ["--catalog-dir", str(tmp_path), "agent", "get", "new-agent"]
        )
        assert get_result.exit_code == 0
        assert "new-agent" in get_result.output

    def test_create_duplicate(self, tmp_path: Path) -> None:
        make_dirs(tmp_path)
        seed_agent(tmp_path, "dup", role="Manager")
        yaml_file = _write_agent_yaml(tmp_path, "dup", role="Manager")
        result = runner.invoke(
            app, ["--catalog-dir", str(tmp_path), "agent", "create", str(yaml_file)]
        )
        assert result.exit_code == 1

    def test_create_with_invalid_yaml(self, tmp_path: Path) -> None:
        make_dirs(tmp_path)
        bad_file = tmp_path / "bad.yaml"
        bad_file.write_text("id: bad\n")  # Missing required card field
        result = runner.invoke(
            app, ["--catalog-dir", str(tmp_path), "agent", "create", str(bad_file)]
        )
        assert result.exit_code == 1
        assert "Validation error" in result.output

    def test_create_with_cross_validation_error(self, tmp_path: Path) -> None:
        make_dirs(tmp_path)
        data = agent_data("bad-refs")
        data["tool_ids"] = ["nonexistent-tool"]
        yaml_file = tmp_path / "bad-refs.yaml"
        yaml_file.write_text(yaml.dump(data, default_flow_style=False))
        result = runner.invoke(
            app, ["--catalog-dir", str(tmp_path), "agent", "create", str(yaml_file)]
        )
        assert result.exit_code == 1
        assert "nonexistent-tool" in result.output


class TestAgentUpdate:
    """agent update command."""

    def test_update_existing(self, tmp_path: Path) -> None:
        make_dirs(tmp_path)
        seed_agent(tmp_path, "a1", role="Manager")
        input_dir = tmp_path / "input"
        input_dir.mkdir()
        yaml_file = _write_agent_yaml(input_dir, "a1", role="Senior Manager")
        result = runner.invoke(
            app, ["--catalog-dir", str(tmp_path), "agent", "update", "a1", str(yaml_file)]
        )
        assert result.exit_code == 0

    def test_update_nonexistent(self, tmp_path: Path) -> None:
        make_dirs(tmp_path)
        yaml_file = _write_agent_yaml(tmp_path, "nope", role="Ghost")
        result = runner.invoke(
            app, ["--catalog-dir", str(tmp_path), "agent", "update", "nope", str(yaml_file)]
        )
        assert result.exit_code == 1


class TestAgentDelete:
    """agent delete command."""

    def test_delete_existing(self, tmp_path: Path) -> None:
        make_dirs(tmp_path)
        seed_agent(tmp_path, "a1", role="Manager")
        result = runner.invoke(app, ["--catalog-dir", str(tmp_path), "agent", "delete", "a1"])
        assert result.exit_code == 0

    def test_delete_nonexistent(self, tmp_path: Path) -> None:
        make_dirs(tmp_path)
        result = runner.invoke(app, ["--catalog-dir", str(tmp_path), "agent", "delete", "nope"])
        assert result.exit_code == 1
        assert "Not found" in result.output

    def test_delete_protected_by_team(self, tmp_path: Path) -> None:
        make_dirs(tmp_path)
        seed_agent(tmp_path, "eng-mgr", role="Manager")
        seed_team(
            tmp_path,
            "eng-team",
            entry_point="eng-mgr",
            members=[{"agent_id": "eng-mgr", "headcount": 1}],
        )
        result = runner.invoke(app, ["--catalog-dir", str(tmp_path), "agent", "delete", "eng-mgr"])
        assert result.exit_code == 1
        assert "eng-team" in result.output


class TestAgentSearch:
    """agent search command."""

    def test_search_by_role(self, tmp_path: Path) -> None:
        make_dirs(tmp_path)
        seed_agent(tmp_path, "a1", role="Manager")
        seed_agent(tmp_path, "a2", role="Engineer")
        result = runner.invoke(
            app, ["--catalog-dir", str(tmp_path), "agent", "search", "--role", "Manager"]
        )
        assert result.exit_code == 0
        assert "a1" in result.output

    def test_search_by_skill(self, tmp_path: Path) -> None:
        make_dirs(tmp_path)
        seed_agent(tmp_path, "a1", role="Researcher", skills=["research", "analysis"])
        seed_agent(tmp_path, "a2", role="Engineer", skills=["coding"])
        result = runner.invoke(
            app, ["--catalog-dir", str(tmp_path), "agent", "search", "--skill", "research"]
        )
        assert result.exit_code == 0
        assert "a1" in result.output

    def test_search_by_description(self, tmp_path: Path) -> None:
        make_dirs(tmp_path)
        seed_agent(tmp_path, "a1", role="Manager", description="Coordinates engineering")
        seed_agent(tmp_path, "a2", role="Engineer", description="Writes code")
        result = runner.invoke(
            app,
            ["--catalog-dir", str(tmp_path), "agent", "search", "--description", "engineering"],
        )
        assert result.exit_code == 0
        assert "a1" in result.output

    def test_search_no_results(self, tmp_path: Path) -> None:
        make_dirs(tmp_path)
        seed_agent(tmp_path, "a1", role="Manager")
        result = runner.invoke(
            app, ["--catalog-dir", str(tmp_path), "agent", "search", "--role", "Ghost"]
        )
        assert result.exit_code == 0
        assert "No entries found" in result.output
