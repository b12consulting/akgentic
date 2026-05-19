"""Import-guard regression for ``create_app(backend="postgres")`` without ``[postgres]``.

AC28: when the ``[postgres]`` extra is absent (``nagra`` unimportable), the
API factory's lazy import path inside ``_build_repository`` surfaces a
clean ``ImportError``. The API surface does NOT wrap it in a friendlier
message — that translation is the CLI's job (see
``cli/main.py::_build_catalog``'s Postgres branch + its ``try/except`` on
the import). Programmatic API callers get the raw ``ImportError`` so they
can branch on it directly.

The test simulates the no-extra environment by monkeypatching
``sys.modules["nagra"]`` to ``None`` — importing ``nagra`` then raises
``ImportError`` immediately. This runs unconditionally; it does NOT need
Docker or the ``[postgres]`` extra to be installed.
"""

from __future__ import annotations

import sys

import pytest


def test_create_app_postgres_without_extra_raises_cleanly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC28: a missing ``nagra`` module → ``ImportError`` from the lazy branch."""
    pytest.importorskip("fastapi")

    from akgentic.catalog.api.app import create_app
    from akgentic.catalog.repositories.postgres import PostgresCatalogConfig

    # Drop cached modules so the lazy import inside _build_repository
    # actually re-executes the import statement and hits our sentinel.
    for mod in list(sys.modules):
        if mod == "nagra" or mod.startswith("nagra."):
            monkeypatch.delitem(sys.modules, mod, raising=False)
    # Sentinel: importing "nagra" now raises ImportError.
    monkeypatch.setitem(sys.modules, "nagra", None)

    cfg = PostgresCatalogConfig(
        connection_string="postgresql://postgres:postgres@localhost:5432/ignored"
    )

    with pytest.raises(ImportError):
        create_app(backend="postgres", postgres_config=cfg)
