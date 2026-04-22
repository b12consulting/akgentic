"""Infrastructure protocols — stable abstractions for community and tier implementations."""

from __future__ import annotations

from akgentic.infra.protocols.auth import AuthStrategy
from akgentic.infra.protocols.channels import (
    ChannelMessage,
    ChannelParser,
    ChannelRegistry,
    InteractionChannelAdapter,
    InteractionChannelIngestion,
    JsonValue,
)
from akgentic.infra.protocols.event_stream import EventStream, StreamClosed, StreamReader
from akgentic.infra.protocols.health import HealthMonitor
from akgentic.infra.protocols.placement import PlacementStrategy
from akgentic.infra.protocols.recovery import RecoveryPolicy
from akgentic.infra.protocols.runtime_cache import RuntimeCache
from akgentic.infra.protocols.team_handle import TeamHandle
from akgentic.infra.protocols.worker_handle import WorkerHandle

__all__ = [
    "AuthStrategy",
    "ChannelMessage",
    "ChannelParser",
    "ChannelRegistry",
    "EventStream",
    "HealthMonitor",
    "InteractionChannelAdapter",
    "InteractionChannelIngestion",
    "JsonValue",
    "PlacementStrategy",
    "RecoveryPolicy",
    "RuntimeCache",
    "StreamClosed",
    "StreamReader",
    "TeamHandle",
    "WorkerHandle",
]
