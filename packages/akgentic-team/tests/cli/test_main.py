"""Tests for the ak-team CLI commands.

Validates list, inspect, global options, create, delete, and resume commands
using Typer CliRunner with YamlEventStore backed by temporary directories.

Acceptance Criteria: AC1-AC11 from Story 6.1, AC1-AC9 from Story 6.2,
AC1-AC6 from Story 6.3.
"""

from __future__ import annotations

import json
import signal
import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch

import yaml
from typer.testing import CliRunner

from akgentic.team.cli.main import app
from akgentic.team.models import TeamCard, TeamStatus
from akgentic.team.repositories.yaml import YamlEventStore
from tests.cli.conftest import populate_teams
from tests.models.conftest import (
    make_agent_state_snapshot,
    make_persisted_event,
    make_process,
    make_team_card,
)


class TestListCommand:
    """Tests for `ak-team list` command (AC2, AC3, AC4, AC5)."""

    def test_list_with_no_teams_returns_empty(
        self, cli_runner: CliRunner, data_dir: object
    ) -> None:
        """AC4: list with no teams shows empty output, exit code 0."""
        result = cli_runner.invoke(app, ["--data-dir", str(data_dir), "list"])
        assert result.exit_code == 0
        assert "No teams found" in result.output

    def test_list_with_teams_shows_all(
        self, cli_runner: CliRunner, data_dir: object, yaml_store: YamlEventStore
    ) -> None:
        """AC2: list with teams shows all team names and IDs."""
        teams = populate_teams(yaml_store, count=2)
        result = cli_runner.invoke(app, ["--data-dir", str(data_dir), "list"])
        assert result.exit_code == 0
        for t in teams:
            # Table truncates to 8 chars
            assert str(t.team_id)[:8] in result.output
            assert t.team_card.name in result.output

    def test_list_status_filter_running(
        self, cli_runner: CliRunner, data_dir: object, yaml_store: YamlEventStore
    ) -> None:
        """AC3: list --status running filters correctly."""
        running = populate_teams(yaml_store, count=1, status=TeamStatus.RUNNING)
        stopped = populate_teams(yaml_store, count=1, status=TeamStatus.STOPPED)
        result = cli_runner.invoke(
            app, ["--data-dir", str(data_dir), "list", "--status", "running"]
        )
        assert result.exit_code == 0
        assert str(running[0].team_id)[:8] in result.output
        assert str(stopped[0].team_id)[:8] not in result.output

    def test_list_format_json(
        self, cli_runner: CliRunner, data_dir: object, yaml_store: YamlEventStore
    ) -> None:
        """AC5: list --format json produces valid JSON."""
        populate_teams(yaml_store, count=1)
        result = cli_runner.invoke(
            app, ["--data-dir", str(data_dir), "--format", "json", "list"]
        )
        assert result.exit_code == 0
        parsed = json.loads(result.output)
        assert isinstance(parsed, list)
        assert len(parsed) == 1

    def test_list_format_yaml(
        self, cli_runner: CliRunner, data_dir: object, yaml_store: YamlEventStore
    ) -> None:
        """AC5: list --format yaml produces valid YAML."""
        populate_teams(yaml_store, count=1)
        result = cli_runner.invoke(
            app, ["--data-dir", str(data_dir), "--format", "yaml", "list"]
        )
        assert result.exit_code == 0
        parsed = yaml.safe_load(result.output)
        assert isinstance(parsed, list)
        assert len(parsed) == 1


