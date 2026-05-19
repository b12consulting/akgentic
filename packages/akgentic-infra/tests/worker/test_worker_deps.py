"""Tests for WorkerServices DI container."""

from __future__ import annotations

from unittest.mock import MagicMock

from akgentic.core import ActorSystem
from akgentic.team.manager import TeamManager
from akgentic.team.ports import EventStore, NullServiceRegistry, ServiceRegistry

from akgentic.infra.protocols.runtime_cache import RuntimeCache
from akgentic.infra.protocols.worker_handle import WorkerHandle
from akgentic.infra.worker.deps import WorkerServices


class TestWorkerServicesConstruction:
    """WorkerServices must accept community-tier concrete types (AC #2)."""

    def test_worker_services_accepts_community_adapters(self) -> None:
        """Construct WorkerServices with the same types used by wire_community()."""
        team_manager = MagicMock(spec=TeamManager)
        actor_system = MagicMock(spec=ActorSystem)
        event_store = MagicMock(spec=EventStore)
        service_registry = NullServiceRegistry()
        runtime_cache = MagicMock(spec=RuntimeCache)
        worker_handle = MagicMock(spec=WorkerHandle)

        services = WorkerServices(
            team_manager=team_manager,
            actor_system=actor_system,
            event_store=event_store,
            service_registry=service_registry,
            runtime_cache=runtime_cache,
            worker_handle=worker_handle,
        )

        assert services.team_manager is team_manager
        assert services.actor_system is actor_system
        assert services.event_store is event_store
        assert services.service_registry is service_registry
        assert services.runtime_cache is runtime_cache
        assert services.worker_handle is worker_handle


class TestWorkerServicesModel:
    """WorkerServices model structure and metadata (AC #1)."""

    def test_has_required_fields(self) -> None:
        expected_fields = {
            "team_manager",
            "actor_system",
            "event_store",
            "service_registry",
            "runtime_cache",
            "worker_handle",
        }
        assert set(WorkerServices.model_fields.keys()) == expected_fields

    def test_field_descriptions_present(self) -> None:
        for name, field_info in WorkerServices.model_fields.items():
            assert field_info.description is not None, f"Field {name} missing description"

    def test_arbitrary_types_allowed(self) -> None:
        assert WorkerServices.model_config.get("arbitrary_types_allowed") is True

    def test_service_registry_is_runtime_checkable(self) -> None:
        registry = NullServiceRegistry()
        assert isinstance(registry, ServiceRegistry)
