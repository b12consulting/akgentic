"""Worker module — typed configuration, DI container, and shared routers for akgentic-infra workers.

After Epic 28 the dead worker FastAPI shell (``create_worker_app``,
``WorkerLifecycle``, the ``_lifespan`` factory) is gone. The surviving public
surface is exactly: settings, the DI container, and the two shared routers that
every tier mounts on its own FastAPI worker.
"""

from akgentic.infra.worker.deps import WorkerServices
from akgentic.infra.worker.routes.readiness import router as readiness_router
from akgentic.infra.worker.routes.teams import router as teams_router
from akgentic.infra.worker.settings import WorkerSettings

__all__ = ["WorkerServices", "WorkerSettings", "readiness_router", "teams_router"]
