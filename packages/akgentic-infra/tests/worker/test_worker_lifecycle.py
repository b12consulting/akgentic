"""Tests for WorkerLifecycle service."""

from __future__ import annotations

import logging
import uuid
from unittest.mock import MagicMock, patch

import pytest
from akgentic.team.ports import NullServiceRegistry, ServiceRegistry

from akgentic.infra.protocols.worker_handle import WorkerHandle
from akgentic.infra.worker.services.lifecycle import WorkerLifecycle


@pytest.fixture()
def instance_id() -> uuid.UUID:
    return uuid.uuid4()


@pytest.fixture()
def mock_worker_handle() -> MagicMock:
    return MagicMock(spec=WorkerHandle)


@pytest.fixture()
def mock_service_registry() -> MagicMock:
    return MagicMock(spec=ServiceRegistry)


@pytest.fixture()
def lifecycle(
    mock_worker_handle: MagicMock,
    mock_service_registry: MagicMock,
    instance_id: uuid.UUID,
) -> WorkerLifecycle:
    return WorkerLifecycle(
        worker_handle=mock_worker_handle,
        service_registry=mock_service_registry,
        instance_id=instance_id,
    )


class TestStartup:
    """WorkerLifecycle.startup() tests (AC #2)."""

    @pytest.mark.asyncio
    async def test_startup_registers_instance(
        self,
        lifecycle: WorkerLifecycle,
        mock_service_registry: MagicMock,
        instance_id: uuid.UUID,
    ) -> None:
        await lifecycle.startup()
        mock_service_registry.register_instance.assert_called_once_with(instance_id)


class TestShutdown:
    """WorkerLifecycle.shutdown() tests (AC #3, #6)."""

    @pytest.mark.asyncio
    async def test_shutdown_deregisters_and_stops(
        self,
        lifecycle: WorkerLifecycle,
        mock_service_registry: MagicMock,
        mock_worker_handle: MagicMock,
        instance_id: uuid.UUID,
    ) -> None:
        await lifecycle.shutdown(drain_timeout=30)
        mock_service_registry.deregister_instance.assert_called_once_with(instance_id)
        mock_worker_handle.stop_all.assert_called_once()

    @pytest.mark.asyncio
    async def test_shutdown_deregisters_before_stop_all(
        self,
        lifecycle: WorkerLifecycle,
        mock_service_registry: MagicMock,
        mock_worker_handle: MagicMock,
    ) -> None:
        """Deregister must happen before stop_all (no new teams routed while stopping)."""
        call_order: list[str] = []
        mock_service_registry.deregister_instance.side_effect = (
            lambda _: call_order.append("deregister")
        )
        mock_worker_handle.stop_all.side_effect = lambda: call_order.append("stop_all")

        await lifecycle.shutdown(drain_timeout=30)
        assert call_order == ["deregister", "stop_all"]

    @pytest.mark.asyncio
    async def test_shutdown_timeout_logged_not_raised(
        self,
        lifecycle: WorkerLifecycle,
        mock_worker_handle: MagicMock,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """TimeoutError from stop_all() is caught and logged, not raised."""
        with patch(
            "akgentic.infra.worker.services.lifecycle.asyncio.wait_for",
            side_effect=TimeoutError,
        ):
            with caplog.at_level(logging.WARNING):
                await lifecycle.shutdown(drain_timeout=1)

        assert any("exceeded drain_timeout" in rec.message for rec in caplog.records)


    @pytest.mark.asyncio
    async def test_shutdown_stop_all_runs_even_if_deregister_fails(
        self,
        mock_worker_handle: MagicMock,
        mock_service_registry: MagicMock,
        instance_id: uuid.UUID,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """stop_all() must execute even if deregister_instance() raises."""
        mock_service_registry.deregister_instance.side_effect = RuntimeError("registry down")
        lc = WorkerLifecycle(
            worker_handle=mock_worker_handle,
            service_registry=mock_service_registry,
            instance_id=instance_id,
        )
        with caplog.at_level(logging.ERROR):
            await lc.shutdown(drain_timeout=30)

        mock_worker_handle.stop_all.assert_called_once()
        assert any("deregister_instance failed" in rec.message for rec in caplog.records)


class TestNullServiceRegistry:
    """WorkerLifecycle with NullServiceRegistry (AC #4)."""

    @pytest.mark.asyncio
    async def test_works_with_null_service_registry(
        self,
        mock_worker_handle: MagicMock,
    ) -> None:
        """Community tier path — NullServiceRegistry no-ops, no errors."""
        null_registry = NullServiceRegistry()
        lc = WorkerLifecycle(
            worker_handle=mock_worker_handle,
            service_registry=null_registry,
            instance_id=uuid.uuid4(),
        )
        await lc.startup()
        await lc.shutdown(drain_timeout=5)
        mock_worker_handle.stop_all.assert_called_once()


class TestLifespanDelegation:
    """Lifespan handler delegates to WorkerLifecycle (AC #5)."""

    @pytest.mark.asyncio
    async def test_lifespan_delegates_to_lifecycle(self) -> None:
        """Create a worker app, invoke _lifespan directly, verify lifecycle methods called."""
        from unittest.mock import AsyncMock

        from akgentic.core import ActorSystem
        from akgentic.team.manager import TeamManager
        from akgentic.team.ports import EventStore

        from akgentic.infra.protocols.runtime_cache import RuntimeCache
        from akgentic.infra.worker.app import _lifespan, create_worker_app
        from akgentic.infra.worker.deps import WorkerServices
        from akgentic.infra.worker.settings import WorkerSettings

        services = WorkerServices(
            team_manager=MagicMock(spec=TeamManager),
            actor_system=MagicMock(spec=ActorSystem),
            event_store=MagicMock(spec=EventStore),
            service_registry=NullServiceRegistry(),
            runtime_cache=MagicMock(spec=RuntimeCache),
            worker_handle=MagicMock(spec=WorkerHandle),
        )
        settings = WorkerSettings(shutdown_drain_timeout=1, shutdown_pre_drain_delay=0)
        app = create_worker_app(services, settings)

        # Replace lifecycle with a mock to verify delegation
        mock_lifecycle = MagicMock()
        mock_lifecycle.startup = AsyncMock()
        mock_lifecycle.shutdown = AsyncMock()
        app.state.lifecycle = mock_lifecycle

        async with _lifespan(app):
            assert app.state.draining is False
            mock_lifecycle.startup.assert_called_once()

        mock_lifecycle.shutdown.assert_called_once_with(drain_timeout=1)
