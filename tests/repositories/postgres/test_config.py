"""AC4, AC5: ``PostgresCatalogConfig`` Pydantic contract + scheme validation.

Pure Pydantic tests — no database, no testcontainer. Verify the field set,
defaults, scheme-validator behaviour, and round-trip through ``model_dump``
/ ``model_validate``.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from akgentic.catalog.repositories.postgres import PostgresCatalogConfig


def test_accepts_postgresql_scheme() -> None:
    """AC5: ``postgresql://`` scheme validates successfully."""
    config = PostgresCatalogConfig(connection_string="postgresql://user:pw@host:5432/db")
    assert config.connection_string == "postgresql://user:pw@host:5432/db"


def test_accepts_postgres_scheme() -> None:
    """AC5: ``postgres://`` scheme validates successfully."""
    config = PostgresCatalogConfig(connection_string="postgres://localhost/catalog")
    assert config.connection_string == "postgres://localhost/catalog"


def test_defaults_schema_name_and_table() -> None:
    """AC4: defaults for ``schema_name`` and ``table`` are pinned."""
    config = PostgresCatalogConfig(connection_string="postgresql://localhost/db")
    assert config.schema_name == "public"
    assert config.table == "catalog_entries"


def test_custom_schema_and_table() -> None:
    """AC4: ``schema_name`` and ``table`` accept string overrides."""
    config = PostgresCatalogConfig(
        connection_string="postgresql://localhost/db",
        schema_name="catalogs",
        table="my_entries",
    )
    assert config.schema_name == "catalogs"
    assert config.table == "my_entries"


@pytest.mark.parametrize(
    "bad_dsn",
    [
        "",
        "mysql://user@host/db",
        "sqlite:///catalog.db",
        "http://localhost/db",
        "redis://localhost",
        "host-only-no-scheme",
    ],
)
def test_rejects_non_postgres_schemes(bad_dsn: str) -> None:
    """AC5: invalid schemes raise ``ValueError`` with the offending prefix echoed."""
    with pytest.raises(ValidationError) as excinfo:
        PostgresCatalogConfig(connection_string=bad_dsn)
    # The Pydantic error wraps the underlying ValueError; the message should
    # include the offending input so logs are diagnosable.
    errors = excinfo.value.errors()
    assert len(errors) == 1
    assert "postgresql://" in errors[0]["msg"]
    assert "postgres://" in errors[0]["msg"]


def test_model_round_trips_through_dump_and_validate() -> None:
    """AC4: ``model_dump`` / ``model_validate`` round-trip is identity."""
    config = PostgresCatalogConfig(
        connection_string="postgresql://localhost/db",
        schema_name="custom",
        table="entries_v2",
    )
    dumped = config.model_dump()
    assert dumped == {
        "connection_string": "postgresql://localhost/db",
        "schema_name": "custom",
        "table": "entries_v2",
    }
    restored = PostgresCatalogConfig.model_validate(dumped)
    assert restored == config
