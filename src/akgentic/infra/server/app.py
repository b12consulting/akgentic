"""FastAPI application factory for the akgentic-infra server.

Per ADR-023: the ``/admin/catalog/*`` HTTP surface IS the v2 unified router
(``akgentic.catalog.api.router.build_router``) with ``AuthStrategy`` wired
as a FastAPI dependency. The v1-shaped ``admin_catalog`` router is gone; the
catalog package itself owns request/response validation, error mapping, and
CRUD semantics. Infra owns only the mount point, the auth gate, and the
structured mutation log middleware.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import APIRouter, Depends, FastAPI
from fastapi.dependencies.utils import get_parameterless_sub_dependant
from fastapi.middleware.cors import CORSMiddleware
from fastapi.params import Depends as DependsParam
from fastapi.routing import APIRoute

from akgentic.catalog.api import add_exception_handlers
from akgentic.catalog.api._settings import CatalogRouterSettings
from akgentic.catalog.api.router import build_router as build_catalog_router
from akgentic.catalog.api.router import set_catalog as set_unified_catalog
from akgentic.infra.server.deps import TierServices
from akgentic.infra.server.logging_config import configure_logging
from akgentic.infra.server.routes._admin_mutation_log import AdminCatalogMutationLogMiddleware
from akgentic.infra.server.routes._auth_dep import require_authenticated_principal
from akgentic.infra.server.routes._catalog_authz import (
    require_import_owner_or_admin,
    require_namespace_owner_or_admin,
)
from akgentic.infra.server.routes._catalog_caller_identity import scope_catalog_caller_identity
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
    # ``workspaces_root`` is declared on ``CommunitySettings``; base
    # ``ServerSettings`` callers fall back to the same default the field
    # declares so ``TeamService`` always has a valid FS-cleanup root.
    workspaces_root = getattr(settings, "workspaces_root", Path("workspaces"))
    team_service = TeamService(services, workspaces_root=workspaces_root)
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
    # Inject the v2 catalog into the unified router's module-level slot before
    # any request arrives — see ADR-023 §D1 and akgentic-catalog ADR-09.
    set_unified_catalog(services.catalog)
    _mount_routes(app, settings)
    # Mutation-log middleware must be added AFTER route mounting so that its
    # dispatch wraps every admin-catalog response (see ADR-023 §D5).
    # BaseHTTPMiddleware subclasses don't structurally match FastAPI's
    # _MiddlewareFactory protocol (constructor returns None, not the app);
    # the runtime contract is identical — the type ignore is purely
    # Starlette's protocol vs. a concrete class mismatch.
    app.add_middleware(AdminCatalogMutationLogMiddleware)  # type: ignore[arg-type]
    add_exception_handlers(app)
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
    logger.info("Building app: CORSMiddleware - cors origins %s", cors_origins)


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


def _mount_routes(app: FastAPI, settings: ServerSettings) -> None:
    """Mount all API routers and optional frontend adapter.

    Route mount order (ADR-023 §D2): readiness, teams, workspace, ws, webhook,
    admin-catalog, then optional frontend adapter. Admin-catalog is mounted
    last among API routers so its ``/admin`` prefix cannot accidentally
    shadow any other route.
    """
    app.include_router(readiness_router)
    app.include_router(teams_router)
    app.include_router(workspace_router)
    app.include_router(ws_router)
    app.include_router(webhook_router)

    # v2 unified catalog router, exposed under /admin/catalog/* with the
    # generic kind-CRUD family enabled and gated by the wired AuthStrategy.
    admin_catalog_router = build_catalog_router(
        CatalogRouterSettings(expose_generic_kind_crud=True),
    )
    # ADR-028: additionally gate the modify + delete routes with the
    # resource-level owner-or-admin dependency, per-route (NOT a blanket
    # include_router dependency — that would fire on reads and break the
    # body-carried create routes). The router-level authentication gate below
    # stays exactly as-is; this gate is additive.
    _attach_owner_or_admin_gate(admin_catalog_router)
    # ADR-028 §Decision 8: the import route's target namespace is body-carried,
    # so the path/query route gate above cannot see it. Attach a separate
    # body-reading gate to exactly POST /catalog/namespace/import — the one
    # mutating route the route gate deliberately does NOT body-peek. Kept as a
    # sibling helper (not folded into _OWNER_OR_ADMIN_GATED_ROUTES) so the
    # no-body-peek route gate and the body-peek import gate stay visibly
    # distinct.
    _attach_import_owner_or_admin_gate(admin_catalog_router)
    # ADR-028 §Decision 7 (infra side): scope each /admin/catalog/* request
    # inside Catalog.as_caller(request_user.user_id), derived server-side from
    # the ADR-023 get_request_user seam. Attached once here so department
    # (calls create_app) and enterprise (transplants these routes/state)
    # inherit it from the same place. The router-level authentication gate
    # below stays exactly as-is; this dependency is additive and never reads a
    # spoofable inbound header.
    app.include_router(
        admin_catalog_router,
        prefix="/admin",
        dependencies=[
            Depends(require_authenticated_principal),
            Depends(scope_catalog_caller_identity),
        ],
    )
    logger.info("Building app: routes mounted")

    if settings.frontend_adapter:
        adapter = load_frontend_adapter(settings.frontend_adapter)
        adapter.register_routes(app)
        app.state.frontend_adapter = adapter
        logger.debug("Building app: Frontend adapter loaded: %s", settings.frontend_adapter)


# Catalog-router-relative paths of the four owner-or-admin-gated mutation
# routes. Paths are the router-local form (the router's own ``/catalog``
# prefix), each paired with the HTTP method that mutates.
# ``DELETE /catalog/namespace/{namespace}`` is the namespace-delete route
# introduced by the catalog package's namespace-delete capability. The
# attachment is forward-compatible: it gates the route only when present on
# the built router, and skips silently (no hard-fail) if a catalog version is
# pinned that does not yet expose it. See issue #297 for the cross-package
# sequencing.
_OWNER_OR_ADMIN_GATED_ROUTES: tuple[tuple[str, str], ...] = (
    ("PUT", "/catalog/{kind}/{id}"),
    ("DELETE", "/catalog/{kind}/{id}"),
    ("PUT", "/catalog/namespace/{namespace}/meta"),
    ("DELETE", "/catalog/namespace/{namespace}"),
)


def _attach_owner_or_admin_gate(router: APIRouter) -> None:
    """Append the owner-or-admin dependency to the four gated mutation routes.

    Shape (1) from the story / ADR-028: augment each matching ``APIRoute`` on
    the already-built catalog router in place, by method + router-local path.
    The dependency is added to both ``route.dependencies`` (so it is part of
    the route *definition* and travels with the route when enterprise
    transplants it) and the live ``route.dependant`` (so it fires at request
    time). This is additive — the router-level authentication dependency is
    untouched, reads/creates are not gated, and the dependency is per-route.

    Routes absent from the currently-pinned catalog (notably the
    namespace-delete route from akgentic-catalog Story 27.1) are simply not
    found and silently skipped — the attachment is forward-compatible.
    """
    dep: DependsParam = Depends(require_namespace_owner_or_admin)
    for method, path in _OWNER_OR_ADMIN_GATED_ROUTES:
        for route in router.routes:
            if not isinstance(route, APIRoute):
                continue
            if route.path == path and method in route.methods:
                route.dependencies.append(dep)
                route.dependant.dependencies.insert(
                    0,
                    get_parameterless_sub_dependant(depends=dep, path=route.path_format),
                )


# Router-local (method, path) of the single body-carried mutation route gated
# by the import-specific owner-or-admin dependency. Kept separate from
# ``_OWNER_OR_ADMIN_GATED_ROUTES`` because that constant drives the no-body-peek
# route gate; this route's namespace lives in the YAML body and needs the
# body-reading ``require_import_owner_or_admin`` instead (ADR-028 §Decision 8).
_IMPORT_OWNER_OR_ADMIN_GATED_ROUTE: tuple[str, str] = ("POST", "/catalog/namespace/import")


def _attach_import_owner_or_admin_gate(router: APIRouter) -> None:
    """Append the body-reading import gate to ``POST /catalog/namespace/import``.

    Mirrors ``_attach_owner_or_admin_gate``'s in-place two-line augmentation
    (append to ``route.dependencies`` AND insert into ``route.dependant``) but
    for the single import route and with ``require_import_owner_or_admin`` — the
    dependency that reads the YAML body to find the target namespace, then
    applies the same owner-or-admin predicate. Adding it to
    ``route.dependencies`` makes it part of the route *definition* so it travels
    with the ``APIRoute`` when enterprise transplants it (ADR-028 §Decision 8).

    This is additive and per-route: the router-level authentication and
    caller-identity dependencies are untouched, the four path/query routes keep
    ``require_namespace_owner_or_admin`` (which still does not body-peek), and no
    other route is gated by the import dependency.
    """
    dep: DependsParam = Depends(require_import_owner_or_admin)
    method, path = _IMPORT_OWNER_OR_ADMIN_GATED_ROUTE
    for route in router.routes:
        if not isinstance(route, APIRoute):
            continue
        if route.path == path and method in route.methods:
            route.dependencies.append(dep)
            route.dependant.dependencies.insert(
                0,
                get_parameterless_sub_dependant(depends=dep, path=route.path_format),
            )
