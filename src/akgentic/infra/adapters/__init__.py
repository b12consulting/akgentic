"""Adapters module — re-exports from shared and community sub-packages.

This top-level ``__init__.py`` provides backwards-compatible imports.
All adapters can be imported directly from ``akgentic.infra.adapters``
or from their tier-specific sub-package (``shared/`` or ``community/``).
"""

from __future__ import annotations

from akgentic.infra.adapters.community import (
    LocalIngestion,
    LocalPlacement,
    LocalRuntimeCache,
    LocalTeamHandle,
    LocalWorkerHandle,
    NoAuth,
    NullChannelRegistry,
    YamlChannelRegistry,
)
from akgentic.infra.adapters.shared import (
    ChannelConfig,
    ChannelParserRegistry,
    InteractionChannelDispatcher,
    NullEventStream,
    NullStreamReader,
    TelegramChannelAdapter,
    TelegramChannelParser,
    TelemetrySubscriber,
)

__all__ = [
    "ChannelConfig",
    "ChannelParserRegistry",
    "InteractionChannelDispatcher",
    "LocalIngestion",
    "LocalPlacement",
    "LocalRuntimeCache",
    "LocalTeamHandle",
    "LocalWorkerHandle",
    "NoAuth",
    "NullChannelRegistry",
    "NullEventStream",
    "NullStreamReader",
    "TelegramChannelAdapter",
    "TelegramChannelParser",
    "TelemetrySubscriber",
    "YamlChannelRegistry",
]
