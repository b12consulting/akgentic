"""YamlEventStore: file-based EventStore with per-team directory layout.

Persists team data to YAML files in a per-team directory structure using
PyYAML for serialization and pathlib for filesystem operations. Satisfies
the EventStore protocol via structural subtyping (no explicit inheritance).

File layout per team::

    {data_dir}/
      {team_uuid}/
        team.yaml           # Process metadata (overwrite)
        events.yaml         # Append-only event log (multi-document YAML)
        states/
          {agent_id}.yaml   # Latest agent state snapshot (overwrite)
"""

from __future__ import annotations

import logging
import shutil
import tempfile
import uuid
from pathlib import Path

import yaml

from akgentic.team.models import AgentStateSnapshot, PersistedEvent, Process

logger = logging.getLogger(__name__)


class YamlEventStore:
    """File-based EventStore using YAML serialization with per-team directories.

    Satisfies the ``EventStore`` protocol via structural subtyping without
    inheriting from it. All filesystem directories are created on demand
    (not eagerly at instantiation time).

    Args:
        data_dir: Root directory for all persisted team data.
    """

    def __init__(self, data_dir: Path) -> None:
        self._data_dir = data_dir

    @staticmethod
    def _atomic_write(path: Path, data: dict[str, object]) -> None:
        """Write YAML data atomically using write-to-temp-then-rename.

        Prevents corrupted partial files if the process crashes mid-write.

        Args:
            path: Destination file path.
            data: Dictionary to serialize as YAML.
        """
        fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
        try:
            with open(fd, "w") as f:
                yaml.dump(data, f, default_flow_style=False)
            Path(tmp).replace(path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise

    def _team_dir(self, team_id: uuid.UUID) -> Path:
        """Return the directory path for a specific team.

        Args:
            team_id: Unique identifier of the team.

        Returns:
            Path to the team's directory under the data root.
        """
        return self._data_dir / str(team_id)

    def save_team(self, process: Process) -> None:
        """Persist a team process snapshot to team.yaml.

        Creates the team directory if it does not exist, then writes
        (or overwrites) the serialized Process to ``team.yaml``.

        Args:
            process: The team process snapshot to persist.
        """
        team_dir = self._team_dir(process.team_id)
        team_dir.mkdir(parents=True, exist_ok=True)
        team_path = team_dir / "team.yaml"
        self._atomic_write(team_path, process.model_dump())
        logger.debug("Saved team %s to %s", process.team_id, team_path)

    def load_team(self, team_id: uuid.UUID) -> Process | None:
        """Load a team process snapshot from team.yaml.

        Args:
            team_id: Unique identifier of the team.

        Returns:
            The deserialized Process, or None if no team.yaml exists.
        """
        team_path = self._team_dir(team_id) / "team.yaml"
        if not team_path.exists():
            return None
        try:
            with open(team_path) as f:
                data = yaml.safe_load(f)
            process = Process.model_validate(data)
        except (yaml.YAMLError, ValueError) as exc:
            logger.error("Corrupted team.yaml for team %s: %s", team_id, exc)
            return None
        logger.debug("Loaded team %s from %s", team_id, team_path)
        return process

    def list_teams(self) -> list[Process]:
        """Load all team process snapshots from the data directory.

        Iterates subdirectories of ``data_dir``, attempts to parse each
        directory name as a UUID, and loads the team snapshot for valid
        team directories. Non-UUID directories are skipped with a warning.

        Returns:
            List of all loadable Process snapshots.
        """
        if not self._data_dir.exists():
            return []
        teams: list[Process] = []
        for child in sorted(self._data_dir.iterdir()):
            if not child.is_dir():
                continue
            try:
                team_id = uuid.UUID(child.name)
            except ValueError:
                logger.warning("Skipping non-team directory: %s", child.name)
                continue
            process = self.load_team(team_id)
            if process is not None:
                teams.append(process)
        return teams

    def save_event(self, event: PersistedEvent) -> None:
        """Append a persisted event to events.yaml.

        Uses multi-document YAML format (documents separated by ``---``)
        for append-only semantics. Creates the team directory if needed.

        Args:
            event: The event to append.
        """
        team_dir = self._team_dir(event.team_id)
        team_dir.mkdir(parents=True, exist_ok=True)
        events_path = team_dir / "events.yaml"
        data = event.model_dump()
        with open(events_path, "a") as f:
            f.write("---\n")
            yaml.dump(data, f, default_flow_style=False)
        logger.debug("Appended event seq=%d for team %s", event.sequence, event.team_id)

    def load_events(self, team_id: uuid.UUID) -> list[PersistedEvent]:
        """Load all persisted events for a team from events.yaml.

        Reads multi-document YAML and reconstructs each document as a
        PersistedEvent, returned sorted by sequence number.

        Args:
            team_id: Unique identifier of the team.

        Returns:
            List of PersistedEvent ordered by sequence, or empty list if
            no events file exists.
        """
        events_path = self._team_dir(team_id) / "events.yaml"
        if not events_path.exists():
            return []
        try:
            with open(events_path) as f:
                docs = list(yaml.safe_load_all(f))
        except yaml.YAMLError as exc:
            logger.error("Corrupted events.yaml for team %s: %s", team_id, exc)
            return []
        events: list[PersistedEvent] = []
        for doc in docs:
            if doc is None:
                continue
            try:
                events.append(PersistedEvent.model_validate(doc))
            except ValueError as exc:
                logger.warning(
                    "Skipping corrupted event for team %s: %s", team_id, exc
                )
        logger.debug("Loaded %d events for team %s", len(events), team_id)
        return sorted(events, key=lambda e: e.sequence)

    def get_max_sequence(self, team_id: uuid.UUID) -> int:
        """Return the highest event sequence number for a team, or 0.

        Loads all events and computes the max in Python. This is acceptable
        for a file-based store; database-backed stores should use an
        efficient query instead.

        Args:
            team_id: Unique identifier of the team.

        Returns:
            The highest sequence number, or 0 if no events exist.
        """
        events = self.load_events(team_id)
        return max((e.sequence for e in events), default=0)

    def save_agent_state(self, snapshot: AgentStateSnapshot) -> None:
        """Persist an agent state snapshot to states/{agent_id}.yaml.

        Creates the states directory if it does not exist, then writes
        (or overwrites) the serialized snapshot.

        Args:
            snapshot: The agent state snapshot to persist.
        """
        states_dir = self._team_dir(snapshot.team_id) / "states"
        states_dir.mkdir(parents=True, exist_ok=True)
        state_path = states_dir / f"{snapshot.agent_id}.yaml"
        self._atomic_write(state_path, snapshot.model_dump())
        logger.debug(
            "Saved agent state %s for team %s", snapshot.agent_id, snapshot.team_id
        )

    def load_agent_states(self, team_id: uuid.UUID) -> list[AgentStateSnapshot]:
        """Load all agent state snapshots for a team from states/ directory.

        Args:
            team_id: Unique identifier of the team.

        Returns:
            List of AgentStateSnapshot, or empty list if no states
            directory exists.
        """
        states_dir = self._team_dir(team_id) / "states"
        if not states_dir.exists():
            return []
        snapshots: list[AgentStateSnapshot] = []
        for state_path in sorted(states_dir.glob("*.yaml")):
            try:
                with open(state_path) as f:
                    data = yaml.safe_load(f)
                snapshots.append(AgentStateSnapshot.model_validate(data))
            except (yaml.YAMLError, ValueError) as exc:
                logger.warning(
                    "Skipping corrupted state file %s for team %s: %s",
                    state_path.name,
                    team_id,
                    exc,
                )
        logger.debug("Loaded %d agent states for team %s", len(snapshots), team_id)
        return snapshots

    def delete_team(self, team_id: uuid.UUID) -> None:
        """Delete all persisted data for a team.

        Removes the entire team directory and all contents. If the directory
        does not exist, this is a no-op (no error raised).

        Args:
            team_id: Unique identifier of the team to delete.
        """
        team_dir = self._team_dir(team_id)
        if team_dir.exists():
            shutil.rmtree(team_dir)
            logger.debug("Deleted team directory %s", team_dir)
