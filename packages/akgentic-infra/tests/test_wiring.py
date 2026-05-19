"""Tests for wire_community() assembly function."""

from __future__ import annotations

import logging
from collections.abc import Generator
from pathlib import Path

import pytest
from akgentic.team.manager import TeamManager
from akgentic.team.ports import NullServiceRegistry, ServiceRegistry
from akgentic.team.repositories.yaml import YamlEventStore

from akgentic.infra.adapters.community.local_event_stream import LocalEventStream
from akgentic.infra.adapters.community.local_placement import LocalPlacement
from akgentic.infra.adapters.community.local_worker_handle import LocalWorkerHandle
from akgentic.infra.adapters.community.no_auth import NoAuth
from akgentic.infra.adapters.community.null_channel_registry import NullChannelRegistry
from akgentic.infra.adapters.community.yaml_channel_registry import YamlChannelRegistry
from akgentic.infra.adapters.shared.event_stream_subscriber import EventStreamSubscriber
from akgentic.infra.server.deps import CommunityServices
from akgentic.infra.server.settings import CommunitySettings
from akgentic.infra.wiring import wire_community


class TestWireCommunityLogging:
    """wire_community() emits expected log messages."""

    def test_emits_wiring_info_log(
        self,
        tmp_path: Path,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """wire_community() emits 'Wiring community services' INFO log."""
        settings = CommunitySettings(
            workspaces_root=tmp_path / "workspaces",
            event_store_path=tmp_path / "event_store",
            catalog_path=tmp_path / "catalog",
        )
        with caplog.at_level(logging.INFO, logger="akgentic.infra.wiring"):
            services = wire_community(settings)
        try:
            assert any("Wiring community services" in r.message for r in caplog.records)
        finally:
            services.team_manager._actor_system.shutdown(timeout=5)


class TestWireCommunity:
    """AC6: wire_community() assembles CommunityServices correctly."""

    @pytest.fixture()
    def services(self, tmp_path: Path) -> Generator[CommunityServices, None, None]:
        """Wire community services with a temp workspace root and cleanup ActorSystem."""
        settings = CommunitySettings(
            workspaces_root=tmp_path / "workspaces",
            event_store_path=tmp_path / "event_store",
            catalog_path=tmp_path / "catalog",
        )
        svc = wire_community(settings)
        yield svc
        svc.team_manager._actor_system.shutdown(timeout=5)

    def test_returns_community_services(self, services: CommunityServices) -> None:
        """wire_community returns a CommunityServices instance."""
        assert isinstance(services, CommunityServices)

    def test_placement_is_local(self, services: CommunityServices) -> None:
        """Placement strategy is a LocalPlacement."""
        assert isinstance(services.placement, LocalPlacement)

    def test_auth_is_noauth(self, services: CommunityServices) -> None:
        """Auth strategy is NoAuth."""
        assert isinstance(services.auth, NoAuth)

    def test_worker_handle_is_local(self, services: CommunityServices) -> None:
        """Worker handle is LocalWorkerHandle."""
        assert isinstance(services.worker_handle, LocalWorkerHandle)

    def test_service_registry_is_null(self, services: CommunityServices) -> None:
        """Service registry is NullServiceRegistry (write-only, community-tier)."""
        assert isinstance(services.service_registry, NullServiceRegistry)

    def test_event_store_is_yaml(self, services: CommunityServices) -> None:
        """Event store is YamlEventStore."""
        assert isinstance(services.event_store, YamlEventStore)

    def test_team_manager_is_present(self, services: CommunityServices) -> None:
        """TeamManager is present and correctly typed."""
        assert isinstance(services.team_manager, TeamManager)

    def test_uses_settings_event_store_path(self, tmp_path: Path) -> None:
        """wire_community passes settings.event_store_path to YamlEventStore."""
        settings = CommunitySettings(
            workspaces_root=tmp_path / "workspaces",
            event_store_path=tmp_path / "event_store",
            catalog_path=tmp_path / "catalog",
        )
        services = wire_community(settings)
        try:
            assert isinstance(services.event_store, YamlEventStore)
            assert services.event_store._data_dir == settings.event_store_path
        finally:
            services.team_manager._actor_system.shutdown(timeout=5)

    def test_shared_service_registry(self, services: CommunityServices) -> None:
        """TeamManager and CommunityServices share the same service registry."""
        assert services.service_registry is services.team_manager._service_registry

    def test_service_registry_satisfies_protocol(
        self,
        services: CommunityServices,
    ) -> None:
        """Service registry satisfies the ServiceRegistry protocol."""
        assert isinstance(services.service_registry, ServiceRegistry)

    def test_catalog_path_override(self, tmp_path: Path) -> None:
        """wire_community uses settings.catalog_path when set."""
        custom_catalog = tmp_path / "custom-catalog"
        custom_catalog.mkdir()
        settings = CommunitySettings(
            workspaces_root=tmp_path / "workspaces",
            event_store_path=tmp_path / "event_store",
            catalog_path=custom_catalog,
        )
        services = wire_community(settings)
        try:
            assert services.catalog is not None
            assert services.catalog._repository._root == custom_catalog
        finally:
            services.team_manager._actor_system.shutdown(timeout=5)

    def test_default_channel_registry_is_null(
        self,
        services: CommunityServices,
    ) -> None:
        """When channel_registry_path is None (default), uses NullChannelRegistry."""
        assert isinstance(services.channel_registry, NullChannelRegistry)

    def test_channel_registry_path_uses_yaml(self, tmp_path: Path) -> None:
        """When channel_registry_path is set, uses YamlChannelRegistry."""
        reg_path = tmp_path / "registry.yaml"
        settings = CommunitySettings(
            workspaces_root=tmp_path / "workspaces",
            event_store_path=tmp_path / "event_store",
            catalog_path=tmp_path / "catalog",
            channel_registry_path=reg_path,
        )
        services = wire_community(settings)
        try:
            assert isinstance(services.channel_registry, YamlChannelRegistry)
        finally:
            services.team_manager._actor_system.shutdown(timeout=5)


class TestWireCommunityEventStream:
    """AC4: EventStreamSubscriber is wired as shared subscriber with LocalEventStream."""

    @pytest.fixture()
    def services(self, tmp_path: Path) -> Generator[CommunityServices, None, None]:
        settings = CommunitySettings(
            workspaces_root=tmp_path / "workspaces",
            event_store_path=tmp_path / "event_store",
            catalog_path=tmp_path / "catalog",
        )
        svc = wire_community(settings)
        yield svc
        svc.team_manager._actor_system.shutdown(timeout=5)

    def test_event_stream_is_local(self, services: CommunityServices) -> None:
        """AC4: CommunityServices.event_stream is a LocalEventStream."""
        assert isinstance(services.event_stream, LocalEventStream)

    def test_event_stream_subscriber_in_shared_subscribers(
        self, services: CommunityServices
    ) -> None:
        """AC4: EventStreamSubscriber is present in TeamManager shared_subscribers."""
        subscribers = services.team_manager._shared_subscribers
        has_event_stream_sub = any(
            isinstance(s, EventStreamSubscriber) for s in subscribers
        )
        assert has_event_stream_sub
