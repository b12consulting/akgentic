"""Tests for YamlChannelRegistry — YAML-backed channel-user-to-team mapping."""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
import yaml

from akgentic.infra.adapters.community.yaml_channel_registry import YamlChannelRegistry
from akgentic.infra.protocols import ChannelRegistry


@pytest.fixture()
def registry_path(tmp_path: Path) -> Path:
    """Return a temporary path for the registry YAML file."""
    return tmp_path / "channel_registry.yaml"


@pytest.fixture()
def registry(registry_path: Path) -> YamlChannelRegistry:
    """Return a fresh YamlChannelRegistry instance."""
    return YamlChannelRegistry(registry_path)


async def test_satisfies_channel_registry_protocol() -> None:
    """YamlChannelRegistry satisfies the ChannelRegistry protocol."""
    assert isinstance(YamlChannelRegistry(Path("/tmp/fake.yaml")), ChannelRegistry)


async def test_register_creates_mapping(registry: YamlChannelRegistry, registry_path: Path) -> None:
    """register() persists a channel-user → team mapping to YAML."""
    team_id = uuid.uuid4()
    await registry.register("whatsapp", "+1234567890", team_id)

    data = yaml.safe_load(registry_path.read_text())  # noqa: ASYNC240
    assert data["whatsapp"]["+1234567890"] == str(team_id)


async def test_find_team_returns_uuid(registry: YamlChannelRegistry) -> None:
    """find_team() returns the UUID for a registered channel user."""
    team_id = uuid.uuid4()
    await registry.register("slack", "U12345", team_id)

    result = await registry.find_team("slack", "U12345")
    assert result == team_id


async def test_find_team_returns_none_for_unknown_channel(
    registry: YamlChannelRegistry,
) -> None:
    """find_team() returns None for an unregistered channel."""
    result = await registry.find_team("whatsapp", "+9999999999")
    assert result is None


async def test_find_team_returns_none_for_unknown_user(
    registry: YamlChannelRegistry,
) -> None:
    """find_team() returns None when channel exists but user doesn't."""
    await registry.register("slack", "U11111", uuid.uuid4())

    result = await registry.find_team("slack", "U99999")
    assert result is None


async def test_deregister_removes_mapping(registry: YamlChannelRegistry) -> None:
    """deregister() removes the mapping for a channel user."""
    team_id = uuid.uuid4()
    await registry.register("whatsapp", "+1234567890", team_id)
    await registry.deregister("whatsapp", "+1234567890")

    result = await registry.find_team("whatsapp", "+1234567890")
    assert result is None


async def test_deregister_unknown_channel_is_noop(
    registry: YamlChannelRegistry,
) -> None:
    """deregister() on an unknown channel does not raise."""
    await registry.deregister("nonexistent", "nobody")


async def test_deregister_unknown_user_is_noop(
    registry: YamlChannelRegistry,
) -> None:
    """deregister() for unknown user in existing channel does not raise."""
    await registry.register("slack", "U11111", uuid.uuid4())
    await registry.deregister("slack", "U99999")


async def test_missing_file_treated_as_empty(registry_path: Path) -> None:
    """A non-existent YAML file is treated as an empty registry."""
    reg = YamlChannelRegistry(registry_path)
    result = await reg.find_team("whatsapp", "+1234567890")
    assert result is None


async def test_empty_file_treated_as_empty(registry_path: Path) -> None:
    """An empty YAML file is treated as an empty registry."""
    registry_path.write_text("")  # noqa: ASYNC240
    reg = YamlChannelRegistry(registry_path)
    result = await reg.find_team("whatsapp", "+1234567890")
    assert result is None


async def test_multiple_channels(registry: YamlChannelRegistry) -> None:
    """Multiple channels with different users are tracked independently."""
    t1 = uuid.uuid4()
    t2 = uuid.uuid4()
    await registry.register("whatsapp", "+111", t1)
    await registry.register("slack", "U222", t2)

    assert await registry.find_team("whatsapp", "+111") == t1
    assert await registry.find_team("slack", "U222") == t2


async def test_multiple_users_same_channel(registry: YamlChannelRegistry) -> None:
    """Multiple users in the same channel each get their own mapping."""
    t1 = uuid.uuid4()
    t2 = uuid.uuid4()
    await registry.register("slack", "U111", t1)
    await registry.register("slack", "U222", t2)

    assert await registry.find_team("slack", "U111") == t1
    assert await registry.find_team("slack", "U222") == t2


async def test_uuid_serialization_roundtrip(registry: YamlChannelRegistry) -> None:
    """UUID is serialized to string and deserialized back correctly."""
    team_id = uuid.UUID("550e8400-e29b-41d4-a716-446655440000")
    await registry.register("whatsapp", "+1234567890", team_id)

    result = await registry.find_team("whatsapp", "+1234567890")
    assert result == team_id
    assert isinstance(result, uuid.UUID)


async def test_deregister_removes_empty_channel_section(
    registry: YamlChannelRegistry, registry_path: Path
) -> None:
    """Deregistering the last user in a channel removes the channel section."""
    await registry.register("whatsapp", "+111", uuid.uuid4())
    await registry.deregister("whatsapp", "+111")

    data = yaml.safe_load(registry_path.read_text())  # noqa: ASYNC240
    assert data is None or "whatsapp" not in (data or {})


async def test_register_overwrites_existing(registry: YamlChannelRegistry) -> None:
    """Re-registering the same user updates the team ID."""
    t1 = uuid.uuid4()
    t2 = uuid.uuid4()
    await registry.register("slack", "U111", t1)
    await registry.register("slack", "U111", t2)

    assert await registry.find_team("slack", "U111") == t2