class TestInspectCommand:
    """Tests for `ak-team inspect` command (AC6, AC7)."""

    def test_inspect_existing_team(
        self, cli_runner: CliRunner, data_dir: object, yaml_store: YamlEventStore
    ) -> None:
        """AC6: inspect shows full metadata for existing team."""
        process = make_process()
        yaml_store.save_team(process)
        yaml_store.save_event(
            make_persisted_event(team_id=process.team_id, sequence=1)
        )
        yaml_store.save_agent_state(
            make_agent_state_snapshot(team_id=process.team_id, agent_id="agent-a")
        )

        result = cli_runner.invoke(
            app, ["--data-dir", str(data_dir), "inspect", str(process.team_id)]
        )
        assert result.exit_code == 0
        assert str(process.team_id) in result.output
        assert process.team_card.name in result.output

    def test_inspect_nonexistent_team(
        self, cli_runner: CliRunner, data_dir: object
    ) -> None:
        """AC7: inspect non-existent team shows error, exit code 1."""
        fake_id = str(uuid.uuid4())
        result = cli_runner.invoke(
            app, ["--data-dir", str(data_dir), "inspect", fake_id]
        )
        assert result.exit_code == 1
        assert "not found" in result.output

    def test_inspect_invalid_uuid(
        self, cli_runner: CliRunner, data_dir: object
    ) -> None:
        """AC7: inspect with invalid UUID shows error, exit code 1."""
        result = cli_runner.invoke(
            app, ["--data-dir", str(data_dir), "inspect", "not-a-uuid"]
        )
        assert result.exit_code == 1
        assert "Invalid UUID" in result.output

    def test_inspect_json_format(
        self, cli_runner: CliRunner, data_dir: object, yaml_store: YamlEventStore
    ) -> None:
        """AC5, AC6: inspect --format json includes event and agent state counts."""
        process = make_process()
        yaml_store.save_team(process)
        yaml_store.save_event(
            make_persisted_event(team_id=process.team_id, sequence=1)
        )

        result = cli_runner.invoke(
            app,
            [
                "--data-dir", str(data_dir),
                "--format", "json",
                "inspect", str(process.team_id),
            ],
        )
        assert result.exit_code == 0
        parsed = json.loads(result.output)
        assert parsed["event_count"] == 1
        assert parsed["agent_state_count"] == 0


class TestGlobalOptions:
    """Tests for global CLI options (AC1, AC8)."""

    def test_invalid_backend(self, cli_runner: CliRunner) -> None:
        """AC1: Invalid backend shows error, exit code 1."""
        result = cli_runner.invoke(
            app, ["--backend", "sqlite", "list"]
        )
        assert result.exit_code == 1
        assert "Invalid backend" in result.output

    def test_mongodb_backend_without_mongo_uri(self, cli_runner: CliRunner) -> None:
        """AC1: --backend mongodb without --mongo-uri shows error."""
        result = cli_runner.invoke(
            app, ["--backend", "mongodb", "list"]
        )
        assert result.exit_code == 1
        assert "--mongo-uri" in result.output

    def test_list_invalid_status_filter(
        self, cli_runner: CliRunner, data_dir: object
    ) -> None:
        """AC3: list --status with invalid value shows error, exit code 1."""
        result = cli_runner.invoke(
            app, ["--data-dir", str(data_dir), "list", "--status", "bogus"]
        )
        assert result.exit_code == 1
        assert "Invalid status" in result.output


def _write_team_card_yaml(team_card: TeamCard, path: Path) -> Path:
    """Serialize a TeamCard to YAML and write to file."""
    data = team_card.model_dump(mode="json")
    file = path / "team-card.yaml"
    file.write_text(yaml.dump(data, default_flow_style=False))
    return file


