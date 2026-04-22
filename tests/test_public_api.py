"""Smoke tests for the top-level ``akgentic.catalog`` public API surface.

Verifies that:

1. Importing ``akgentic.catalog`` succeeds regardless of whether optional
   backend extras are installed (the ``[postgres]`` and Mongo extras are
   gated by ``try/except ImportError`` blocks).
2. When the ``[postgres]`` extra IS installed, the four Nagra repositories
   and ``init_db`` are re-exported from the top-level package.
3. When ``pymongo`` is available, the existing Mongo re-exports remain
   untouched — a regression guard against Story 15.5 accidentally altering
   the Mongo block.

The module imports cleanly without any optional extra — Nagra / Mongo test
bodies use function-level ``pytest.importorskip`` so the file itself stays
importable on minimal environments.
"""

from __future__ import annotations

import pytest


def test_top_level_package_imports_without_postgres_extra() -> None:
    """Importing ``akgentic.catalog`` must never raise, even without extras."""
    import akgentic.catalog

    # Sanity: the unconditional exports are always present.
    assert hasattr(akgentic.catalog, "TemplateEntry")
    assert hasattr(akgentic.catalog, "YamlTemplateCatalogRepository")


def test_nagra_repositories_reexported_when_extra_installed() -> None:
    """When ``nagra`` is available, all five Nagra names re-export correctly."""
    pytest.importorskip("nagra")

    from akgentic.catalog import (
        NagraAgentCatalogRepository,
        NagraTeamCatalogRepository,
        NagraTemplateCatalogRepository,
        NagraToolCatalogRepository,
        init_db,
    )
    from akgentic.catalog.repositories import postgres as pg_pkg

    assert NagraAgentCatalogRepository is pg_pkg.NagraAgentCatalogRepository
    assert NagraTeamCatalogRepository is pg_pkg.NagraTeamCatalogRepository
    assert NagraTemplateCatalogRepository is pg_pkg.NagraTemplateCatalogRepository
    assert NagraToolCatalogRepository is pg_pkg.NagraToolCatalogRepository
    assert init_db is pg_pkg.init_db


def test_mongo_reexports_still_present() -> None:
    """Regression guard: the Mongo re-export block is untouched."""
    pytest.importorskip("pymongo")

    from akgentic.catalog import (
        MongoAgentCatalogRepository,
        MongoCatalogConfig,
        MongoTeamCatalogRepository,
        MongoTemplateCatalogRepository,
        MongoToolCatalogRepository,
    )

    # Types should resolve to actual classes, not None / sentinels.
    assert MongoAgentCatalogRepository is not None
    assert MongoCatalogConfig is not None
    assert MongoTeamCatalogRepository is not None
    assert MongoTemplateCatalogRepository is not None
    assert MongoToolCatalogRepository is not None
