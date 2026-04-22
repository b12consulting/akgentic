"""Tests for the team CLI commands."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from typer.testing import CliRunner

from akgentic.catalog.cli.main import app
from tests.cli.conftest import make_dirs, seed_agent, seed_team, team_data

runner = CliRunner()


def _write_team_yaml(path: Path, team_id: str, **kwargs: Any) -> Path:
    """Write a team YAML file and return its path."""
    data = team_data(team_id, **kwargs)
    file = path / f"{team_id}.yaml"
    file.write_text(yaml.dump(data, default_flow_style=False))
    return file


class TestTeamList:
    """team list command."""

    def test_list_empty(self, tmp_path: Path) -> None:
        make_dirs(tmp_path)
        result = runner.invoke(app, ["--catalog-dir", str(tmp_path), "team", "list"])
        assert result.exit_code == 0
        assert "No entries found" in result.output

    def test_list_with_entries(self, tmp_path: Path) -> None:
        make_dirs(tmp_path)
        seed_agent(tmp_path, "eng-mgr", role="Manager")
        seed_agent(tmp_path, "dev-lead", role="Lead")
        seed_team(tmp_path, "team1", name="Engineering", entry_point="eng-mgr")
        seed_team(tmp_path, "team2", name="Dev", entry_point="dev-lead")
        result = runner.invoke(app, ["--catalog-dir", str(tmp_path), "team", "list"])
        assert result.exit_code == 0
        assert "team1" in result.output
        assert "team2" in result.output


class TestTeamGet:
    """team get command."""

    def test_get_existing(self, tmp_path: Path) -> None:
        make_dirs(tmp_path)
        seed_agent(tmp_path, "eng-mgr", role="Manager")
        seed_team(tmp_path, "team1", name="Engineering", entry_point="eng-mgr")
        result = runner.invoke(app, ["--catalog-dir", str(tmp_path), "team", "get", "team1"])
        assert result.exit_code == 0
        assert "team1" in result.output

    def test_get_nonexistent(self, tmp_path: Path) -> None:
        make_dirs(tmp_path)
        result = runner.invoke(app, ["--catalog-dir", str(tmp_path), "team", "get", "nope"])
        assert result.exit_code == 1
        assert "Not found" in result.output


class TestTeamCreate:
    """team create command."""

    def test_create_from_yaml(self, tmp_path: Path) -> None:
        make_dirs(tmp_path)
        seed_agent(tmp_path, "eng-mgr", role="Manager")
        yaml_file = _write_team_yaml(tmp_path, "new-team", name="New Team", entry_point="eng-mgr")
        result = runner.invoke(
            app, ["--catalog-dir", str(tmp_path), "team", "create", str(yaml_file)]
        )
        assert result.exit_code == 0
        # Verify it was actually created
        get_result = runner.invoke(app, ["--catalog-dir", str(tmp_path), "team", "get", "new-team"])
        assert get_result.exit_code == 0
        assert "new-team" in get_result.output

    def test_create_duplicate(self, tmp_path: Path) -> None:
        make_dirs(tmp_path)
        seed_agent(tmp_path, "eng-mgr", role="Manager")
        seed_team(tmp_path, "dup", name="Dup Team", entry_point="eng-mgr")
        yaml_file = _write_team_yaml(tmp_path, "dup", name="Dup Team", entry_point="eng-mgr")
        result = runner.invoke(
            app, ["--catalog-dir", str(tmp_path), "team", "create", str(yaml_file)]
        )
        assert result.exit_code == 1

    def test_create_with_missing_agent(self, tmp_path: Path) -> None:
        make_dirs(tmp_path)
        yaml_file = _write_team_yaml(
            tmp_path,
            "bad-team",
            name="Bad Team",
            entry_point="ghost-agent",
            members=[{"agent_id": "ghost-agent", "headcount": 1}],
        )
        result = runner.invoke(
            app, ["--catalog-dir", str(tmp_path), "team", "create", str(yaml_file)]
        )
        assert result.exit_code == 1
        assert "ghost-agent" in result.output

    def test_create_with_invalid_yaml(self, tmp_path: Path) -> None:
        make_dirs(tmp_path)
        bad_file = tmp_path / "bad.yaml"
        bad_file.write_text("id: bad\n")  # Missing required fields
        result = runner.invoke(
            app, ["--catalog-dir", str(tmp_path), "team", "create", str(bad_file)]
        )
        assert result.exit_code == 1
        assert "Validation error" in result.output


class TestTeamUpdate:
    """team update command."""

    def test_update_existing(self, tmp_path: Path) -> None:
        make_dirs(tmp_path)
        seed_agent(tmp_path, "eng-mgr", role="Manager")
        seed_team(tmp_path, "team1", name="Old Name", entry_point="eng-mgr")
        input_dir = tmp_path / "input"
        input_dir.mkdir()
        yaml_file = _write_team_yaml(input_dir, "team1", name="New Name", entry_point="eng-mgr")
        result = runner.invoke(
            app, ["--catalog-dir", str(tmp_path), "team", "update", "team1", str(yaml_file)]
        )
        assert result.exit_code == 0

    def test_update_nonexistent(self, tmp_path: Path) -> None:
        make_dirs(tmp_path)
        seed_agent(tmp_path, "eng-mgr", role="Manager")
        yaml_file = _write_team_yaml(tmp_path, "nope", name="Ghost Team", entry_point="eng-mgr")
        result = runner.invoke(
            app, ["--catalog-dir", str(tmp_path), "team", "update", "nope", str(yaml_file)]
        )
        assert result.exit_code == 1


class TestTeamDelete:
    """team delete command."""

    def test_delete_existing(self, tmp_path: Path) -> None:
        make_dirs(tmp_path)
        seed_agent(tmp_path, "eng-mgr", role="Manager")
        seed_team(tmp_path, "team1", entry_point="eng-mgr")
        result = runner.invoke(app, ["--catalog-dir", str(tmp_path), "team", "delete", "team1"])
        assert result.exit_code == 0

    def test_delete_nonexistent(self, tmp_path: Path) -> None:
        make_dirs(tmp_path)
        result = runner.invoke(app, ["--catalog-dir", str(tmp_path), "team", "delete", "nope"])
        assert result.exit_code == 1
        assert "Not found" in result.output


class TestTeamSearch:
    """team search command."""

    def test_search_by_name(self, tmp_path: Path) -> None:
        make_dirs(tmp_path)
        seed_agent(tmp_path, "eng-mgr", role="Manager")
        seed_team(tmp_path, "team1", name="Engineering Team", entry_point="eng-mgr")
        seed_team(tmp_path, "team2", name="Marketing Team", entry_point="eng-mgr")
        result = runner.invoke(
            app, ["--catalog-dir", str(tmp_path), "team", "search", "--name", "Engineering"]
        )
        assert result.exit_code == 0
        assert "team1" in result.output

    def test_search_by_agent_id(self, tmp_path: Path) -> None:
        make_dirs(tmp_path)
        seed_agent(tmp_path, "eng-mgr", role="Manager")
        seed_agent(tmp_path, "dev-lead", role="Lead")
        seed_team(tmp_path, "team1", name="Eng Team", entry_point="eng-mgr")
        seed_team(tmp_path, "team2", name="Dev Team", entry_point="dev-lead")
        result = runner.invoke(
            app, ["--catalog-dir", str(tmp_path), "team", "search", "--agent-id", "eng-mgr"]
        )
        assert result.exit_code == 0
        assert "team1" in result.output

    def test_search_by_description(self, tmp_path: Path) -> None:
        make_dirs(tmp_path)
        seed_agent(tmp_path, "eng-mgr", role="Manager")
        seed_team(
            tmp_path,
            "team1",
            name="Eng Team",
            entry_point="eng-mgr",
            description="Handles backend services",
        )
        seed_team(
            tmp_path,
            "team2",
            name="UX Team",
            entry_point="eng-mgr",
            description="Designs user interfaces",
        )
        result = runner.invoke(
            app, ["--catalog-dir", str(tmp_path), "team", "search", "--description", "backend"]
        )
        assert result.exit_code == 0
        assert "team1" in result.output

    def test_search_no_results(self, tmp_path: Path) -> None:
        make_dirs(tmp_path)
        seed_agent(tmp_path, "eng-mgr", role="Manager")
        seed_team(tmp_path, "team1", name="Engineering", entry_point="eng-mgr")
        result = runner.invoke(
            app, ["--catalog-dir", str(tmp_path), "team", "search", "--name", "Ghost"]
        )
        assert result.exit_code == 0
        assert "No entries found" in result.output
