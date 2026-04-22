"""RecoveryPolicy protocol — determines recovery behavior when a worker instance fails."""

from __future__ import annotations

import uuid
from typing import Protocol


class RecoveryPolicy(Protocol):
    """Determines recovery behavior when a worker instance fails.

    Called by the health-check scheduler after ``HealthMonitor.check_health()``
    returns expired worker IDs. The scheduler resolves orphaned team IDs via
    ``ServiceRegistry.find_team()`` and passes them to ``recover()``.

    Recovery semantics vary by implementation:

    - **Department** (``MarkStoppedRecovery``): marks each orphaned team as
      ``STOPPED`` in the event store. Manual restore required.
    - **Enterprise** (``AutoRestoreRecovery``): re-places orphaned teams on
      a healthy worker automatically.
    - **Enterprise** (``NotifyOnlyRecovery``): sends an alert but takes no
      automatic action.
    - **Community**: no recovery (single process, no workers).

    Idempotency:
        ``recover()`` must be idempotent — calling it twice with the same
        ``instance_id`` and ``team_ids`` must not corrupt state or duplicate
        side effects.
    """

    def recover(self, instance_id: uuid.UUID, team_ids: list[uuid.UUID]) -> None:
        """Execute recovery for teams orphaned by a failed worker.

        Args:
            instance_id: The failed worker instance ID.
            team_ids: Teams that were hosted on the failed worker.
        """
        ...
