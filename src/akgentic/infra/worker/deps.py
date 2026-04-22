"""Dependency injection container for worker processes."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, SkipValidation

from akgentic.core import ActorSystem
from akgentic.infra.protocols.runtime_cache import RuntimeCache
from akgentic.infra.protocols.worker_handle import WorkerHandle
from akgentic.team.manager import TeamManager
from akgentic.team.ports import EventStore, ServiceRegistry


class WorkerServices(BaseModel):
    """Runtime DI container for worker processes.

    Holds worker-side dependencies: team lifecycle management, actor system,
    event sourcing, service discovery, and runtime caching. Enterprise and
    department tiers extend this with tier-specific adapters (Dapr, Redis).

    This is a runtime DI container, NOT a serialization model —
    arbitrary_types_allowed is intentional (Golden Rule #1b exemption).
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    team_manager: TeamManager = Field(description="Team lifecycle manager")
    actor_system: ActorSystem = Field(description="Actor system for agent lifecycle")
    event_store: SkipValidation[EventStore] = Field(
        description="Persistence backend for team event sourcing"
    )
    service_registry: ServiceRegistry = Field(description="Service discovery registry")
    runtime_cache: RuntimeCache = Field(
        description="Cache mapping team IDs to live TeamHandle instances"
    )
    worker_handle: WorkerHandle = Field(description="Local worker handle for this process")
