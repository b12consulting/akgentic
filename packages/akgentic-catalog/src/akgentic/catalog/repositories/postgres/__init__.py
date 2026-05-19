"""PostgreSQL-backed Catalog v2 repository package.

Structural peer of :mod:`akgentic.catalog.repositories.mongo`: one class,
one table, one implementation of
:class:`~akgentic.catalog.repositories.base.EntryRepository`. The
:class:`PostgresEntryRepository` persists entries to a single
``catalog_entries`` table keyed by a compound ``(namespace, id)`` primary
key; :func:`init_db` creates the table as a one-shot operation decoupled
from the repository constructor (initContainer / prestart pattern);
:class:`PostgresCatalogConfig` is the Pydantic configuration model.

**Lazy imports**: this package's top-level imports are
``nagra``-free and ``psycopg``-free. Consumers can
``import akgentic.catalog.repositories.postgres`` on an install without the
``[postgres]`` extra; the ``nagra`` / ``psycopg`` dependencies are only
pulled in when :class:`PostgresEntryRepository` or :func:`init_db` is
actually invoked.

Implements ADR-011 §"Package layout" — navigation-only reference.
"""

from __future__ import annotations

from .config import PostgresCatalogConfig
from .init_db import init_db
from .repository import PostgresEntryRepository

__all__ = [
    "PostgresCatalogConfig",
    "PostgresEntryRepository",
    "init_db",
]
