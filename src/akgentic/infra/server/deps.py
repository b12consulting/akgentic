"""Dependency injection containers for tiered service assembly."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, SkipValidation

from akgentic.catalog import Catalog
from akgentic.core import ActorSystem
from akgentic.infra.adapters.shared.channel_parser_registry import ChannelParserRegistry
from akgentic.infra.protocols.auth import AuthStrategy
from akgentic.infra.protocols.channels import ChannelRegistry, InteractionChannelIngestion
from akgentic.infra.protocols.event_stream import EventStream
from akgentic.infra.protocols.placement import PlacementStrategy
from akgentic.infra.protocols.runtime_cache import RuntimeCache
from akgentic.infra.protocols.worker_handle import WorkerHandle
from akgentic.team.manager import TeamManager
from akgentic.team.ports import EventStore, ServiceRegistry


class TierServices(BaseModel):
    """Base dependency container holding services common to all deployment tiers.

    This is a runtime DI container, NOT a serialization model — arbitrary_types_allowed
    is intentional (Golden Rule #1b exemption).
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    placement: PlacementStrategy = Field(description="Strategy for placing teams on workers")
    worker_handle: WorkerHandle = Field(description="Worker-level team lifecycle operations")
    auth: AuthStrategy = Field(description="Authentication strategy for incoming requests")
    event_store: SkipValidation[EventStore] = Field(
        description="Persistence backend for team event sourcing"
    )
    runtime_cache: RuntimeCache = Field(
        description="Cache mapping team IDs to live TeamHandle instances"
    )
    event_stream: EventStream = Field(
        description="Ephemeral event bus for cursor-based replay and fan-out"
    )
    ingestion: InteractionChannelIngestion = Field(
        description="Inbound channel message ingestion handler"
    )
    channel_registry: ChannelRegistry = Field(
        description="Registry mapping channel IDs to team IDs"
    )
    catalog: Catalog = Field(description="v2 unified catalog service")


class CommunityServices(TierServices):
    """Community-tier service container with embedded TeamManager and catalog.

    Extends TierServices with an in-process TeamManager and the YAML-backed
    unified catalog for single-process deployment.
    """

    service_registry: ServiceRegistry = Field(
        description="Service discovery registry (community-specific, used by wiring)"
    )
    actor_system: ActorSystem = Field(description="Actor system for managing agent lifecycle")
    team_manager: TeamManager = Field(description="Team lifecycle manager (embedded, in-process)")
    channel_parser_registry: ChannelParserRegistry = Field(
        description="Registry of channel message parsers"
    )
