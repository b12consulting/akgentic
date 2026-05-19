"""Public API surface for akgentic-catalog (v2).

Re-exports the unified :class:`Entry` model, its supporting types, the v2
query/clone models, v2-alive error types, the concrete :class:`Catalog`
service, the structural :class:`EntryRepository` protocol, the YAML entry
repository, the resolver surface (sentinel keys, ``populate_refs`` /
``resolve`` etc.), and the ``resolve_env_vars`` utility.

MongoDB-backed repository and the FastAPI application factory / routers are
conditionally re-exported when their optional dependencies are installed.
The CLI entry point is re-exported as ``cli_app`` when ``typer`` is available.
"""

from __future__ import annotations

from akgentic.catalog.catalog import UNSET_NAMESPACE, Catalog
from akgentic.catalog.env import resolve_env_vars
from akgentic.catalog.models.entry import Entry, EntryKind
from akgentic.catalog.models.errors import CatalogValidationError, EntryNotFoundError
from akgentic.catalog.models.namespace_meta import NamespaceMeta
from akgentic.catalog.models.queries import CloneRequest, EntryQuery
from akgentic.catalog.repositories.base import EntryRepository
from akgentic.catalog.repositories.yaml import YamlEntryRepository
from akgentic.catalog.resolver import (
    NAMESPACE_KEY,
    REF_KEY,
    TYPE_KEY,
    load_model_type,
    populate_refs,
    prepare_for_write,
    reconcile_refs,
    resolve,
    validate_delete,
)

__all__ = [
    "NAMESPACE_KEY",
    "REF_KEY",
    "TYPE_KEY",
    "Catalog",
    "CatalogValidationError",
    "CloneRequest",
    "Entry",
    "EntryKind",
    "EntryNotFoundError",
    "EntryQuery",
    "EntryRepository",
    "NamespaceMeta",
    "UNSET_NAMESPACE",
    "YamlEntryRepository",
    "load_model_type",
    "populate_refs",
    "prepare_for_write",
    "reconcile_refs",
    "resolve",
    "resolve_env_vars",
    "validate_delete",
]

try:
    from akgentic.catalog.repositories.mongo import MongoCatalogConfig, MongoEntryRepository

    __all__ += [
        "MongoCatalogConfig",
        "MongoEntryRepository",
    ]
except ImportError:
    pass

try:
    from akgentic.catalog.repositories.postgres import (
        PostgresCatalogConfig,
        PostgresEntryRepository,
        init_db,
    )

    __all__ += [
        "PostgresCatalogConfig",
        "PostgresEntryRepository",
        "init_db",
    ]
except ImportError:
    pass

try:
    from akgentic.catalog.api import (
        ErrorResponse,
        add_exception_handlers,
        create_app,
    )

    __all__ += [
        "ErrorResponse",
        "add_exception_handlers",
        "create_app",
    ]
except ImportError:
    pass

try:
    from akgentic.catalog.cli import app as cli_app

    __all__ += [
        "cli_app",
    ]
except ImportError:
    pass
