"""Shared adapters — tier-agnostic implementations reusable by all deployment profiles.

Adapters in this package are independent of deployment topology. They integrate
with external services, provide dispatch infrastructure, or implement logic that
every tier needs identically. A future pro or enterprise tier imports from here
without modification.
"""

from __future__ import annotations

from akgentic.infra.adapters.shared.channel_dispatcher import InteractionChannelDispatcher
from akgentic.infra.adapters.shared.channel_parser_registry import (
    ChannelConfig,
    ChannelParserRegistry,
)
from akgentic.infra.adapters.shared.event_stream_subscriber import EventStreamSubscriber
from akgentic.infra.adapters.shared.null_event_stream import NullEventStream, NullStreamReader
from akgentic.infra.adapters.shared.telegram_adapter import TelegramChannelAdapter
from akgentic.infra.adapters.shared.telegram_parser import TelegramChannelParser
from akgentic.infra.adapters.shared.telemetry_subscriber import TelemetrySubscriber

__all__ = [
    "ChannelConfig",
    "ChannelParserRegistry",
    "EventStreamSubscriber",
    "InteractionChannelDispatcher",
    "NullEventStream",
    "NullStreamReader",
    "TelegramChannelAdapter",
    "TelegramChannelParser",
    "TelemetrySubscriber",
]
