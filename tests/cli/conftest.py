"""Test fixtures for team CLI tests."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from akgentic.team.models import Process, TeamStatus
from akgentic.team.repositories.yaml import YamlEventStore
from tests.models.conftest import make_process


@pytest.fixture
def cli_runner() -> CliRunner:
    """Create a Typer CliRunner for CLI tests."""
    return CliRunner()


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    """Create a temporary data directory for CLI tests."""
    d = tmp_path / "data"
    d.mkdir()
    return d


@pytest.fixture
def yaml_store(data_dir: Path) -> YamlEventStore:
    """Create a YamlEventStore backed by a temporary directory."""
    return YamlEventStore(data_dir)


def populate_teams(
    store: YamlEventStore,
    count: int = 2,
    status: TeamStatus = TeamStatus.RUNNING,
) -> list[Process]:
    """Pre-populate the store with team processes.

    Args:
        store: The YamlEventStore to populate.
        count: Number of teams to create.
        status: Status for all created teams.

    Returns:
        List of created Process instances.
    """
    teams: list[Process] = []
    for _ in range(count):
        p = make_process(status=status)
        store.save_team(p)
        teams.append(p)
    return teams