class TestCreateCommand:
    """Tests for `ak-team create` command (AC1, AC2, AC3, AC4 from Story 6.2)."""

    @patch("akgentic.team.cli.main._run_interactive")
    def test_create_valid_team_card(
        self, mock_interactive: MagicMock, cli_runner: CliRunner, data_dir: Path
    ) -> None:
        """AC1: create from valid TeamCard YAML shows team_id, exit code 0."""
        team_card = make_team_card(agent_class="akgentic.core.agent.Akgent")
        card_file = _write_team_card_yaml(team_card, data_dir)

        result = cli_runner.invoke(
            app, ["--data-dir", str(data_dir), "create", str(card_file)]
        )
        assert result.exit_code == 0, result.output
        assert "Team created:" in result.output
        mock_interactive.assert_called_once()

    @patch("akgentic.team.cli.main._run_interactive")
    def test_create_with_user_id(
        self,
        mock_interactive: MagicMock,
        cli_runner: CliRunner,
        data_dir: Path,
        yaml_store: YamlEventStore,
    ) -> None:
        """AC2: create with --user-id passes it to TeamManager."""
        team_card = make_team_card(agent_class="akgentic.core.agent.Akgent")
        card_file = _write_team_card_yaml(team_card, data_dir)

        result = cli_runner.invoke(
            app,
            [
                "--data-dir", str(data_dir),
                "create", str(card_file),
                "--user-id", "myuser",
            ],
        )
        assert result.exit_code == 0, result.output

        # Verify user_id was stored in the Process
        teams = yaml_store.list_teams()
        assert len(teams) == 1
        assert teams[0].user_id == "myuser"

    def test_create_nonexistent_file(
        self, cli_runner: CliRunner, data_dir: Path
    ) -> None:
        """AC3: create with non-existent file shows error, exit code 1."""
        result = cli_runner.invoke(
            app,
            ["--data-dir", str(data_dir), "create", "/no/such/file.yaml"],
        )
        assert result.exit_code == 1
        assert "File not found" in result.output

    def test_create_invalid_yaml(
        self, cli_runner: CliRunner, data_dir: Path
    ) -> None:
        """AC4: create with invalid YAML shows error, exit code 1."""
        bad_file = data_dir / "bad.yaml"
        bad_file.write_text("not: valid: teamcard: {{{}}}:")

        result = cli_runner.invoke(
            app, ["--data-dir", str(data_dir), "create", str(bad_file)]
        )
        assert result.exit_code == 1
        assert "Invalid YAML" in result.output

    def test_create_invalid_team_card_structure(
        self, cli_runner: CliRunner, data_dir: Path
    ) -> None:
        """AC4: create with valid YAML but invalid TeamCard shows error, exit code 1."""
        bad_file = data_dir / "bad-card.yaml"
        bad_file.write_text(yaml.dump({"name": "incomplete"}))

        result = cli_runner.invoke(
            app, ["--data-dir", str(data_dir), "create", str(bad_file)]
        )
        assert result.exit_code == 1
        assert "Invalid TeamCard" in result.output

    def test_create_team_build_failure(
        self, cli_runner: CliRunner, data_dir: Path
    ) -> None:
        """Error path: TeamFactory.build failure shows friendly error, exit code 1."""
        team_card = make_team_card(agent_class="nonexistent.module.FakeAgent")
        card_file = _write_team_card_yaml(team_card, data_dir)

        result = cli_runner.invoke(
            app, ["--data-dir", str(data_dir), "create", str(card_file)]
        )
        assert result.exit_code == 1
        assert "Failed to create team" in result.output


class TestDeleteCommand:
    """Tests for `ak-team delete` command (AC5, AC6, AC7, AC8 from Story 6.2)."""

    def test_delete_stopped_team(
        self, cli_runner: CliRunner, data_dir: Path, yaml_store: YamlEventStore
    ) -> None:
        """AC5: delete stopped team succeeds, exit code 0."""
        process = make_process(status=TeamStatus.STOPPED)
        yaml_store.save_team(process)

        result = cli_runner.invoke(
            app, ["--data-dir", str(data_dir), "delete", str(process.team_id)]
        )
        assert result.exit_code == 0, result.output
        assert "deleted" in result.output

        # Verify team is actually deleted from store
        assert yaml_store.load_team(process.team_id) is None

    def test_delete_running_team(
        self, cli_runner: CliRunner, data_dir: Path, yaml_store: YamlEventStore
    ) -> None:
        """AC6: delete running team shows error, exit code 1."""
        process = make_process(status=TeamStatus.RUNNING)
        yaml_store.save_team(process)

        result = cli_runner.invoke(
            app, ["--data-dir", str(data_dir), "delete", str(process.team_id)]
        )
        assert result.exit_code == 1
        assert "running" in result.output.lower()
        assert "stop" in result.output.lower()

    def test_delete_nonexistent_team(
        self, cli_runner: CliRunner, data_dir: Path
    ) -> None:
        """AC7: delete non-existent team shows error, exit code 1."""
        fake_id = str(uuid.uuid4())
        result = cli_runner.invoke(
            app, ["--data-dir", str(data_dir), "delete", fake_id]
        )
        assert result.exit_code == 1
        assert "not found" in result.output

    def test_delete_invalid_uuid(
        self, cli_runner: CliRunner, data_dir: Path
    ) -> None:
        """AC8: delete with invalid UUID shows error, exit code 1."""
        result = cli_runner.invoke(
            app, ["--data-dir", str(data_dir), "delete", "not-a-uuid"]
        )
        assert result.exit_code == 1
        assert "Invalid UUID" in result.output


