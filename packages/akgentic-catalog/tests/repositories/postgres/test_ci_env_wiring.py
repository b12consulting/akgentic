"""CI environment-variable wiring tests for the Nagra/Postgres backend.

Two tests live here:

* ``test_db_conn_string_persistence_reachable``: the CI positive-path test.
  When ``DB_CONN_STRING_PERSISTENCE`` is set in the environment (the GHA
  ``quality`` job exposes it at the job level via the service container
  credentials), open a :class:`nagra.Transaction` against it and run a
  trivial ``SELECT 1`` round-trip. Skips cleanly when the env var is NOT
  set so local runs without CI env continue to pass.
* ``test_repo_constructor_does_not_read_env``: a regression guard. Repo
  constructors must take ``conn_string`` as a plain string argument —
  env-var reading happens at the wiring layer (app startup / infra), not
  in the repo constructor. Monkeypatches ``os.environ`` to ``{}`` and
  confirms construction still succeeds and stores the provided string.

Module-level ``pytest.importorskip`` ensures the module is skipped
cleanly on runners without the ``[postgres]`` extra installed — mirrors
the conftest's gating so the new test is safe in every environment.
"""

from __future__ import annotations

import os

import pytest

pytest.importorskip("nagra")
pytest.importorskip("testcontainers.postgres")


def test_db_conn_string_persistence_reachable() -> None:
    """Positive-path CI test: connect via DB_CONN_STRING_PERSISTENCE and run SELECT 1.

    This is the single behavioural invariant for Story 15.4 AC #6 — that
    the env-var plumbing set up at the CI job level (sourced from the
    postgres:16-alpine service container credentials) points at a live,
    reachable database. Skips when the env var is absent so local runs
    without CI env continue to pass.
    """
    conn_string = os.environ.get("DB_CONN_STRING_PERSISTENCE")
    if not conn_string:
        pytest.skip("DB_CONN_STRING_PERSISTENCE not set — CI-only test")

    from nagra import Transaction

    with Transaction(conn_string) as trn:
        cursor = trn.execute("SELECT 1")
        row = cursor.fetchone()

    assert row is not None
    assert row[0] == 1


def test_repo_constructor_does_not_read_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """AC #13 regression guard: repo constructors must not read os.environ.

    Monkeypatch ``os.environ`` to an empty mapping and construct a
    :class:`NagraTemplateCatalogRepository` with a syntactically valid but
    unreachable connection string. Construction must succeed (``__init__``
    only stores the string and calls ``_ensure_schema_loaded``) and the
    stored conn string must equal the constructor argument verbatim.
    """
    from akgentic.catalog.repositories.postgres.template_repo import (
        NagraTemplateCatalogRepository,
    )

    monkeypatch.setattr(os, "environ", {})

    conn_string = "postgresql://nonexistent:0/nope"
    repo = NagraTemplateCatalogRepository(conn_string)

    assert repo._conn_string == conn_string
