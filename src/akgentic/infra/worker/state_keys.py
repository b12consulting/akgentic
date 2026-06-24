"""Typed ``app.state`` key declarations for the worker tier (ADR-030 §Decision 2).

The worker process stores its :class:`~akgentic.infra.worker.deps.WorkerServices`
DI container under ``app.state.services``; ``SERVICES`` is the typed handle to
that slot. Tier-specific worker slots (department ``worker_identity`` /
``service_registry``, enterprise ``loop_watchdog``) are declared the same way in
their own packages — infra never sees them.
"""

from __future__ import annotations

from akgentic.infra.utils import StateKey
from akgentic.infra.worker.deps import WorkerServices

DRAINING: StateKey[bool] = StateKey("draining", default=False)
SERVICES: StateKey[WorkerServices] = StateKey("services", required=True)
