"""Pydantic configuration model for the Catalog v2 Postgres backend.

:class:`PostgresCatalogConfig` holds the connection string, schema name, and
the unified ``catalog_entries`` table name. It is the Postgres analogue of
:class:`akgentic.catalog.repositories.mongo.MongoCatalogConfig` — a pure
configuration surface, **not** a connection manager. Callers pass the
validated ``connection_string`` directly to :class:`PostgresEntryRepository`
and :func:`init_db`; the repository owns the ``nagra.Transaction`` lifecycle.

The ``connection_string`` field rejects schemes other than ``postgresql://``
and ``postgres://`` at validation time — the config surface refuses
``mysql://``, ``sqlite:///…``, ``http://…``, and the empty string up-front so
downstream consumers never see a malformed DSN (parity with
``MongoCatalogConfig`` which rejects non-``mongodb(+srv)://`` schemes).

Implements ADR-011 §"PostgresCatalogConfig" — navigation-only reference.
"""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator

__all__ = ["PostgresCatalogConfig"]

_VALID_SCHEMES: tuple[str, ...] = ("postgresql://", "postgres://")


class PostgresCatalogConfig(BaseModel):
    """Validated connection + table-naming configuration for Postgres.

    Three fields:

    * ``connection_string`` — libpq-style DSN (``postgresql://…``). Validated
      at construction time to reject non-Postgres schemes.
    * ``schema_name`` — Postgres schema the ``catalog_entries`` table lives
      in. Defaults to ``"public"``. Held for parity with Nagra's
      ``pg_schema`` parameter; the repository's CRUD SQL does not
      schema-qualify the table — Postgres resolves the bare name via
      ``search_path``, which defaults to ``public``.
    * ``table`` — physical table name. Defaults to ``"catalog_entries"``;
      pinned so deployments with a single catalog-entries table match the
      Mongo backend's single-collection shape.

    The model is a plain Pydantic ``BaseModel`` — no
    ``ConfigDict(arbitrary_types_allowed=True)``, no ``dict[str, Any]``
    fields — so it round-trips through ``model_dump()`` /
    ``model_validate()`` cleanly (Golden Rule #1b carries even for non-
    ``ToolCard`` config models: keep the serialisation surface honest).
    """

    connection_string: str = Field(
        description="libpq-style Postgres DSN (postgresql:// or postgres:// scheme).",
    )
    schema_name: str = Field(
        default="public",
        description="Postgres schema containing the catalog_entries table.",
    )
    table: str = Field(
        default="catalog_entries",
        description="Physical table name for unified v2 catalog entries.",
    )

    @field_validator("connection_string")
    @classmethod
    def _validate_connection_string(cls, v: str) -> str:
        """Reject DSNs that do not start with a Postgres scheme.

        Accepts ``postgresql://…`` or ``postgres://…``. Every other prefix —
        including the empty string, ``mysql://``, ``sqlite:///…``,
        ``http://…`` — raises ``ValueError`` with a message echoing the
        offending prefix so callers can diagnose the misconfiguration.
        """
        if not v.startswith(_VALID_SCHEMES):
            # Echo the invalid input verbatim so logs are self-explanatory.
            raise ValueError(
                f"connection_string must start with 'postgresql://' or 'postgres://'; got: {v!r}"
            )
        return v
