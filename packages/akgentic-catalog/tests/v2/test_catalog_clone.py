"""Tests for ``Catalog.clone`` — Story 15.5 ACs 21–30, 37, 38, 41.

Clone is a structural deep-copy with ref rewriting and intra-call dedup. All
tests run against both backends via ``catalog_factory``.
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


def _team_payload(manager_ref: bool = False, assistant_ref: bool = False) -> dict[str, Any]:
    """Minimal valid ``TeamCard`` payload, optionally carrying refs in metadata slots."""
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


class _LeafModel(BaseModel):
    provider: str = "openai"


class _SubModel(BaseModel):
    model_cfg: _LeafModel | None = None


class _RootModel(BaseModel):
    """Payload model carrying up to two ref slots — used to construct clone test graphs."""

    manager: _SubModel | None = None
    assistant: _SubModel | None = None
    name: str = "root"


def _register_models(monkeypatch: pytest.MonkeyPatch) -> tuple[str, str, str]:
    """Register leaf/sub/root under a fake ``akgentic.*`` path; return the three FQCNs."""
    module_name = register_akgentic_test_module(
        monkeypatch,
        "tests_fixture_15_5_clone_models",
        _LeafModel=_LeafModel,
        _SubModel=_SubModel,
        _RootModel=_RootModel,
    )
    return (
        f"{module_name}._LeafModel",
        f"{module_name}._SubModel",
        f"{module_name}._RootModel",
    )


def _seed_team(catalog: Catalog, namespace: str, user_id: str = "anonymous") -> None:
    """Seed a minimal team entry to satisfy the bootstrap invariant."""
    catalog.create(
        Entry(
            id="team",
            kind="team",
            namespace=namespace,
            user_id=user_id,
            model_type=_TEAM_TYPE,
            payload=_team_payload(),
        )
    )


def _seed_three_level_graph(
    catalog: Catalog,
    namespace: str,
    leaf_type: str,
    sub_type: str,
    root_type: str,
    user_id: str = "anonymous",
) -> None:
    """Seed a 3-level ref graph: root→id_mgr→id_gpt_41 (leaf)."""
    _seed_team(catalog, namespace=namespace, user_id=user_id)
    catalog.create(
        Entry(
            id="id_gpt_41",
            kind="model",
            namespace=namespace,
            user_id=user_id,
            model_type=leaf_type,
            payload={"provider": "openai"},
        )
    )
    catalog.create(
        Entry(
            id="id_mgr",
            kind="agent",
            namespace=namespace,
            user_id=user_id,
            model_type=sub_type,
            payload={"model_cfg": {"__ref__": "id_gpt_41"}},
        )
    )
    catalog.create(
        Entry(
            id="root",
            kind="agent",
            namespace=namespace,
            user_id=user_id,
            model_type=root_type,
            payload={"manager": {"__ref__": "id_mgr"}, "name": "root"},
        )
    )


class TestCloneIdPolicy:
    """AC21 — cross-ns preserves id; AC22 — same-ns suffixes (handling taken suffixes)."""

    def test_cross_namespace_preserves_id(
        self, catalog_factory: CatalogFactory, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        catalog, _ = catalog_factory()
        leaf, sub, root = _register_models(monkeypatch)
        _seed_three_level_graph(catalog, "src-ns", leaf, sub, root, user_id="anonymous")
        _seed_team(catalog, namespace="dst-ns", user_id="alice")
        stored = catalog.clone(
            src_namespace="src-ns",
            src_id="root",
            dst_namespace="dst-ns",
            dst_user_id="alice",
        )
        assert stored.id == "root"
        assert stored.namespace == "dst-ns"
        assert stored.user_id == "alice"

    def test_same_namespace_suffixes_id(
        self, catalog_factory: CatalogFactory, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        catalog, _ = catalog_factory()
        leaf, sub, root = _register_models(monkeypatch)
        _seed_three_level_graph(catalog, "ns-1", leaf, sub, root, user_id="alice")
        stored = catalog.clone(
            src_namespace="ns-1",
            src_id="root",
            dst_namespace="ns-1",
            dst_user_id="alice",
        )
        assert stored.id == "root-2"

    def test_same_namespace_skips_taken_suffix(
        self, catalog_factory: CatalogFactory, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        catalog, _ = catalog_factory()
        leaf, sub, root = _register_models(monkeypatch)
        _seed_three_level_graph(catalog, "ns-2", leaf, sub, root, user_id="alice")
        first = catalog.clone(
            src_namespace="ns-2",
            src_id="root",
            dst_namespace="ns-2",
            dst_user_id="alice",
        )
        assert first.id == "root-2"
        second = catalog.clone(
            src_namespace="ns-2",
            src_id="root",
            dst_namespace="ns-2",
            dst_user_id="alice",
        )
        assert second.id == "root-3"


class TestCloneDeepCopy:
    """AC23 — three-level ref graph produces three cloned entries with rewired refs."""

    def test_three_level_clone(
        self, catalog_factory: CatalogFactory, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        catalog, repo = catalog_factory()
        leaf, sub, root = _register_models(monkeypatch)
        _seed_three_level_graph(catalog, "src-ns", leaf, sub, root, user_id="anonymous")
        _seed_team(catalog, namespace="dst-ns", user_id="alice")
        catalog.clone(
            src_namespace="src-ns",
            src_id="root",
            dst_namespace="dst-ns",
            dst_user_id="alice",
        )
        dst_entries = {e.id: e for e in repo.list_by_namespace("dst-ns")}
        # team + 3 cloned = 4 entries in dst-ns.
        assert set(dst_entries) == {"team", "root", "id_mgr", "id_gpt_41"}
        # Ref markers rewired to point at newly-minted dst ids (same names here).
        assert dst_entries["root"].payload["manager"] == {"__ref__": "id_mgr"}
        assert dst_entries["id_mgr"].payload["model_cfg"] == {"__ref__": "id_gpt_41"}


class TestCloneDedup:
    """AC24 — shared sub-entries are cloned once (intra-call dedup map)."""

    def test_shared_leaf_cloned_once(
        self, catalog_factory: CatalogFactory, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        catalog, repo = catalog_factory()
        leaf, sub, root = _register_models(monkeypatch)
        _seed_team(catalog, namespace="src-ns", user_id="anonymous")
        catalog.create(
            Entry(
                id="id_gpt_41",
                kind="model",
                namespace="src-ns",
                model_type=leaf,
                payload={"provider": "openai"},
            )
        )
        catalog.create(
            Entry(
                id="id_mgr",
                kind="agent",
                namespace="src-ns",
                model_type=sub,
                payload={"model_cfg": {"__ref__": "id_gpt_41"}},
            )
        )
        catalog.create(
            Entry(
                id="id_asst",
                kind="agent",
                namespace="src-ns",
                model_type=sub,
                payload={"model_cfg": {"__ref__": "id_gpt_41"}},
            )
        )
        catalog.create(
            Entry(
                id="root",
                kind="agent",
                namespace="src-ns",
                model_type=root,
                payload={
                    "manager": {"__ref__": "id_mgr"},
                    "assistant": {"__ref__": "id_asst"},
                },
            )
        )
        _seed_team(catalog, namespace="dst-ns", user_id="alice")
        catalog.clone(
            src_namespace="src-ns",
            src_id="root",
            dst_namespace="dst-ns",
            dst_user_id="alice",
        )
        dst_entries = {e.id for e in repo.list_by_namespace("dst-ns")}
        # team + root + id_mgr + id_asst + id_gpt_41 = 5; but without team it's 4 clones.
        assert dst_entries == {"team", "root", "id_mgr", "id_asst", "id_gpt_41"}


class TestCloneUserIdPropagation:
    """AC26 — dst_user_id is stamped on every cloned entry (including sub-entries)."""

    def test_enterprise_to_user(
        self, catalog_factory: CatalogFactory, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        catalog, repo = catalog_factory()
        leaf, sub, root = _register_models(monkeypatch)
        _seed_three_level_graph(catalog, "src-ns", leaf, sub, root, user_id="anonymous")
        _seed_team(catalog, namespace="dst-ns", user_id="alice")
        catalog.clone(
            src_namespace="src-ns",
            src_id="root",
            dst_namespace="dst-ns",
            dst_user_id="alice",
        )
        for eid in ("root", "id_mgr", "id_gpt_41"):
            entry = repo.get("dst-ns", eid)
            assert entry is not None
            assert entry.user_id == "alice"

    def test_enterprise_to_enterprise(
        self, catalog_factory: CatalogFactory, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        catalog, repo = catalog_factory()
        leaf, sub, root = _register_models(monkeypatch)
        _seed_three_level_graph(catalog, "src-ns", leaf, sub, root, user_id="anonymous")
        _seed_team(catalog, namespace="dst-ns", user_id="anonymous")
        catalog.clone(
            src_namespace="src-ns",
            src_id="root",
            dst_namespace="dst-ns",
            dst_user_id="anonymous",
        )
        for eid in ("root", "id_mgr", "id_gpt_41"):
            entry = repo.get("dst-ns", eid)
            assert entry is not None
            assert entry.user_id == "anonymous"


class TestCloneAtomicity:
    """AC27, AC41 — missing-target failure leaves dst-ns untouched; zero put calls."""

    def test_corrupted_source_raises_and_does_not_write(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        leaf, sub, root = _register_models(monkeypatch)
        fake = FakeEntryRepository()
        # Seed a corrupted source graph bypassing catalog validation.
        for entry in (
            Entry(
                id="team",
                kind="team",
                namespace="src-ns",
                model_type=_TEAM_TYPE,
                payload=_team_payload(),
            ),
            Entry(
                id="id_mgr",
                kind="agent",
                namespace="src-ns",
                model_type=sub,
                payload={"model_cfg": {"__ref__": "id_missing"}},
            ),
            Entry(
                id="root",
                kind="agent",
                namespace="src-ns",
                model_type=root,
                payload={"manager": {"__ref__": "id_mgr"}},
            ),
        ):
            fake.put(entry)
        counting = CountingEntryRepository(fake)
        catalog = Catalog(counting)
        counting.reset()
        with pytest.raises(CatalogValidationError):
            catalog.clone(
                src_namespace="src-ns",
                src_id="root",
                dst_namespace="dst-ns",
                dst_user_id="alice",
            )
        # Zero writes landed.
        put_calls = [c for c in counting.calls if c[0] == "put"]
        assert put_calls == []
        assert fake.list_by_namespace("dst-ns") == []


class TestCloneMissingSource:
    """AC28 — clone with non-existent src_id raises ``EntryNotFoundError``."""

    def test_missing_source_raises(self, catalog_factory: CatalogFactory) -> None:
        catalog, _ = catalog_factory()
        with pytest.raises(EntryNotFoundError) as exc:
            catalog.clone(
                src_namespace="nope",
                src_id="ghost",
                dst_namespace="anywhere",
                dst_user_id="alice",
            )
        msg = str(exc.value)
        assert "not found" in msg
        assert "nope" in msg
        assert "ghost" in msg


class TestCloneSkipsPrepareForWrite:
    """AC30 — clone does not invoke ``prepare_for_write``."""

    def test_prepare_for_write_is_not_called(
        self, catalog_factory: CatalogFactory, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        catalog, _ = catalog_factory()
        leaf, sub, root = _register_models(monkeypatch)
        _seed_three_level_graph(catalog, "src-ns", leaf, sub, root, user_id="anonymous")
        _seed_team(catalog, namespace="dst-ns", user_id="alice")

        import akgentic.catalog.catalog as catalog_module

        calls: list[Entry] = []
        real = catalog_module.prepare_for_write

        def _spy(entry: Entry, repository: Any, **kwargs: Any) -> Entry:
            calls.append(entry)
            return real(entry, repository, **kwargs)

        monkeypatch.setattr(catalog_module, "prepare_for_write", _spy)
        calls.clear()
        catalog.clone(
            src_namespace="src-ns",
            src_id="root",
            dst_namespace="dst-ns",
            dst_user_id="alice",
        )
        assert calls == []


class TestCloneCrossNs:
    """Story 17.3 / AC13 — cross-ns refs survive clone byte-for-byte."""

    def _seed_target_with_cross_ns_payload(
        self,
        catalog: Catalog,
        src_namespace: str,
        cross_ns_marker: dict[str, Any],
        root_type: str,
    ) -> None:
        """Persist a team + a root entry whose payload carries a cross-ns ref."""
        _seed_team(catalog, namespace=src_namespace, user_id="anonymous")
        # Use a permissive root model — _RootModel.manager accepts any subkeys.
        catalog.create(
            Entry(
                id="root",
                kind="agent",
                namespace=src_namespace,
                user_id="anonymous",
                model_type=root_type,
                payload={"manager": cross_ns_marker, "name": "root"},
            )
        )

    def test_canonical_cross_ns_marker_preserved(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Any
    ) -> None:
        from akgentic.catalog.repositories.yaml import YamlEntryRepository

        leaf, _sub, root = _register_models(monkeypatch)
        # The target namespace must be marked shareable so the source entry
        # passes prepare_for_write at create() time.
        repo = YamlEntryRepository(tmp_path)
        catalog = Catalog(repo)
        # Seed the cross-ns target.
        _seed_team(catalog, namespace="global", user_id="anonymous")
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
        cross_ns_marker = {
            "model_cfg": {
                "__ref__": "shared-prompt",
                "__namespace__": "global",
            }
        }
        self._seed_target_with_cross_ns_payload(
            catalog,
            src_namespace="tenant-A",
            cross_ns_marker=cross_ns_marker,
            root_type=root,
        )
        _seed_team(catalog, namespace="tenant-B", user_id="anonymous")
        cloned = catalog.clone(
            src_namespace="tenant-A",
            src_id="root",
            dst_namespace="tenant-B",
            dst_user_id="anonymous",
        )
        # The cross-ns marker survives verbatim.
        marker = cloned.payload["manager"]["model_cfg"]
        assert marker == {
            "__ref__": "shared-prompt",
            "__namespace__": "global",
        }

    def test_shorthand_cross_ns_marker_preserved(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Any
    ) -> None:
        from akgentic.catalog.repositories.yaml import YamlEntryRepository

        leaf, _sub, root = _register_models(monkeypatch)
        repo = YamlEntryRepository(tmp_path)
        catalog = Catalog(repo)
        _seed_team(catalog, namespace="global", user_id="anonymous")
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
        cross_ns_marker = {
            "model_cfg": {"__ref__": "global.shared-prompt"},
        }
        self._seed_target_with_cross_ns_payload(
            catalog,
            src_namespace="tenant-A",
            cross_ns_marker=cross_ns_marker,
            root_type=root,
        )
        _seed_team(catalog, namespace="tenant-B", user_id="anonymous")
        cloned = catalog.clone(
            src_namespace="tenant-A",
            src_id="root",
            dst_namespace="tenant-B",
            dst_user_id="anonymous",
        )
        # Shorthand survives verbatim — not rewritten.
        assert cloned.payload["manager"]["model_cfg"] == {"__ref__": "global.shared-prompt"}

    def test_mixed_local_and_cross_ns_refs_round_trip(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Any
    ) -> None:
        """Local refs are rewritten to dst ids; cross-ns refs survive verbatim."""
        from akgentic.catalog.repositories.yaml import YamlEntryRepository

        leaf, sub, root = _register_models(monkeypatch)
        repo = YamlEntryRepository(tmp_path)
        catalog = Catalog(repo)

        # Seed global cross-ns target with a shareable meta entry.
        _seed_team(catalog, namespace="global", user_id="anonymous")
        catalog.create(make_meta_entry("global", shareable=True))
        catalog.create(
            Entry(
                id="global-leaf",
                kind="prompt",
                namespace="global",
                user_id="anonymous",
                model_type=leaf,
                payload={"provider": "global"},
            )
        )

        # Seed tenant-A: a local sub-entry + a root with a local ref + a cross-ns ref.
        _seed_team(catalog, namespace="tenant-A", user_id="anonymous")
        catalog.create(
            Entry(
                id="local-sub",
                kind="agent",
                namespace="tenant-A",
                user_id="anonymous",
                model_type=sub,
                payload={"model_cfg": {"provider": "local"}},
            )
        )
        catalog.create(
            Entry(
                id="root",
                kind="agent",
                namespace="tenant-A",
                user_id="anonymous",
                model_type=root,
                payload={
                    "manager": {"__ref__": "local-sub"},
                    "assistant": {
                        "model_cfg": {"__ref__": "global.global-leaf"},
                    },
                    "name": "root",
                },
            )
        )

        # Same-namespace clone: local-sub gets a numeric suffix; cross-ns survives.
        _seed_team(catalog, namespace="tenant-B", user_id="anonymous")
        cloned = catalog.clone(
            src_namespace="tenant-A",
            src_id="root",
            dst_namespace="tenant-B",
            dst_user_id="anonymous",
        )
        # Cross-ns ref survives verbatim.
        assert cloned.payload["assistant"]["model_cfg"] == {"__ref__": "global.global-leaf"}
        # Local ref is rewritten to the cloned dst id (cross-ns clone reuses src id).
        assert cloned.payload["manager"] == {"__ref__": "local-sub"}
        # And the cloned local-sub now exists in tenant-B.
        assert repo.get("tenant-B", "local-sub") is not None
