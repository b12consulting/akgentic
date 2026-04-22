"""Worker lifecycle management — startup registration and graceful shutdown."""

from __future__ import annotations

import asyncio
import logging
import uuid

from akgentic.infra.protocols.worker_handle import WorkerHandle
from akgentic.team.ports import ServiceRegistry

logger = logging.getLogger(__name__)


class WorkerLifecycle:
    """Manages worker startup registration and graceful shutdown.

    Startup registers the worker instance with the ``ServiceRegistry``.
    Shutdown deregisters the instance, then stops all running teams via
    ``WorkerHandle.stop_all()`` with a configurable drain timeout.

    Enterprise tiers extend this class for Dapr-specific registration
    and TTL-based heartbeats. Community tier uses ``NullServiceRegistry``
    (all methods are no-ops).
    """

    def __init__(
        self,
        worker_handle: WorkerHandle,
        service_registry: ServiceRegistry,
        instance_id: uuid.UUID,
    ) -> None:
        self._worker_handle = worker_handle
        self._service_registry = service_registry
        self._instance_id = instance_id

    async def startup(self) -> None:
        """Register this worker instance with the service registry."""
        logger.info("WorkerLifecycle startup: registering instance %s", self._instance_id)
        await asyncio.to_thread(self._service_registry.register_instance, self._instance_id)
        logger.info("WorkerLifecycle startup: registered instance %s", self._instance_id)

    async def shutdown(self, drain_timeout: int) -> None:
        """Deregister this worker instance and stop all running teams.

        Args:
            drain_timeout: Maximum seconds to wait for ``stop_all()`` to complete.
        """
        logger.info("WorkerLifecycle shutdown: deregistering instance %s", self._instance_id)
        try:
            await asyncio.to_thread(self._service_registry.deregister_instance, self._instance_id)
            logger.info("WorkerLifecycle shutdown: deregistered instance %s", self._instance_id)
        except Exception:
            logger.exception(
                "WorkerLifecycle shutdown: deregister_instance failed for %s, "
                "proceeding with stop_all",
                self._instance_id,
            )

        logger.info("WorkerLifecycle shutdown: stopping all teams (timeout=%ds)", drain_timeout)
        try:
            await asyncio.wait_for(
                asyncio.to_thread(self._worker_handle.stop_all),
                timeout=drain_timeout,
            )
            logger.info("WorkerLifecycle shutdown: stop_all() completed successfully")
        except TimeoutError:
            logger.warning(
                "WorkerLifecycle shutdown: stop_all() exceeded drain_timeout=%ds, "
                "proceeding with exit",
                drain_timeout,
            )
