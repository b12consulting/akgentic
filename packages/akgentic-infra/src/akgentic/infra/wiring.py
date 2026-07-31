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
from akgentic.infra.adapters.community.yaml_channel_registry import YamlChannelRegistry
from akgentic.infra.adapters.shared.channel_parser_registry import ChannelParserRegistry
from akgentic.infra.adapters.shared.event_stream_subscriber import EventStreamSubscriber
from akgentic.infra.adapters.shared.telemetry_subscriber import TelemetrySubscriber
from akgentic.infra.server.auth_loader import load_auth_strategy
from akgentic.infra.server.deps import CommunityServices
from akgentic.infra.server.settings import CommunitySettings
from akgentic.team.manager import TeamManager
from akgentic.team.ports import NullServiceRegistry
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

    # Server services — request handling, auth, and catalog (no worker-runtime deps).
    # Auth defaults to NoAuth (settings.auth_strategy == "noauth") via the loader,
    # which short-circuits without any entry-point lookup or auth-library import.
    auth = load_auth_strategy(settings.auth_strategy)
    ingestion = LocalIngestion()
    channel_registry = YamlChannelRegistry(registry_path=settings.channel_registry_path)
    channel_parser_registry = ChannelParserRegistry(channels_config={})
    catalog = Catalog(repository=YamlEntryRepository(root=settings.catalog_path))

    # Shared backends — persistence and the event bus, used by server and worker alike.
    event_store = YamlEventStore(data_dir=settings.event_store_path)
    service_registry = NullServiceRegistry()
    event_stream = LocalEventStream()

    # Worker runtime — the in-process actor layer that runs the teams.
    actor_system = ActorSystem()
    shared_subscribers: list[EventSubscriber] = [
        TelemetrySubscriber(),
        EventStreamSubscriber(event_stream=event_stream),
    ]
    team_manager = TeamManager(
        actor_system=actor_system,
        event_store=event_store,
        service_registry=service_registry,
        subscribers=shared_subscribers,
    )

    # Worker-side handles — local adapters wrapping the embedded TeamManager.
    placement = LocalPlacement(team_manager, service_registry)
    worker_handle = LocalWorkerHandle(team_manager, service_registry, actor_system)
    runtime_cache = LocalRuntimeCache()
    runtime_cache.warm(worker_handle, event_store)

    return CommunityServices(
        # Server services
        auth=auth,
        ingestion=ingestion,
        channel_registry=channel_registry,
        channel_parser_registry=channel_parser_registry,
        catalog=catalog,
        # Shared backends
        event_store=event_store,
        service_registry=service_registry,
        event_stream=event_stream,
        # Worker runtime and local handles
        actor_system=actor_system,
        team_manager=team_manager,
        placement=placement,
        worker_handle=worker_handle,
        runtime_cache=runtime_cache,
    )
