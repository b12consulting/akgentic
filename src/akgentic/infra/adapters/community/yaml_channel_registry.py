"""YAML-backed channel registry — maps channel users to teams via a YAML file."""

from __future__ import annotations

import logging
import uuid
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)


class YamlChannelRegistry:
    """Persists channel-user-to-team mappings in a YAML file.

    File format:
        channel_name:
          channel_user_id: "team_uuid_string"

    Satisfies the ``ChannelRegistry`` protocol.
    """

    def __init__(self, registry_path: Path) -> None:
        self._path = registry_path

    def _load(self) -> dict[str, dict[str, str]]:
        """Load registry data from YAML, returning empty dict if file missing."""
        if not self._path.exists():
            return {}
        text = self._path.read_text(encoding="utf-8")
        data = yaml.safe_load(text)
        if data is None:
            return {}
        return dict(data)

    def _save(self, data: dict[str, dict[str, str]]) -> None:
        """Write registry data to YAML."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(yaml.safe_dump(data, default_flow_style=False), encoding="utf-8")

    async def register(self, channel: str, channel_user_id: str, team_id: uuid.UUID) -> None:
        """Add a channel-user → team mapping."""
        data = self._load()
        if channel not in data:
            data[channel] = {}
        data[channel][channel_user_id] = str(team_id)
        self._save(data)
        logger.debug(
            "Channel registry: registered %s/%s → team %s",
            channel,
            channel_user_id,
            team_id,
        )

    async def find_team(self, channel: str, channel_user_id: str) -> uuid.UUID | None:
        """Look up the team for a channel user, or return None."""
        data = self._load()
        channel_data = data.get(channel)
        if channel_data is None:
            return None
        team_str = channel_data.get(channel_user_id)
        if team_str is None:
            logger.debug("Channel registry: lookup %s/%s → None", channel, channel_user_id)
            return None
        logger.debug("Channel registry: lookup %s/%s → %s", channel, channel_user_id, team_str)
        return uuid.UUID(team_str)

    async def deregister(self, channel: str, channel_user_id: str) -> None:
        """Remove a channel-user mapping if it exists."""
        data = self._load()
        channel_data = data.get(channel)
        if channel_data is None:
            return
        channel_data.pop(channel_user_id, None)
        if not channel_data:
            del data[channel]
        self._save(data)
        logger.debug("Channel registry: deregistered %s/%s", channel, channel_user_id)
