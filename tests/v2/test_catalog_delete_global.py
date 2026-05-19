"""Tests for Story 17.4 — ``Catalog.delete`` widens to a global-scope check.

When the deleted entry's namespace is shareable (its ``_meta`` carries
``payload["shareable"] is True``), ``Catalog.delete`` runs the existing
namespace-local ``validate_delete`` AND the new
``repository.find_references_global(...)`` walker; their referrer messages
are concatenated into a single ``CatalogValidationError``.

When the deleted entry's namespace is NOT shareable (no meta entry, or meta
entry with ``shareable is not True``), the global check short-circuits — no
extra repository call, no behaviour change vs. the pre-17.3 baseline.
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import BaseModel

from akgentic.catalog.catalog import Catalog
from akgentic.catalog.models.entry import Entry
from akgentic.catalog.models.errors import CatalogValidationError

from .conftest import (
    CountingEntryRepository,
    FakeEntryRepository,
    make_meta_entry,
    register_akgentic_test_module,
)

_TEAM_TYPE = "akgentic.team.models.TeamCard"


class _Leaf(BaseModel):
    """Permissive leaf used for cross-ns ref payloads."""

    provider: str = "openai"


class _Holder(BaseModel):
    """Holder payload for cross-ns ref tests."""

    model_cfg: _Leaf | None = None


def _team_payload() -> dict[str, Any]:
    return {
        "name": "team",
        "description": "",
        "entry_point": {
            "card": {
                "role": "entry",
                "description": "",
                "skills": [],
                "agent_class": "akgentic.core.agent.Akgent",
                "config": {"name": "entry", "role": "entry"},
            },
            "headcount": 1,
            "members": [],
        },
        "members": [],
        "agent_profiles": [],
    }


@pytest.fixture
def model_paths(monkeypatch: pytest.MonkeyPatch) -> tuple[str, str]:
    module_name = register_akgentic_test_module(
        monkeypatch,
        "tests_fixture_17_4_delete_global",
        Leaf=_Leaf,
        Holder=_Holder,
    )
    return f"{module_name}.Leaf", f"{module_name}.Holder"


def _seed_team(catalog: Catalog, namespace: str) -> None:
    catalog.create(
        Entry(
            id="team",
            kind="team",
            namespace=namespace,
            user_id="anonymous",
            model_type=_TEAM_TYPE,
            payload=_team_payload(),
        )
    )


def _mark_shareable(catalog: Catalog, namespace: str) -> None:
    """Seed a meta entry making ``namespace`` cross-namespace-referenceable."""
    catalog.create(make_meta_entry(namespace, shareable=True))


class TestGlobalScopeBlocksDelete:
    """Cross-tenant referrer to a shareable entry blocks the delete."""

    def test_shareable_ns_target_blocked_by_tenant_referrer(
        self, model_paths: tuple[str, str]
    ) -> None:
        leaf, holder = model_paths
        repo = FakeEntryRepository()
        catalog = Catalog(repo)
        _seed_team(catalog, "global")
        _mark_shareable(catalog, "global")
        catalog.create(
            Entry(
                id="shared-prompt",
                kind="prompt",
                namespace="global",
                user_id="anonymous",
                model_type=leaf,
                payload={"provider": "shared"},
            )
        )
        # tenant-A references global.shared-prompt
        _seed_team(catalog, "tenant-A")
        catalog.create(
            Entry(
                id="agent-1",
                kind="agent",
                namespace="tenant-A",
                user_id="anonymous",
                model_type=holder,
                payload={
                    "model_cfg": {
                        "__ref__": "shared-prompt",
                        "__namespace__": "global",
                    }
                },
            )
        )

        with pytest.raises(CatalogValidationError) as exc_info:
            catalog.delete("global", "shared-prompt")
        # The error's errors list contains a referrer message naming tenant-A.
        joined = " | ".join(exc_info.value.errors)
        assert "tenant-A" in joined
        assert "agent-1" in joined


class TestNonShareableNamespaceShortCircuits:
    """Deleting from a non-shareable namespace skips the global scan."""

    def test_no_meta_entry_skips_global_call(self, model_paths: tuple[str, str]) -> None:
        leaf, _holder = model_paths
        inner = FakeEntryRepository()
        counting = CountingEntryRepository(inner)
        # No meta entry on 'tenant-A' — namespace is not shareable.
        catalog = Catalog(counting)  # type: ignore[arg-type]
        _seed_team(catalog, "tenant-A")
        catalog.create(
            Entry(
                id="leaf-1",
                kind="prompt",
                namespace="tenant-A",
                user_id="anonymous",
                model_type=leaf,
                payload={"provider": "x"},
            )
        )
        counting.reset()
        catalog.delete("tenant-A", "leaf-1")
        assert counting.count("find_references_global") == 0

    def test_shareable_false_skips_global_call(self, model_paths: tuple[str, str]) -> None:
        leaf, _holder = model_paths
        inner = FakeEntryRepository()
        counting = CountingEntryRepository(inner)
        catalog = Catalog(counting)  # type: ignore[arg-type]
        _seed_team(catalog, "tenant-A")
        # Meta entry with shareable=False ⇒ not shareable.
        catalog.create(make_meta_entry("tenant-A", shareable=False))
        catalog.create(
            Entry(
                id="leaf-1",
                kind="prompt",
                namespace="tenant-A",
                user_id="anonymous",
                model_type=leaf,
                payload={"provider": "x"},
            )
        )
        counting.reset()
        catalog.delete("tenant-A", "leaf-1")
        assert counting.count("find_references_global") == 0


class TestNoMetaEntryByteIdentical:
    """A namespace without a meta entry behaves like the pre-17.3 baseline."""

    def test_no_meta_no_global_call(self, model_paths: tuple[str, str]) -> None:
        leaf, _holder = model_paths
        inner = FakeEntryRepository()
        counting = CountingEntryRepository(inner)
        catalog = Catalog(counting)  # type: ignore[arg-type]
        _seed_team(catalog, "global")
        # No meta entry for global — namespace is not shareable by default.
        catalog.create(
            Entry(
                id="leaf-1",
                kind="prompt",
                namespace="global",
                user_id="anonymous",
                model_type=leaf,
                payload={"provider": "x"},
            )
        )
        counting.reset()
        catalog.delete("global", "leaf-1")
        assert counting.count("find_references_global") == 0
