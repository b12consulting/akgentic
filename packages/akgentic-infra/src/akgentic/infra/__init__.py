"""akgentic-infra: Infrastructure backend protocols and community-tier implementations."""

from __future__ import annotations

from pkgutil import extend_path

from akgentic.infra.adapters import (
    ChannelConfig,
    ChannelParserRegistry,
    InteractionChannelDispatcher,
    LocalIngestion,
    LocalPlacement,
    LocalRuntimeCache,
    LocalTeamHandle,
    LocalWorkerHandle,
    NoAuth,
    NullChannelRegistry,
    NullEventStream,
    NullStreamReader,
    TelegramChannelAdapter,
    TelegramChannelParser,
    TelemetrySubscriber,
    YamlChannelRegistry,
)
from akgentic.infra.protocols import (
    AuthStrategy,
    ChannelMessage,
    ChannelParser,
    ChannelRegistry,
    EventStream,
    HealthMonitor,
    InteractionChannelAdapter,
    InteractionChannelIngestion,
    JsonValue,
    PlacementStrategy,
    RecoveryPolicy,
    RuntimeCache,
    StreamClosed,
    StreamReader,
    TeamHandle,
    WorkerHandle,
)
from akgentic.infra.server.app import create_app
from akgentic.infra.server.deps import CommunityServices, TierServices
from akgentic.infra.server.models import (
    CreateTeamRequest,
    EventListResponse,
    EventResponse,
    HumanInputRequest,
    SendMessageRequest,
    TeamListResponse,
    TeamResponse,
    WorkspaceFileEntry,
    WorkspaceFileUploadResponse,
    WorkspaceTreeResponse,
)
from akgentic.infra.server.services.team_service import TeamService
from akgentic.infra.server.settings import CommunitySettings, ServerSettings
from akgentic.infra.wiring import wire_community

__path__ = extend_path(__path__, __name__)

__all__ = [
    # Protocols
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
    # Adapters
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
    # Server
    "CommunitySettings",
    "CommunityServices",
    "CreateTeamRequest",
    "EventListResponse",
    "EventResponse",
    "HumanInputRequest",
    "SendMessageRequest",
    "ServerSettings",
    "TeamListResponse",
    "TeamResponse",
    "TeamService",
    "TierServices",
    "WorkspaceFileEntry",
    "WorkspaceFileUploadResponse",
    "WorkspaceTreeResponse",
    "create_app",
    # Wiring
    "wire_community",
]
