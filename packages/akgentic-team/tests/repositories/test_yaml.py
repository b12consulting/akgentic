"""YAML-specific tests for ``YamlEventStore``.

Behavioural Protocol coverage (round-trip, upsert, list, sequencing,
max sequence, cascading delete, polymorphic round-trips) lives in the
shared ``tests/repositories/test_event_store_contract.py`` and runs
once per backend. This module retains only YAML-specific invariants:

* Protocol structural-typing check.
* On-disk directory-layout and lazy-creation behaviour.
* List-teams skipping non-UUID directories.
* Corrupted-file resilience (YAML parser errors → ``None`` / ``[]`` /
  skip rather than raise — this is the YamlEventStore contract).
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from akgentic.team.repositories.yaml import YamlEventStore

if TYPE_CHECKING:
    from akgentic.team.ports import EventStore

from tests.models.conftest import (
    make_agent_state_snapshot,
    make_persisted_event,
    make_process,
)


@pytest.fixture
def yaml_store(tmp_path: Path) -> YamlEventStore:
    """Create a YamlEventStore backed by a temporary directory."""
    return YamlEventStore(tmp_path)


class TestYamlEventStoreYamlSpecific:
    """YAML-only invariants — see contract suite for behavioural coverage."""

    # --- Protocol compliance ------------------------------------------------

    def test_satisfies_event_store_protocol(self, tmp_path: Path) -> None:
        """``YamlEventStore`` satisfies ``EventStore`` Protocol structurally."""
        store: EventStore = YamlEventStore(tmp_path)
        assert store is not None

    # --- On-disk layout / directory creation --------------------------------

    def test_directory_creation_is_automatic(self, yaml_store: YamlEventStore) -> None:
        """Per-team directories are created on demand, not eagerly."""
        team_id = uuid.uuid4()
        # Save event without pre-creating any dirs
        event = make_persisted_event(team_id=team_id, sequence=1)
        yaml_store.save_event(event)  # should not raise

        loaded = yaml_store.load_events(team_id)
        assert len(loaded) == 1

    def test_list_teams_ignores_non_team_directories(
        self, yaml_store: YamlEventStore, tmp_path: Path
    ) -> None:
        """``list_teams`` skips non-UUID directories like ``.gitkeep``."""
        p1 = make_process()
        yaml_store.save_team(p1)
        # Create non-team entries
        (tmp_path / ".gitkeep").touch()
        (tmp_path / "__pycache__").mkdir()

        result = yaml_store.list_teams()
        assert len(result) == 1
        assert result[0].team_id == p1.team_id

    # --- Corrupted-file resilience ------------------------------------------

    def test_load_team_returns_none_for_corrupted_yaml(self, tmp_path: Path) -> None:
        """Corrupted ``team.yaml`` returns None instead of raising."""
        store = YamlEventStore(tmp_path)
        team_id = uuid.uuid4()
        team_dir = tmp_path / str(team_id)
        team_dir.mkdir()
        (team_dir / "team.yaml").write_text("{{invalid: yaml: [}")
        assert store.load_team(team_id) is None

    def test_load_events_returns_empty_for_corrupted_yaml(self, tmp_path: Path) -> None:
        """Corrupted ``events.yaml`` returns empty list instead of raising."""
        store = YamlEventStore(tmp_path)
        team_id = uuid.uuid4()
        team_dir = tmp_path / str(team_id)
        team_dir.mkdir()
        (team_dir / "events.yaml").write_text("{{invalid: yaml: [}")
        assert store.load_events(team_id) == []

    def test_load_agent_states_skips_corrupted_files(self, tmp_path: Path) -> None:
        """A corrupted state file is skipped; valid ones are still loaded."""
        store = YamlEventStore(tmp_path)
        team_id = uuid.uuid4()
        # Save a valid state first
        snap = make_agent_state_snapshot(team_id=team_id, agent_id="good-agent")
        store.save_agent_state(snap)
        # Write a corrupted state file
        states_dir = tmp_path / str(team_id) / "states"
        (states_dir / "bad-agent.yaml").write_text("{{invalid: yaml: [}")
        loaded = store.load_agent_states(team_id)
        assert len(loaded) == 1
        assert loaded[0].agent_id == "good-agent"
