"""HealthMonitor protocol — monitors worker instance liveness and detects expired workers."""

from __future__ import annotations

import uuid
from typing import Protocol


class HealthMonitor(Protocol):
    """Monitors worker instance liveness and detects expired workers.

    Heartbeat model:
        Workers push heartbeats at a configured interval (e.g. every 5 s).
        ``check_health()`` is called periodically by a background scheduler
        (department: Redis key-expiry watcher; enterprise: Dapr timer).
        A worker is "expired" when its heartbeat key has not been refreshed
        within the configured TTL. The TTL is set by the ``HealthMonitor``
        implementation, not the caller.

    Implementations:

    - **Department** (``RedisHealthMonitor``): Redis key TTL per worker.
    - **Enterprise** (``DaprHealthMonitor``): Dapr actor reminders.
    - **Community**: no health monitoring (single process, no workers).
    """

    def check_health(self) -> list[uuid.UUID]:
        """Check all registered workers and return expired instance IDs.

        Called by the background health-check scheduler. Returns worker IDs
        whose heartbeat has exceeded the configured TTL.

        Returns:
            List of worker instance IDs that have expired their heartbeat.
        """
        ...
