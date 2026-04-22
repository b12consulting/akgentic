"""CI env-wiring smoke tests for the Nagra-backed Postgres event store.

These tests validate that the V1 PostgreSQL environment-variable wiring
(notably ``DB_CONN_STRING_PERSISTENCE``) is plumbed correctly from the GHA
service container declared in ``.github/workflows/ci.yml`` through to the
:class:`~akgentic.team.repositories.postgres.NagraEventStore` constructor
and the :func:`~akgentic.team.repositories.postgres.init_db` deployment hook.

Two of the three tests skip cleanly when ``DB_CONN_STRING_PERSISTENCE``
is unset (typical local ``pytest`` invocation): the env-wiring tests have
nothing to talk to. The constructor regression guard runs unconditionally
because it explicitly clears ``os.environ`` to verify ``__init__`` does
not silently read from it.

Module-level ``pytest.importorskip`` calls match the conventions of the
sibling ``test_event_store.py`` module so the file skips cleanly when the
``[postgres]`` extra is missing.
"""

from __future__ import annotations

import os

import pytest

pytest.importorskip("nagra")
pytest.importorskip("testcontainers.postgres")

from akgentic.team.repositories.postgres import (  # noqa: E402
    NagraEventStore,
    init_db,
)

_ENV_VAR = "DB_CONN_STRING_PERSISTENCE"


def test_db_conn_string_persistence_reachable() -> None:
    """The CI env wiring yields a ``conn_string`` usable by NagraEventStore.

    Skipped when the env var is unset (local runs). When CI sets the var
    against the GHA service container, this proves:

    1. The connection string parses and connects.
    2. ``init_db`` succeeds against a fresh DB.
    3. ``list_teams`` round-trips through SQL → hydrated ``Process`` list.
    """
    conn_string = os.environ.get(_ENV_VAR)
    if not conn_string:
        pytest.skip(f"{_ENV_VAR} not set — CI-only test")

    init_db(conn_string)
    store = NagraEventStore(conn_string)
    assert isinstance(store.list_teams(), list)


def test_init_db_is_idempotent_against_ci_env() -> None:
    """``init_db`` is safe to invoke twice against the same database.

    Mirrors the deployment shape — Kubernetes initContainers and Nomad
    prestart tasks may rerun on every redeploy. This test uses the CI
    env-wired Postgres because that is the operator-facing path the
    deployment hook ultimately runs against.
    """
    conn_string = os.environ.get(_ENV_VAR)
    if not conn_string:
        pytest.skip(f"{_ENV_VAR} not set — CI-only test")

    init_db(conn_string)
    init_db(conn_string)


def test_event_store_constructor_does_not_read_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression guard: ``NagraEventStore.__init__`` must take ``conn_string`` directly.

    Env-var reading is a wiring-layer concern; the event store itself must
    remain decoupled. Constructing with an empty environment and a bogus
    conn string must succeed (no connection is opened in ``__init__``) and
    the supplied string must be stored verbatim.
    """
    monkeypatch.setattr(os, "environ", {})

    store = NagraEventStore("postgresql://nonexistent:0/nope")

    assert store._conn_string == "postgresql://nonexistent:0/nope"
