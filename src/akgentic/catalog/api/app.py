"""FastAPI application factory for the Akgentic catalog API.

Provides ``create_app()`` which assembles a FastAPI application serving
the unified ``/catalog`` router over a YAML-, MongoDB-, or Postgres-backed
:class:`EntryRepository`.

Implements ADR-011 §"Wiring surface" — Postgres is the third first-class
backend alongside YAML and MongoDB. Navigation-only reference.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Literal

from fastapi import FastAPI, Request, Response
from starlette.middleware.base import RequestResponseEndpoint

from akgentic.catalog.api._errors import add_exception_handlers
from akgentic.catalog.api._settings import CatalogRouterSettings
from akgentic.catalog.api.router import build_router, set_catalog
from akgentic.catalog.catalog import Catalog

if TYPE_CHECKING:
    from pathlib import Path

    from akgentic.catalog.repositories.base import EntryRepository
    from akgentic.catalog.repositories.mongo import MongoCatalogConfig
    from akgentic.catalog.repositories.postgres import PostgresCatalogConfig

__all__ = ["create_app"]

logger = logging.getLogger(__name__)


def create_app(
    *,
    backend: Literal["yaml", "mongodb", "postgres"] = "yaml",
    yaml_base_path: Path | None = None,
    mongo_config: MongoCatalogConfig | None = None,
    postgres_config: PostgresCatalogConfig | None = None,
    router_settings: CatalogRouterSettings | None = None,
    caller_user_id_header: str | None = None,
) -> FastAPI:
    """Create a FastAPI app serving the unified ``/catalog`` router.

    Args:
        backend: ``"yaml"`` for filesystem-backed storage, ``"mongodb"`` for
            MongoDB-backed storage, or ``"postgres"`` for PostgreSQL-backed
            storage.
        yaml_base_path: Root directory for YAML entries. Defaults to
            ``Path("./catalog")`` when ``backend="yaml"`` and this argument is
            ``None``. Created if absent.
        mongo_config: MongoDB connection + naming configuration. Required when
            ``backend="mongodb"``.
        postgres_config: PostgreSQL connection configuration. Required when
            ``backend="postgres"``. The DSN is consumed by
            :class:`PostgresEntryRepository`; schema creation is a separate
            deployment concern (``python -m akgentic.catalog.scripts.init_db``).
        router_settings: Router configuration controlling whether the
            generic ``/catalog/{kind}`` CRUD family is registered
            (Story 16.7). Defaults to
            :meth:`CatalogRouterSettings.from_env` — reads
            ``AKGENTIC_CATALOG_EXPOSE_GENERIC_KIND_CRUD``.
        caller_user_id_header: Optional HTTP header name carrying the
            authenticated caller's ``user_id``. When ``None`` (the default
            — community tier), no middleware is registered and every
            request runs with ``_caller_user_id == None`` (no visibility
            filtering — today's behaviour). When set to a header name
            (e.g. ``"X-User-Id"``), the app registers a single FastAPI
            middleware that reads the header on every request, opens a
            ``Catalog.as_caller(value)`` context manager spanning the
            request's downstream call chain, and tears it down on
            response. When the header is missing or its value is empty,
            the middleware leaves the contextvar at its ``None`` default
            (rejection is upstream-tier policy, out of scope here).
            Whitespace-only values are passed through verbatim — they
            satisfy ``Catalog.as_caller``'s non-empty contract (Python
            truthiness) and act as a real caller identity that owns no
            entries; the upstream tier MUST sanitise inputs.

    Returns:
        A configured ``FastAPI`` app with the catalog router mounted and
        catalog exception handlers registered.

    Raises:
        ValueError: If the backend identifier is unknown or required arguments
            are missing.

    Cross-namespace sharing is data-driven (ADR-008 §D2 as updated
    2026-05-08 rev 2): a namespace declares itself shareable through its
    own ``_meta`` entry's ``payload["shareable"] is True`` flag (typed bool
    at the root, strict-bool comparison). Operators provision shareable
    namespaces via bundle import; no app-factory wiring is required.
    """
    repo = _build_repository(
        backend=backend,
        yaml_base_path=yaml_base_path,
        mongo_config=mongo_config,
        postgres_config=postgres_config,
    )
    catalog = Catalog(repository=repo)
    set_catalog(catalog)

    app = FastAPI(title="Akgentic Catalog")
    app.include_router(build_router(router_settings))
    add_exception_handlers(app)

    if caller_user_id_header is not None:
        _register_caller_identity_middleware(app, caller_user_id_header)

    logger.info("Created Akgentic Catalog API with %s backend", backend)
    return app


def _register_caller_identity_middleware(app: FastAPI, header_name: str) -> None:
    """Register the per-request ``Catalog.as_caller`` middleware (ADR-009 §D2).

    Reads ``header_name`` on every request; when present and non-empty,
    wraps the downstream call chain in ``Catalog.as_caller(value)`` so
    visibility filtering inside ``Catalog.list / get / clone`` sees the
    caller's identity. Missing / empty headers leave the contextvar at
    its ``None`` default (community-tier passthrough). Whitespace-only
    values are passed through verbatim and act as a non-empty caller
    identity — the upstream tier MUST sanitise inputs.
    """

    @app.middleware("http")
    async def _caller_identity_middleware(
        request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        raw_value = request.headers.get(header_name)
        if not raw_value:
            return await call_next(request)
        with Catalog.as_caller(raw_value):
            return await call_next(request)


def _build_repository(
    *,
    backend: Literal["yaml", "mongodb", "postgres"],
    yaml_base_path: Path | None,
    mongo_config: MongoCatalogConfig | None,
    postgres_config: PostgresCatalogConfig | None,
) -> EntryRepository:
    """Construct the concrete ``EntryRepository`` for ``create_app``."""
    if backend == "yaml":
        from pathlib import Path as _Path

        from akgentic.catalog.repositories.yaml import YamlEntryRepository

        base = yaml_base_path if yaml_base_path is not None else _Path("./catalog")
        base.mkdir(parents=True, exist_ok=True)
        return YamlEntryRepository(base)
    if backend == "mongodb":
        if mongo_config is None:
            msg = "mongo_config is required when backend='mongodb'"
            raise ValueError(msg)
        from akgentic.catalog.repositories.mongo import MongoEntryRepository

        client = mongo_config.create_client()
        collection = mongo_config.get_collection(client, mongo_config.catalog_entries_collection)
        return MongoEntryRepository(collection)
    if backend == "postgres":
        if postgres_config is None:
            msg = "postgres_config is required when backend='postgres'"
            raise ValueError(msg)
        # Lazy import — kept out of the module top-level so the [postgres]
        # extra is only required when the Postgres branch is actually taken.
        from akgentic.catalog.repositories.postgres import PostgresEntryRepository

        return PostgresEntryRepository(postgres_config.connection_string)
    msg = f"Unknown backend: {backend!r}. Must be 'yaml', 'mongodb', or 'postgres'."
    raise ValueError(msg)
