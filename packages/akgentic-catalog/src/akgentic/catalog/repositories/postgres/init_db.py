"""Idempotent schema bootstrap for the Catalog v2 Postgres backend.

:func:`init_db` creates the ``catalog_entries`` table, the compound unique
index on ``(namespace, id)`` (which Nagra emits for the ``natural_key``
declared in ``schema.toml``), and the secondary index on
``(namespace, kind)``. The helper is **decoupled from the repository
constructor**: :class:`PostgresEntryRepository` never calls :func:`init_db`
— callers invoke it explicitly as a one-shot operation (init-container /
prestart pattern).

The function is idempotent: Nagra's ``Schema.create_tables`` is a no-op
against an existing table (it introspects ``information_schema`` and only
emits ``CREATE TABLE`` for tables it does not find), and the secondary
index uses ``CREATE INDEX IF NOT EXISTS``. Running :func:`init_db` twice
against the same database completes successfully without any destructive
effect.

``nagra`` is imported **inside the function body** — the module's top-level
imports stay nagra-free so ``import akgentic.catalog.repositories.postgres``
succeeds on an install without the ``[postgres]`` extra (ADR-011 lazy-import
discipline; tested by the no-extra import guard).

Implements ADR-011 §"init_db helper — schema creation lives here" —
navigation-only reference.
"""

from __future__ import annotations

import logging
from importlib.resources import files

from .config import PostgresCatalogConfig

__all__ = ["init_db"]

logger = logging.getLogger(__name__)


_SECONDARY_INDEX_STATEMENTS: tuple[str, ...] = (
    'CREATE INDEX IF NOT EXISTS "catalog_entries_namespace_kind_idx" '
    'ON "catalog_entries" ("namespace", "kind")',
)


def init_db(config: PostgresCatalogConfig) -> None:
    """Create or reconcile the ``catalog_entries`` schema against ``config``.

    Steps, in order, inside a single Nagra ``Transaction``:

    1. Load ``schema.toml`` via ``Schema.from_toml`` (in-process; no DDL).
    2. Call ``schema.create_tables(trn)`` — emits ``CREATE TABLE`` for
       ``catalog_entries`` and the natural-key ``UNIQUE INDEX`` on
       ``(namespace, id)`` if they do not already exist.
    3. Emit ``CREATE INDEX IF NOT EXISTS`` for the secondary index
       ``(namespace, kind)``.

    The transaction commits on successful exit and rolls back on any
    exception — the caller (Story 22.2's init-container entrypoint)
    distinguishes exit codes by inspecting the raised error class.

    Args:
        config: Validated :class:`PostgresCatalogConfig`. Only
            ``config.connection_string`` is used here; ``schema_name`` and
            ``table`` are reserved for Story 22.2's repository-wiring layer
            and do not affect the emitted DDL in this story (the schema
            references the table by its bare name ``catalog_entries``,
            resolved through ``search_path``).

    Raises:
        ImportError: when the ``[postgres]`` extra is not installed.
        Any ``psycopg`` / ``nagra`` exception: propagated unchanged so the
            caller's exit-code policy is free to distinguish error classes.
    """
    # Lazy import — kept out of module top-level so install without the
    # `[postgres]` extra remains import-clean. nagra has no `py.typed`
    # marker, so the resulting names surface as Any at the call sites;
    # the `# type: ignore[import-untyped]` tags that as expected.
    from nagra import Schema, Transaction  # type: ignore[import-untyped]

    schema_toml = files(__package__).joinpath("schema.toml").read_text(encoding="utf-8")
    schema = Schema.from_toml(schema_toml)

    logger.info("Applying catalog_entries schema to Postgres")
    with Transaction(config.connection_string) as trn:
        schema.create_tables(trn=trn)
        for statement in _SECONDARY_INDEX_STATEMENTS:
            trn.execute(statement)
    logger.info("catalog_entries schema applied")
