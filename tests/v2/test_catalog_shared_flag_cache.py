"""Tests for Story 17.4 / AC10, AC11 — per-Catalog shareable-flag cache + invalidation.

The resolver caches the per-namespace shareable flag for the lifetime of a
``Catalog`` instance, with cache invalidation on meta-entry mutation
(create / update / delete).
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
    provider: str = "openai"


class _Holder(BaseModel):
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
        "tests_fixture_17_4_cache",
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


class TestShareableFlagCacheHit:
    """AC10 — repeated cross-ns resolves issue exactly one ``repository.get`` per ns."""

    def test_repeated_resolve_does_not_reissue_meta_lookup(
        self, model_paths: tuple[str, str]
    ) -> None:
        leaf, holder = model_paths
        inner = FakeEntryRepository()
        counting = CountingEntryRepository(inner)
        catalog = Catalog(counting)  # type: ignore[arg-type]
        _seed_team(catalog, "global")
        catalog.create(make_meta_entry("global", shareable=True))
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
        # Cold cache: first resolve hits the meta lookup once.
        catalog.resolve_by_id("tenant-A", "agent-1")
        counting.reset()
        # Second resolve: cache should serve, no meta lookup.
        catalog.resolve_by_id("tenant-A", "agent-1")
        meta_lookups = sum(
            1 for name, args, _ in counting.calls if name == "get" and args == ("global", "_meta")
        )
        assert meta_lookups == 0, f"expected 0 meta lookups, got {meta_lookups}: {counting.calls}"


class TestShareableFlagCacheInvalidation:
    """AC10 — flipping the meta entry invalidates the cache."""

    def test_flip_shareable_true_to_false_makes_resolve_fail(
        self, model_paths: tuple[str, str]
    ) -> None:
        leaf, holder = model_paths
        repo = FakeEntryRepository()
        catalog = Catalog(repo)
        _seed_team(catalog, "global")
        catalog.create(make_meta_entry("global", shareable=True))
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
        # Cold resolve succeeds.
        catalog.resolve_by_id("tenant-A", "agent-1")

        # Flip global meta to shareable=false via update — this must
        # invalidate the cache so the next resolve fails.
        catalog.update(make_meta_entry("global", shareable=False))
        with pytest.raises(CatalogValidationError) as exc_info:
            catalog.resolve_by_id("tenant-A", "agent-1")
        assert "is not shareable" in exc_info.value.errors[0]

    def test_delete_meta_invalidates_cache(self, model_paths: tuple[str, str]) -> None:
        leaf, holder = model_paths
        repo = FakeEntryRepository()
        catalog = Catalog(repo)
        _seed_team(catalog, "global")
        catalog.create(make_meta_entry("global", shareable=True))
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
        # Prime the cache.
        catalog.resolve_by_id("tenant-A", "agent-1")

        # Deleting the meta entry must invalidate the cache.
        catalog.delete("global", "_meta")
        with pytest.raises(CatalogValidationError) as exc_info:
            catalog.resolve_by_id("tenant-A", "agent-1")
        assert "is not shareable" in exc_info.value.errors[0]

    def test_create_meta_invalidates_cache(self, model_paths: tuple[str, str]) -> None:
        leaf, holder = model_paths
        repo = FakeEntryRepository()
        catalog = Catalog(repo)
        # No meta on global initially.
        _seed_team(catalog, "global")
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
        _seed_team(catalog, "tenant-A")
        # Prime the negative cache by trying a resolve that fails.
        try:
            catalog.resolve(
                Entry(
                    id="probe",
                    kind="agent",
                    namespace="tenant-A",
                    user_id="alice",
                    model_type=holder,
                    payload={
                        "model_cfg": {
                            "__ref__": "shared-prompt",
                            "__namespace__": "global",
                        }
                    },
                )
            )
        except CatalogValidationError:
            pass
        # Now mark global shareable by creating a meta entry — cache should
        # invalidate so the next resolve succeeds.
        catalog.create(make_meta_entry("global", shareable=True))
        # Now we can create the agent without hitting the gate.
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


class TestShareableFlagCachePerInstance:
    """AC11 — the cache is per-Catalog-instance, not module-global."""

    def test_two_catalogs_have_independent_caches(self) -> None:
        repo = FakeEntryRepository()
        c1 = Catalog(repo)
        c2 = Catalog(repo)
        # Independent dicts — mutating one must not affect the other.
        c1._shareable_flag_cache["global"] = True
        assert c2._shareable_flag_cache == {}


class TestShareableFlagStrictBoolSemantics:
    """Story 17.7 / AC3 + AC18 — only typed-bool ``True`` at the root enables sharing.

    The gate body is ``meta.payload.get("shareable") is True`` — strict-bool
    comparison. ``1``, ``"true"``, ``"True"``, and other truthy strings all
    fall through to ``False``. The legacy nested
    ``payload["properties"]["shared"]`` shape is plain string data now and
    is NOT consulted by the gate (strict-cutover, no read-time fallback).
    """

    @pytest.mark.parametrize(
        "non_true_payload",
        [
            # Strict-cutover (AC11): legacy nested shape is plain data,
            # gate sees no root-level ``shareable`` key → shareable=False.
            {"properties": {"shared": "true"}},
            # Strict-bool (AC18): the string ``"true"`` at the root is NOT
            # coerced to ``True`` by the gate.
            {"shareable": "true"},
            # Strict-bool: integer 1 (a truthy value) is NOT coerced.
            {"shareable": 1},
            # Explicit False root value.
            {"shareable": False},
            # Missing key entirely.
            {},
        ],
        ids=["legacy-nested", "string-true", "int-1", "bool-false", "absent"],
    )
    def test_non_typed_true_values_are_not_shareable(
        self, non_true_payload: dict[str, object], model_paths: tuple[str, str]
    ) -> None:
        leaf, holder = model_paths
        repo = FakeEntryRepository()
        catalog = Catalog(repo)
        _seed_team(catalog, "global")
        # Bypass the catalog write pipeline (which routes through
        # ``NamespaceMeta`` and would reject ``"true"`` strings under strict
        # mode). The gate's behaviour is the focus: it must read the stored
        # payload's ``shareable`` key with strict-bool ``is True`` semantics
        # regardless of how the entry got into the repository.
        meta_payload: dict[str, object] = {
            "name": "global",
            "description": "",
            "properties": {},
        }
        meta_payload.update(non_true_payload)
        repo.put(
            Entry(
                id="_meta",
                kind="meta",
                namespace="global",
                user_id="anonymous",
                model_type="akgentic.catalog.models.namespace_meta.NamespaceMeta",
                payload=meta_payload,
            )
        )
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
        _seed_team(catalog, "tenant-A")
        with pytest.raises(CatalogValidationError) as exc_info:
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
        assert "is not shareable" in exc_info.value.errors[0]

    def test_typed_bool_true_at_root_is_shareable(self, model_paths: tuple[str, str]) -> None:
        """Positive control — the canonical post-17.7 shape resolves cross-ns refs."""
        leaf, holder = model_paths
        repo = FakeEntryRepository()
        catalog = Catalog(repo)
        _seed_team(catalog, "global")
        catalog.create(make_meta_entry("global", shareable=True))
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
        _seed_team(catalog, "tenant-A")
        # No exception — the cross-ns ref resolves because the meta entry
        # carries ``payload["shareable"] is True`` at the root.
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
