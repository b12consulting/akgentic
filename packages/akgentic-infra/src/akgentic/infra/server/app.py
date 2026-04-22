"""FastAPI application factory for the akgentic-infra server."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from akgentic.catalog.api import (
    add_exception_handlers,
    agent_router,
    team_router,
    template_router,
    tool_router,
)
from akgentic.catalog.api.agent_router import set_catalog as set_agent_catalog
from akgentic.catalog.api.team_router import set_catalog as set_team_catalog
from akgentic.catalog.api.template_router import set_catalog as set_template_catalog
from akgentic.catalog.api.tool_router import set_catalog as set_tool_catalog
from akgentic.infra.server.deps import TierServices
from akgentic.infra.server.logging_config import configure_logging
from akgentic.infra.server.routes.admin_catalog import router as admin_catalog_router
from akgentic.infra.server.routes.frontend_adapter import load_frontend_adapter
from akgentic.infra.server.routes.readiness import router as readiness_router
from akgentic.infra.server.routes.teams import router as teams_router
from akgentic.infra.server.routes.webhook import router as webhook_router
from akgentic.infra.server.routes.workspace import router as workspace_router
from akgentic.infra.server.routes.ws import ConnectionManager, shutdown_reader_pool
from akgentic.infra.server.routes.ws import router as ws_router
from akgentic.infra.server.services.team_service import TeamService
from akgentic.infra.server.settings import ServerSettings

logger = logging.getLogger(__name__)


def create_app(
    services: TierServices,
    settings: ServerSettings | None = None,
) -> FastAPI:
    """Create and configure the FastAPI application.

    Entry-point factory: constructs ``TeamService``, completes deferred
    ``LocalIngestion`` wiring, and mounts all routes.

    Args:
        services: Pre-wired tier services container.
        settings: Server settings. Defaults to ``ServerSettings()``.

    Returns:
        Configured FastAPI application instance.
    """
    settings = settings or ServerSettings()
    configure_logging(settings.log_level)
    logger.info("Logging configured: level=%s", settings.log_level)
    team_service = TeamService(services)
    _wire_ingestion(services, team_service)
    return _build_app(services, team_service, settings)


def _wire_ingestion(services: TierServices, team_service: TeamService) -> None:
    """Complete deferred LocalIngestion wiring with the constructed TeamService.

    Community tier needs this deferred wiring because LocalIngestion holds a
    direct in-process reference to TeamService, creating a circular construction
    dependency (wire_community -> CommunityServices -> LocalIngestion, but
    TeamService needs CommunityServices). Department/enterprise tiers don't
    need this — their ingestion adapters communicate over the network (HTTP or
    Dapr), so they arrive fully wired from their own wire_*() functions.
    """
    from akgentic.infra.adapters.community.local_ingestion import LocalIngestion

    if isinstance(services.ingestion, LocalIngestion):
        services.ingestion.team_service = team_service


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    """FastAPI lifespan handler implementing ADR-013 graceful shutdown sequence.

    Startup: sets ``app.state.draining = False``.
    Shutdown: sets draining flag, waits pre-drain delay, disconnects all
    WebSocket clients, then stops all teams via ``worker_handle.stop_all()``.
    """
    app.state.draining = False
    logger.info("Lifespan startup: draining=False")
    yield
    # --- Shutdown sequence (ADR-013 Decision 2) ---
    app.state.draining = True
    logger.info("Lifespan shutdown: draining=True")

    delay = app.state.settings.shutdown_pre_drain_delay
    if delay > 0:
        logger.info("Pre-drain delay: sleeping %ds", delay)
        await asyncio.sleep(delay)

    logger.info("Disconnecting all WebSocket clients")
    await app.state.connection_manager.disconnect_all()

    timeout = app.state.settings.shutdown_drain_timeout
    logger.info("Stopping all teams (timeout=%ds)", timeout)
    try:
        await asyncio.wait_for(
            asyncio.to_thread(app.state.services.worker_handle.stop_all),
            timeout=timeout,
        )
        logger.info("stop_all() completed successfully")
    except TimeoutError:
        logger.warning(
            "stop_all() exceeded shutdown_drain_timeout=%ds, proceeding with exit",
            timeout,
        )

    # Shut down the dedicated WS reader thread pool — see issue #227.
    shutdown_reader_pool()
    logger.info("WebSocket reader pool shut down")


def _build_app(
    services: TierServices,
    team_service: TeamService,
    settings: ServerSettings,
) -> FastAPI:
    """Assemble the FastAPI app from pre-built services (shared by create_app and tests).

    Args:
        services: Wired tier services container.
        team_service: Pre-built team service.
        settings: Server settings.

    Returns:
        Configured FastAPI application instance.
    """
    app = FastAPI(title="Akgentic Platform API", lifespan=_lifespan)
    _add_cors(app, settings.cors_origins)
    _store_state(app, services, team_service, settings)
    _inject_catalogs(services)
    _mount_routes(app, settings)
    add_exception_handlers(app)
    logger.info("Building app: routes mounted, CORS origins=%s", settings.cors_origins)
    return app


def _add_cors(app: FastAPI, cors_origins: list[str]) -> None:
    """Add CORS middleware to the application.

    When *cors_origins* is empty the middleware is **not** registered at all,
    allowing an external gateway (e.g. Azure App Service) to manage CORS.
    """
    if not cors_origins:
        return
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )


def _store_state(
    app: FastAPI,
    services: TierServices,
    team_service: TeamService,
    settings: ServerSettings,
) -> None:
    """Store services and configuration on app.state for dependency injection."""
    app.state.services = services
    app.state.team_service = team_service
    app.state.settings = settings
    app.state.connection_manager = ConnectionManager()
    app.state.channel_parser_registry = getattr(services, "channel_parser_registry", None)
    app.state.channel_registry = services.channel_registry
    app.state.ingestion = services.ingestion


def _inject_catalogs(services: TierServices) -> None:
    """Inject catalog service instances into akgentic-catalog router modules."""
    set_agent_catalog(services.agent_catalog)
    set_team_catalog(services.team_catalog)
    set_template_catalog(services.template_catalog)
    set_tool_catalog(services.tool_catalog)


def _mount_routes(app: FastAPI, settings: ServerSettings) -> None:
    """Mount all API routers and optional frontend adapter."""
    app.include_router(teams_router)
    app.include_router(agent_router, prefix="/catalog")
    app.include_router(team_router, prefix="/catalog")
    app.include_router(template_router, prefix="/catalog")
    app.include_router(tool_router, prefix="/catalog")
    app.include_router(workspace_router)
    app.include_router(ws_router)
    app.include_router(webhook_router)
    app.include_router(readiness_router)
    app.include_router(admin_catalog_router)

    if settings.frontend_adapter:
        adapter = load_frontend_adapter(settings.frontend_adapter)
        adapter.register_routes(app)
        app.state.frontend_adapter = adapter
        logger.debug("Frontend adapter loaded: %s", settings.frontend_adapter)
