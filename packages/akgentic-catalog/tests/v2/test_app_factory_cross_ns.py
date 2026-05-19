"""Story 17.4 — assert the removed allowlist API surfaces are gone.

Story 17.3 shipped ``create_app(cross_namespace_refs_allowed=...)`` and
``Catalog(repo, cross_namespace_refs_allowed=...)``. Story 17.4 deletes
both per Epic 17 Addendum (2026-05-08) — the data-driven shareable-flag
mechanism replaces them. These tests pin the deletion: passing the
removed kwarg raises ``TypeError`` ("unexpected keyword argument").
"""

from __future__ import annotations

from pathlib import Path

import pytest


def test_create_app_rejects_removed_cross_ns_kwarg(tmp_path: Path) -> None:
    pytest.importorskip("fastapi")
    from akgentic.catalog.api.app import create_app

    with pytest.raises(TypeError):
        create_app(  # type: ignore[call-arg]
            backend="yaml",
            yaml_base_path=tmp_path,
            cross_namespace_refs_allowed=frozenset({"global"}),
        )


class TestCatalogCtorRejectsRemovedKwarg:
    """Story 17.4 / AC4 — ``Catalog.__init__`` no longer accepts the kwarg."""

    def test_keyword_argument_rejected(self) -> None:
        from akgentic.catalog.catalog import Catalog

        from .conftest import FakeEntryRepository

        with pytest.raises(TypeError):
            Catalog(  # type: ignore[call-arg]
                FakeEntryRepository(),
                cross_namespace_refs_allowed=frozenset({"global"}),
            )

    def test_constructor_takes_only_repository(self) -> None:
        """Sanity: the public signature is now ``__init__(self, repository)``."""
        from akgentic.catalog.catalog import Catalog

        from .conftest import FakeEntryRepository

        catalog = Catalog(FakeEntryRepository())
        # The internal cache attribute is still per-instance state.
        assert catalog._shareable_flag_cache == {}