class TestResumeCommand:
    """Tests for `ak-team resume` command (AC1, AC3, AC4, AC5 from Story 6.3)."""

    @patch("akgentic.team.cli.main._run_interactive")
    @patch("akgentic.team.manager.TeamManager.resume_team")
    def test_resume_stopped_team(
        self,
        mock_resume: MagicMock,
        mock_interactive: MagicMock,
        cli_runner: CliRunner,
        data_dir: Path,
        yaml_store: YamlEventStore,
    ) -> None:
        """AC1: resume stopped team shows team_id, exit code 0."""
        process = make_process(status=TeamStatus.STOPPED)
        yaml_store.save_team(process)

        mock_runtime = MagicMock()
        mock_runtime.id = process.team_id
        mock_resume.return_value = mock_runtime

        result = cli_runner.invoke(
            app,
            ["--data-dir", str(data_dir), "resume", str(process.team_id)],
        )
        assert result.exit_code == 0, result.output
        assert "Team resumed:" in result.output
        mock_resume.assert_called_once_with(process.team_id)
        mock_interactive.assert_called_once()

    def test_resume_running_team_shows_error(
        self,
        cli_runner: CliRunner,
        data_dir: Path,
        yaml_store: YamlEventStore,
    ) -> None:
        """AC3: resume a running team shows error, exit code 1."""
        process = make_process(status=TeamStatus.RUNNING)
        yaml_store.save_team(process)

        result = cli_runner.invoke(
            app, ["--data-dir", str(data_dir), "resume", str(process.team_id)]
        )
        assert result.exit_code == 1
        assert "running" in result.output.lower()

    def test_resume_deleted_team_shows_error(
        self,
        cli_runner: CliRunner,
        data_dir: Path,
        yaml_store: YamlEventStore,
    ) -> None:
        """AC3: resume a deleted team shows error, exit code 1."""
        process = make_process(status=TeamStatus.DELETED)
        yaml_store.save_team(process)

        result = cli_runner.invoke(
            app, ["--data-dir", str(data_dir), "resume", str(process.team_id)]
        )
        assert result.exit_code == 1
        assert "deleted" in result.output.lower()

    def test_resume_nonexistent_team_shows_error(
        self, cli_runner: CliRunner, data_dir: Path
    ) -> None:
        """AC4: resume non-existent team shows error, exit code 1."""
        fake_id = str(uuid.uuid4())
        result = cli_runner.invoke(
            app, ["--data-dir", str(data_dir), "resume", fake_id]
        )
        assert result.exit_code == 1
        assert "not found" in result.output.lower()

    def test_resume_invalid_uuid_shows_error(
        self, cli_runner: CliRunner, data_dir: Path
    ) -> None:
        """AC5: resume with invalid UUID shows error, exit code 1."""
        result = cli_runner.invoke(
            app, ["--data-dir", str(data_dir), "resume", "not-a-uuid"]
        )
        assert result.exit_code == 1
        assert "Invalid UUID" in result.output


class TestGracefulShutdown:
    """Tests for SIGINT-based graceful shutdown (AC2 from Story 6.3)."""

    def test_run_interactive_calls_stop_team_on_shutdown(self) -> None:
        """AC2: _run_interactive calls stop_team when shutdown event fires."""
        from akgentic.team.cli.main import _run_interactive

        mock_manager = MagicMock()
        mock_runtime = MagicMock()
        mock_runtime.id = uuid.uuid4()

        with (
            patch("akgentic.team.cli.main.signal.signal") as mock_signal,
            patch("akgentic.team.cli.main.threading.Event") as mock_event_cls,
        ):
            mock_event = MagicMock()
            mock_event_cls.return_value = mock_event
            # Make wait() return immediately (simulates shutdown event being set)
            mock_event.wait.return_value = None

            _run_interactive(mock_manager, mock_runtime)

            # Verify SIGINT handler was registered with the correct signal
            mock_signal.assert_called_once()
            assert mock_signal.call_args[0][0] == signal.SIGINT
            # Verify stop_team was called with correct id
            mock_manager.stop_team.assert_called_once_with(mock_runtime.id)

    def test_run_interactive_handles_stop_team_exception(self) -> None:
        """AC2: _run_interactive handles stop_team errors gracefully."""
        from akgentic.team.cli.main import _run_interactive

        mock_manager = MagicMock()
        mock_manager.stop_team.side_effect = RuntimeError("actors already dead")
        mock_runtime = MagicMock()
        mock_runtime.id = uuid.uuid4()

        with (
            patch("akgentic.team.cli.main.signal.signal"),
            patch("akgentic.team.cli.main.threading.Event") as mock_event_cls,
        ):
            mock_event = MagicMock()
            mock_event_cls.return_value = mock_event
            mock_event.wait.return_value = None

            # Should not raise even though stop_team fails
            _run_interactive(mock_manager, mock_runtime)
