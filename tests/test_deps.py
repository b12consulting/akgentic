"""Tests for TierServices dependency injection container."""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock

from akgentic.catalog.services import (
    AgentCatalog,
    TeamCatalog,
    TemplateCatalog,
    ToolCatalog,
)
from akgentic.team.models import AgentStateSnapshot, PersistedEvent, Process

from akgentic.infra.protocols.auth import AuthStrategy
from akgentic.infra.protocols.channels import ChannelRegistry, InteractionChannelIngestion
from akgentic.infra.protocols.event_stream import EventStream
from akgentic.infra.protocols.placement import PlacementStrategy
from akgentic.infra.protocols.runtime_cache import RuntimeCache
from akgentic.infra.protocols.worker_handle import WorkerHandle
from akgentic.infra.server.deps import TierServices


class FakeEventStore:
    """Minimal EventStore-shaped class satisfying the protocol via structural subtyping.

    Does NOT inherit from EventStore -- validates that Pydantic accepts
    protocol-typed fields through structural subtyping when
    arbitrary_types_allowed=True and SkipValidation is used.
    """

    def save_event(self, event: PersistedEvent) -> None:
        """No-op stub."""

    def load_events(self, team_id: uuid.UUID) -> list[PersistedEvent]:
        """Return empty list."""
        return []

    def save_team(self, process: Process) -> None:
        """No-op stub."""

    def load_team(self, team_id: uuid.UUID) -> Process | None:
        """Return None."""
        return None

    def delete_team(self, team_id: uuid.UUID) -> None:
        """No-op stub."""

    def save_agent_state(self, snapshot: AgentStateSnapshot) -> None:
        """No-op stub."""

    def list_teams(self) -> list[Process]:
        """Return empty list."""
        return []

    def get_max_sequence(self, team_id: uuid.UUID) -> int:
        """Return 0."""
        return 0

    def load_agent_states(self, team_id: uuid.UUID) -> list[AgentStateSnapshot]:
        """Return empty list."""
        return []


class TestTierServicesEventStoreProtocol:
    """AC6: TierServices accepts a MongoEventStore-shaped object via structural subtyping."""

    def test_tierservices_accepts_mongo_shaped_event_store(self) -> None:
        """TierServices construction succeeds with a fake EventStore implementation.

        The fake class does NOT inherit from EventStore -- it satisfies the
        protocol purely through structural subtyping, the same pattern used
        by MongoEventStore and YamlEventStore.
        """
        fake_store = FakeEventStore()

        services = TierServices(
            placement=MagicMock(spec=PlacementStrategy),
            worker_handle=MagicMock(spec=WorkerHandle),
            auth=MagicMock(spec=AuthStrategy),
            event_store=fake_store,
            runtime_cache=MagicMock(spec=RuntimeCache),
            event_stream=MagicMock(spec=EventStream),
            ingestion=MagicMock(spec=InteractionChannelIngestion),
            channel_registry=MagicMock(spec=ChannelRegistry),
            team_catalog=MagicMock(spec=TeamCatalog),
            agent_catalog=MagicMock(spec=AgentCatalog),
            tool_catalog=MagicMock(spec=ToolCatalog),
            template_catalog=MagicMock(spec=TemplateCatalog),
        )

        assert services.event_store is fake_store
