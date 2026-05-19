"""Community-tier service wiring — assembles all adapters for single-process deployment."""

from __future__ import annotations

import logging

from akgentic.catalog import Catalog, YamlEntryRepository
from akgentic.core import ActorSystem, EventSubscriber
from akgentic.infra.adapters.community.local_event_stream import LocalEventStream
from akgentic.infra.adapters.community.local_ingestion import LocalIngestion
from akgentic.infra.adapters.community.local_placement import LocalPlacement
from akgentic.infra.adapters.community.local_runtime_cache import LocalRuntimeCache
from akgentic.infra.adapters.community.local_worker_handle import LocalWorkerHandle
from akgentic.infra.adapters.community.no_auth import NoAuth
from akgentic.infra.adapters.community.null_channel_registry import NullChannelRegistry
from akgentic.infra.adapters.community.yaml_channel_registry import YamlChannelRegistry
from akgentic.infra.adapters.shared.channel_parser_registry import ChannelParserRegistry
from akgentic.infra.adapters.shared.event_stream_subscriber import EventStreamSubscriber
from akgentic.infra.adapters.shared.telemetry_subscriber import TelemetrySubscriber
from akgentic.infra.protocols.event_stream import EventStream
from akgentic.infra.server.deps import CommunityServices
from akgentic.infra.server.settings import CommunitySettings
from akgentic.team.manager import TeamManager
from akgentic.team.ports import EventStore, NullServiceRegistry, ServiceRegistry
from akgentic.team.repositories.yaml import YamlEventStore

logger = logging.getLogger(__name__)


def wire_community(settings: CommunitySettings) -> CommunityServices:
    """Assemble community-tier services for single-process deployment.

    ``LocalIngestion`` is created with deferred ``team_service`` wiring —
    callers must set ``ingestion.team_service`` after constructing ``TeamService``.

    Args:
        settings: Community-tier configuration

    Returns:
        Fully wired CommunityServices container
    """
    logger.info("Wiring community services")
    event_store = YamlEventStore(data_dir=settings.event_store_path)
    service_registry = NullServiceRegistry()
    event_stream = LocalEventStream()
    actor_system, team_manager = _build_actor_layer(event_store, service_registry, event_stream)
    catalog = _build_catalog(settings)

    local_placement = LocalPlacement(team_manager, service_registry)
    local_worker_handle = LocalWorkerHandle(team_manager, service_registry, actor_system)

    logger.info(
        "Community services wired: placement=%s, worker=%s",
        type(local_placement).__name__,
        type(local_worker_handle).__name__,
    )
    runtime_cache = LocalRuntimeCache()
    runtime_cache.warm(local_worker_handle, event_store)

    return CommunityServices(
        placement=local_placement,
        worker_handle=local_worker_handle,
        service_registry=service_registry,
        auth=NoAuth(),
        event_store=event_store,
        runtime_cache=runtime_cache,
        event_stream=event_stream,
        ingestion=LocalIngestion(),
        channel_registry=(
            YamlChannelRegistry(registry_path=settings.channel_registry_path)
            if settings.channel_registry_path is not None
            else NullChannelRegistry()
        ),
        actor_system=actor_system,
        team_manager=team_manager,
        channel_parser_registry=ChannelParserRegistry(channels_config={}),
        catalog=catalog,
    )


def _build_actor_layer(
    event_store: EventStore,
    service_registry: ServiceRegistry,
    event_stream: EventStream,
) -> tuple[ActorSystem, TeamManager]:
    """Build ActorSystem and TeamManager."""
    logger.debug("Building actor layer: event_store=%s", type(event_store).__name__)
    shared_subscribers: list[EventSubscriber] = [
        TelemetrySubscriber(),
        EventStreamSubscriber(event_stream=event_stream),
    ]
    actor_system = ActorSystem()
    team_manager = TeamManager(
        actor_system=actor_system,
        event_store=event_store,
        service_registry=service_registry,
        subscribers=shared_subscribers,
    )
    logger.debug(
        "Actor system created, team manager initialized with %d shared subscribers",
        len(shared_subscribers),
    )
    return actor_system, team_manager


def _build_catalog(settings: CommunitySettings) -> Catalog:
    """Build the v2 unified ``Catalog`` service backed by ``YamlEntryRepository``."""
    logger.debug("Building unified catalog from %s", settings.catalog_path)
    return Catalog(repository=YamlEntryRepository(root=settings.catalog_path))
