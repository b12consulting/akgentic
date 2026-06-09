"""Tests for Story 27.1 — ``Catalog.delete_namespace``.

``Catalog.delete_namespace(namespace)`` removes every entry in a namespace
(including ``_meta``) in one atomic call. Its only guard is the structural
inbound-reference check (ADR-028 §Decision 5): for a shareable namespace, an
entry in *another* namespace referencing any entry being deleted blocks the
whole operation (``CatalogValidationError``); intra-namespace references never
block. An empty/absent namespace raises ``EntryNotFoundError``.

Behaviour is parametrised across the YAML and Mongo backends via
``catalog_factory``.
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import BaseModel

from akgentic.catalog.catalog import Catalog
from akgentic.catalog.models.entry import Entry
from akgentic.catalog.models.errors import CatalogValidationError, EntryNotFoundError

from .conftest import (
    CatalogFactory,
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
        "tests_fixture_27_1_delete_namespace",
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


def _seed_leaf(catalog: Catalog, namespace: str, id: str, leaf_type: str) -> None:
    catalog.create(
        Entry(
            id=id,
            kind="prompt",
            namespace=namespace,
            user_id="anonymous",
            model_type=leaf_type,
            payload={"provider": id},
        )
    )


class TestHappyPath:
    """A namespace with a team + entries + ``_meta`` is fully deleted."""

    def test_full_namespace_deleted(
        self, catalog_factory: CatalogFactory, model_paths: tuple[str, str]
    ) -> None:
        leaf, _holder = model_paths
        catalog, repo = catalog_factory()
        _seed_team(catalog, "ns-del")
        catalog.create(make_meta_entry("ns-del", shareable=False))
        _seed_leaf(catalog, "ns-del", "p-1", leaf)
        _seed_leaf(catalog, "ns-del", "p-2", leaf)

        catalog.delete_namespace("ns-del")

        assert repo.list_by_namespace("ns-del") == []
        with pytest.raises(EntryNotFoundError):
            catalog.get("ns-del", "p-1")
        with pytest.raises(EntryNotFoundError):
            catalog.get("ns-del", "team")
        with pytest.raises(EntryNotFoundError):
            catalog.get("ns-del", "_meta")


class TestInboundRefGuardNonShareable:
    """A non-shareable namespace with intra-namespace refs deletes cleanly."""

    def test_internal_refs_do_not_block(
        self, catalog_factory: CatalogFactory, model_paths: tuple[str, str]
    ) -> None:
        leaf, holder = model_paths
        catalog, repo = catalog_factory()
        _seed_team(catalog, "ns-internal")
        _seed_leaf(catalog, "ns-internal", "shared", leaf)
        # agent in the SAME namespace references the prompt (intra-ns ref).
        catalog.create(
            Entry(
                id="agent-1",
                kind="agent",
                namespace="ns-internal",
                user_id="anonymous",
                model_type=holder,
                payload={"model_cfg": {"__ref__": "shared"}},
            )
        )

        catalog.delete_namespace("ns-internal")

        assert repo.list_by_namespace("ns-internal") == []


class TestInboundRefGuardShareableExternalReferrer:
    """A shareable namespace with an external referrer is blocked + intact."""

    def test_external_referrer_blocks_and_leaves_intact(
        self, catalog_factory: CatalogFactory, model_paths: tuple[str, str]
    ) -> None:
        leaf, holder = model_paths
        catalog, repo = catalog_factory()
        _seed_team(catalog, "global")
        catalog.create(make_meta_entry("global", shareable=True))
        _seed_leaf(catalog, "global", "shared-prompt", leaf)
        # tenant-A (different namespace) references global.shared-prompt.
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

        before = {(e.namespace, e.id) for e in repo.list_by_namespace("global")}
        with pytest.raises(CatalogValidationError) as exc_info:
            catalog.delete_namespace("global")
        joined = " | ".join(exc_info.value.errors)
        assert "tenant-A" in joined
        assert "agent-1" in joined
        # Namespace left intact — every original entry still present.
        after = {(e.namespace, e.id) for e in repo.list_by_namespace("global")}
        assert after == before


class TestAtomicity:
    """A refused delete leaves the entry count unchanged."""

    def test_blocked_delete_preserves_count(
        self, catalog_factory: CatalogFactory, model_paths: tuple[str, str]
    ) -> None:
        leaf, holder = model_paths
        catalog, repo = catalog_factory()
        _seed_team(catalog, "global")
        catalog.create(make_meta_entry("global", shareable=True))
        _seed_leaf(catalog, "global", "shared-prompt", leaf)
        _seed_leaf(catalog, "global", "other", leaf)
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

        count_before = len(repo.list_by_namespace("global"))
        with pytest.raises(CatalogValidationError):
            catalog.delete_namespace("global")
        count_after = len(repo.list_by_namespace("global"))
        assert count_after == count_before


class TestMissingOrEmptyNamespace:
    """Deleting a namespace with no entries raises ``EntryNotFoundError``."""

    def test_absent_namespace_raises_not_found(self, catalog_factory: CatalogFactory) -> None:
        catalog, _repo = catalog_factory()
        with pytest.raises(EntryNotFoundError):
            catalog.delete_namespace("does-not-exist")


class TestCacheInvalidation:
    """Deleting a shareable/public namespace re-derives flags to ``False``."""

    def test_flags_re_derive_false_after_delete(
        self, catalog_factory: CatalogFactory, model_paths: tuple[str, str]
    ) -> None:
        leaf, _holder = model_paths
        catalog, _repo = catalog_factory()
        _seed_team(catalog, "ns-flags")
        catalog.create(make_meta_entry("ns-flags", shareable=True, public=True))
        _seed_leaf(catalog, "ns-flags", "p-1", leaf)
        # Warm the caches so a stale True would surface if not invalidated.
        assert catalog._is_namespace_shareable("ns-flags") is True
        assert catalog._is_namespace_public("ns-flags") is True

        catalog.delete_namespace("ns-flags")

        assert catalog._is_namespace_shareable("ns-flags") is False
        assert catalog._is_namespace_public("ns-flags") is False


class TestNonShareableShortCircuit:
    """A non-shareable namespace delete skips the global referrer scan."""

    def test_non_shareable_skips_global_call(self, model_paths: tuple[str, str]) -> None:
        leaf, _holder = model_paths
        inner = FakeEntryRepository()
        counting = CountingEntryRepository(inner)
        catalog = Catalog(counting)  # type: ignore[arg-type]
        _seed_team(catalog, "tenant-A")
        # Meta entry with shareable=False ⇒ not shareable.
        catalog.create(make_meta_entry("tenant-A", shareable=False))
        _seed_leaf(catalog, "tenant-A", "leaf-1", leaf)
        counting.reset()

        catalog.delete_namespace("tenant-A")

        assert counting.count("find_references_global") == 0
