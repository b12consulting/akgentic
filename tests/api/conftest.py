"""Shared fixtures for ``tests/api/`` (Epic 21).

Mirrors the ``api_client`` fixture from ``tests/v2/conftest.py``. Defined here
(rather than promoted to the package-level ``tests/conftest.py``) to keep the
v2 test layout unchanged; Epic 21 only needs the fixture inside ``tests/api/``.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

from akgentic.catalog.catalog import Catalog
from akgentic.catalog.repositories.yaml import YamlEntryRepository

if TYPE_CHECKING:
    pass


@pytest.fixture
def api_client(tmp_path: Path) -> tuple[Any, Catalog]:
    """Yield a ``(TestClient, Catalog)`` pair wired to a YAML-backed v2 router.

    Function-scoped — ``set_catalog`` is called fresh per test so the module-
    level ``_catalog`` in ``api/router.py`` cannot leak between tests.
    ``fastapi`` is guarded via ``importorskip`` inside the fixture body so
    this conftest module stays importable when the ``api`` extra is absent.
    Opts **in** to the kind-generic CRUD surface (Story 16.7).
    """
    pytest.importorskip("fastapi")
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from akgentic.catalog.api._errors import add_exception_handlers
    from akgentic.catalog.api._settings import CatalogRouterSettings
    from akgentic.catalog.api.router import build_router, set_catalog

    repo = YamlEntryRepository(tmp_path)
    catalog = Catalog(repo)

    app = FastAPI(title="Akgentic Catalog")
    app.include_router(build_router(CatalogRouterSettings(expose_generic_kind_crud=True)))
    set_catalog(catalog)
    add_exception_handlers(app)

    return TestClient(app), catalog
