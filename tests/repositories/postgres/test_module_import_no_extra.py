"""AC28: ``akgentic.catalog.repositories.postgres`` imports without ``[postgres]``.

The Postgres package must stay importable on an install that does NOT ship
``nagra`` or ``psycopg``. The test verifies this by the (equivalent) path
of asserting ``sys.modules`` is free of ``nagra*`` / ``psycopg*`` entries
immediately after importing the package — those prefixes would only appear
if the module had imported them at load time.

The test uses ``importlib.reload`` after scrubbing ``sys.modules`` so it
exercises a fresh import even in a session that already pulled the package
in (e.g. via pytest's earlier test runs).
"""

from __future__ import annotations

import importlib
import sys


def test_postgres_package_imports_without_nagra_or_psycopg_side_effect() -> None:
    """AC28: importing the package does not pull in nagra or psycopg.

    Scrub ``sys.modules`` of any ``nagra*`` / ``psycopg*`` / postgres-
    subpackage entries, reload the package, and verify the two prefix
    namespaces are not touched by the import. This keeps the YAML-only
    install path import-clean even when nagra / psycopg are not pinned.
    """
    # Snapshot and scrub any existing entries so the reload starts clean.
    prefixes_to_scrub = ("nagra", "psycopg", "akgentic.catalog.repositories.postgres")
    removed = [
        name
        for name in list(sys.modules)
        if any(name == prefix or name.startswith(prefix + ".") for prefix in prefixes_to_scrub)
    ]
    for name in removed:
        del sys.modules[name]

    # Fresh import — must succeed.
    importlib.import_module("akgentic.catalog.repositories.postgres")

    # Neither `nagra` nor `psycopg` should be in sys.modules as a side
    # effect of importing the package.
    nagra_loaded = [name for name in sys.modules if name == "nagra" or name.startswith("nagra.")]
    psycopg_loaded = [
        name for name in sys.modules if name == "psycopg" or name.startswith("psycopg.")
    ]
    assert nagra_loaded == [], (
        f"Importing akgentic.catalog.repositories.postgres loaded nagra modules: {nagra_loaded}"
    )
    assert psycopg_loaded == [], (
        f"Importing akgentic.catalog.repositories.postgres loaded psycopg modules: {psycopg_loaded}"
    )
