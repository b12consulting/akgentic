"""Tests for NullChannelRegistry — no-op channel registry when channels are disabled."""

from __future__ import annotations

import uuid

from akgentic.infra.adapters.community.null_channel_registry import NullChannelRegistry
from akgentic.infra.protocols import ChannelRegistry


async def test_satisfies_channel_registry_protocol() -> None:
    """NullChannelRegistry satisfies the ChannelRegistry protocol."""
    assert isinstance(NullChannelRegistry(), ChannelRegistry)


async def test_find_team_returns_none() -> None:
    """find_team() always returns None."""
    registry = NullChannelRegistry()
    result = await registry.find_team("whatsapp", "+1234567890")
    assert result is None


async def test_register_is_noop() -> None:
    """register() does not raise."""
    registry = NullChannelRegistry()
    await registry.register("slack", "U12345", uuid.uuid4())


async def test_deregister_is_noop() -> None:
    """deregister() does not raise."""
    registry = NullChannelRegistry()
    await registry.deregister("slack", "U12345")
