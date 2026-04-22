"""Community adapters — single-process, zero-infrastructure implementations.

Adapters in this package provide local, in-memory, or file-backed stand-ins for
infrastructure protocols. They enable the community tier to run with no external
dependencies (no Redis, no message queue, no auth provider). A distributed tier
replaces these with network-aware alternatives.
"""

from __future__ import annotations

from akgentic.infra.adapters.community.local_event_stream import (
    LocalEventStream,
    LocalStreamReader,
)
from akgentic.infra.adapters.community.local_ingestion import LocalIngestion
from akgentic.infra.adapters.community.local_placement import LocalPlacement
from akgentic.infra.adapters.community.local_runtime_cache import LocalRuntimeCache
from akgentic.infra.adapters.community.local_team_handle import LocalTeamHandle
from akgentic.infra.adapters.community.local_worker_handle import LocalWorkerHandle
from akgentic.infra.adapters.community.no_auth import NoAuth
from akgentic.infra.adapters.community.null_channel_registry import NullChannelRegistry
from akgentic.infra.adapters.community.yaml_channel_registry import YamlChannelRegistry

__all__ = [
    "LocalEventStream",
    "LocalStreamReader",
    "LocalIngestion",
    "LocalPlacement",
    "LocalRuntimeCache",
    "LocalTeamHandle",
    "LocalWorkerHandle",
    "NoAuth",
    "NullChannelRegistry",
    "YamlChannelRegistry",
]
