"""Tests for ``Catalog.export_namespace_yaml`` / ``import_namespace_yaml`` (Story 16.2).

Every round-trip test runs against both backends via the parametrised
``catalog_factory`` fixture. The dangling-ref and prepare-for-write-failure
no-op assertions use the single-backend ``counting_catalog`` fixture so the
test can verify ``put`` was never invoked.
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import BaseModel

from akgentic.catalog.catalog import Catalog
from akgentic.catalog.models.entry import Entry
from akgentic.catalog.models.errors import CatalogValidationError
from akgentic.catalog.serialization import dump_namespace

from .conftest import (
    CatalogFactory,
    CountingEntryRepository,
    make_meta_entry,
    register_akgentic_test_module,
)

_TEAM_TYPE = "akgentic.team.models.TeamCard"


def _team_payload() -> dict[str, Any]:
    """Return a minimal valid ``TeamCard`` payload (copied from test_catalog_crud)."""
    return {
        "name": "team",
        "description": "",
        "entry_point": {
            "card": {
                "role": "entry",
                "description": "entry",
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


class _LeafPayloadModel(BaseModel):
    provider: str = "openai"
    temperature: float = 0.0


class _AgentPayloadModel(BaseModel):
    provider: str = "openai"
    temperature: float = 0.0
    model_cfg: _LeafPayloadModel | None = None


def _register_agent_models(monkeypatch: pytest.MonkeyPatch) -> tuple[str, str]:
    """Register stub agent + leaf payload models; return their FQCN paths."""
    module_name = register_akgentic_test_module(
        monkeypatch,
        "tests_fixture_16_2_bundle",
        _AgentPayloadModel=_AgentPayloadModel,
        _LeafPayloadModel=_LeafPayloadModel,
    )
    return f"{module_name}._AgentPayloadModel", f"{module_name}._LeafPayloadModel"


def _seed_team(
    catalog: Catalog,
    namespace: str,
    user_id: str = "alice",
) -> Entry:
    return catalog.create(
        Entry(
            id="team",
            kind="team",
            namespace=namespace,
            user_id=user_id,
            model_type=_TEAM_TYPE,
            payload=_team_payload(),
        )
    )


def _seed_agent(
    catalog: Catalog,
    namespace: str,
    id: str,
    user_id: str = "alice",
    payload: dict[str, Any] | None = None,
    model_type: str | None = None,
) -> Entry:
    return catalog.create(
        Entry(
            id=id,
            kind="agent",
            namespace=namespace,
            user_id=user_id,
            model_type=model_type or "akgentic.core.agent_card.AgentCard",
            payload=payload if payload is not None else _agent_payload(id),
        )
    )


def _agent_payload(id: str = "a") -> dict[str, Any]:
    return {
        "role": "r",
        "description": "",
        "skills": [],
        "agent_class": "akgentic.core.agent.Akgent",
        "config": {"name": id, "role": "r"},
        "routes_to": [],
        "metadata": {},
    }


# --- Export -----------------------------------------------------------------


class TestExportNamespaceYaml:
    """``Catalog.export_namespace_yaml``."""

    def test_export_empty_namespace_raises(self, catalog_factory: CatalogFactory) -> None:
        catalog, _ = catalog_factory()
        with pytest.raises(CatalogValidationError):
            catalog.export_namespace_yaml("nope")

    def test_export_round_trip_idempotent(self, catalog_factory: CatalogFactory) -> None:
        """Two consecutive exports of an unchanged namespace produce byte-identical YAML."""
        catalog, _ = catalog_factory()
        _seed_team(catalog, "ns-rt")
        _seed_agent(catalog, "ns-rt", "a")
        _seed_agent(catalog, "ns-rt", "b")
        yaml_text_a = catalog.export_namespace_yaml("ns-rt")
        yaml_text_b = catalog.export_namespace_yaml("ns-rt")
        # Story 17.6 round-trip pin: deterministic ordering yields byte-identical
        # re-export, including the header trio synthesized via team-fallback.
        assert yaml_text_a == yaml_text_b


# --- Import -----------------------------------------------------------------


class TestImportNamespaceYaml:
    """``Catalog.import_namespace_yaml``."""

    def test_import_into_empty_namespace_creates_entries(
        self, catalog_factory: CatalogFactory
    ) -> None:
        catalog, repo = catalog_factory()
        _seed_team(catalog, "ns-src")
        _seed_agent(catalog, "ns-src", "a")
        yaml_text = catalog.export_namespace_yaml("ns-src")
        # Rewrite to a different destination namespace.
        new_text = yaml_text.replace("ns-src", "ns-dst")
        result = catalog.import_namespace_yaml(new_text)
        # The returned list reflects the bundle entries (team + a). Story 17.6
        # AC11: the meta upsert is an atomic side-effect, NOT a bundle entry.
        assert {e.id for e in result} == {"team", "a"}
        fetched = repo.list_by_namespace("ns-dst")
        # Persisted state includes the auto-upserted `_meta` entry — the export
        # synthesized header fields via team-fallback (no source meta), and the
        # import path upserts a `_meta` from those fields atomically (AC11).
        assert {e.id for e in fetched} == {"team", "a", "_meta"}

    def test_import_atomic_replace(self, catalog_factory: CatalogFactory) -> None:
        catalog, repo = catalog_factory()
        _seed_team(catalog, "ns-swap")
        _seed_agent(catalog, "ns-swap", "A")
        _seed_agent(catalog, "ns-swap", "B")
        _seed_agent(catalog, "ns-swap", "C")

        # New bundle: team + updated A + new D; B and C removed.
        bundle_entries = [
            Entry(
                id="team",
                kind="team",
                namespace="ns-swap",
                user_id="alice",
                model_type=_TEAM_TYPE,
                payload=_team_payload(),
            ),
            Entry(
                id="A",
                kind="agent",
                namespace="ns-swap",
                user_id="alice",
                model_type="akgentic.core.agent_card.AgentCard",
                payload=_agent_payload("A-updated"),
                description="updated A",
            ),
            Entry(
                id="D",
                kind="agent",
                namespace="ns-swap",
                user_id="alice",
                model_type="akgentic.core.agent_card.AgentCard",
                payload=_agent_payload("D"),
            ),
        ]
        yaml_text = dump_namespace(bundle_entries)
        catalog.import_namespace_yaml(yaml_text)
        fetched = {e.id: e for e in repo.list_by_namespace("ns-swap")}
        assert set(fetched.keys()) == {"team", "A", "D"}
        assert fetched["A"].description == "updated A"

    def test_import_rejects_bundle_with_no_team(
        self, catalog_factory: CatalogFactory, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        catalog, _ = catalog_factory()
        agent_type, _ = _register_agent_models(monkeypatch)
        bundle = [
            Entry(
                id="a",
                kind="agent",
                namespace="ns-nt",
                user_id="alice",
                model_type=agent_type,
                payload={},
            )
        ]
        yaml_text = dump_namespace(bundle)
        with pytest.raises(CatalogValidationError) as exc_info:
            catalog.import_namespace_yaml(yaml_text)
        assert any("has no team entry and no meta entry" in e for e in exc_info.value.errors)

    def test_import_rejects_multiple_team_entries(self, catalog_factory: CatalogFactory) -> None:
        catalog, _ = catalog_factory()
        bundle = [
            Entry(
                id="team1",
                kind="team",
                namespace="ns-mt",
                user_id="alice",
                model_type=_TEAM_TYPE,
                payload=_team_payload(),
            ),
            Entry(
                id="team2",
                kind="team",
                namespace="ns-mt",
                user_id="alice",
                model_type=_TEAM_TYPE,
                payload=_team_payload(),
            ),
        ]
        yaml_text = dump_namespace(bundle)
        with pytest.raises(CatalogValidationError) as exc_info:
            catalog.import_namespace_yaml(yaml_text)
        assert any("multiple team entries" in e for e in exc_info.value.errors)

    def test_validate_bundle_invariants_rejects_ownership_mismatch(
        self, catalog_factory: CatalogFactory
    ) -> None:
        """Exercise ``Catalog._validate_bundle_invariants`` with hand-crafted mismatch.

        The YAML bundle format assigns ``user_id`` from the document-level
        key to every entry, so a mismatch cannot surface through
        ``load_namespace``. This test invokes the invariant helper directly
        with a list whose entries disagree on ``user_id`` to prove the
        Catalog-level check still rejects the mismatch — matching the
        ``_check_ownership`` error shape referenced in AC22.
        """
        catalog, _ = catalog_factory()
        prepared = [
            Entry(
                id="team",
                kind="team",
                namespace="ns-own",
                user_id="alice",
                model_type=_TEAM_TYPE,
                payload=_team_payload(),
            ),
            Entry(
                id="rogue",
                kind="agent",
                namespace="ns-own",
                user_id="bob",
                model_type="akgentic.core.agent_card.AgentCard",
                payload=_agent_payload("rogue"),
            ),
        ]
        with pytest.raises(CatalogValidationError) as exc_info:
            catalog._validate_bundle_invariants(prepared)
        assert any(
            "Ownership mismatch" in e and "entry 'rogue'" in e for e in exc_info.value.errors
        )

    def test_import_rejects_dangling_ref(
        self,
        counting_catalog: tuple[Catalog, CountingEntryRepository],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        catalog, counting = counting_catalog
        agent_type, leaf_type = _register_agent_models(monkeypatch)
        # Pre-seed a team + a leaf target that exists in the current
        # namespace state. ``prepare_for_write`` therefore succeeds (the ref
        # resolves against the repo), but the bundle check catches that
        # ``ghost`` is absent from the imported bundle's id set.
        _seed_team(catalog, "ns-dr")
        catalog.create(
            Entry(
                id="ghost",
                kind="model",
                namespace="ns-dr",
                user_id="alice",
                model_type=leaf_type,
                payload={"provider": "openai", "temperature": 0.0},
            )
        )
        # The bundle omits 'ghost' but an agent payload refers to it.
        bundle_team = Entry(
            id="team",
            kind="team",
            namespace="ns-dr",
            user_id="alice",
            model_type=_TEAM_TYPE,
            payload=_team_payload(),
        )
        bundle_agent = Entry(
            id="dangler",
            kind="agent",
            namespace="ns-dr",
            user_id="alice",
            model_type=agent_type,
            payload={
                "provider": "openai",
                "model_cfg": {"__ref__": "ghost", "__type__": leaf_type},
            },
        )
        yaml_text = dump_namespace([bundle_team, bundle_agent])
        counting.reset()
        with pytest.raises(CatalogValidationError) as exc_info:
            catalog.import_namespace_yaml(yaml_text)
        assert any("not found in bundle" in e for e in exc_info.value.errors)
        # Atomic-failure contract: no put during the failing call.
        assert counting.count("put") == 0
        assert counting.count("delete") == 0

    def test_import_rejects_prepare_for_write_failure(
        self,
        counting_catalog: tuple[Catalog, CountingEntryRepository],
    ) -> None:
        catalog, counting = counting_catalog
        _seed_team(catalog, "ns-pfw")
        # Build a bundle whose agent payload is structurally incompatible
        # with AgentCard (missing required ``role`` field) — prepare_for_write
        # will surface a CatalogValidationError during model validation.
        team_entry = Entry(
            id="team",
            kind="team",
            namespace="ns-pfw",
            user_id="alice",
            model_type=_TEAM_TYPE,
            payload=_team_payload(),
        )
        bad_agent = Entry(
            id="broken",
            kind="agent",
            namespace="ns-pfw",
            user_id="alice",
            model_type="akgentic.core.agent_card.AgentCard",
            payload={"not_a_real_field": "x"},  # missing required role/agent_class/config
        )
        yaml_text = dump_namespace([team_entry, bad_agent])
        counting.reset()
        with pytest.raises(CatalogValidationError):
            catalog.import_namespace_yaml(yaml_text)
        # Atomic-failure contract: no put / delete during the failing call.
        assert counting.count("put") == 0
        assert counting.count("delete") == 0

    def test_import_malformed_yaml_raises(self, catalog_factory: CatalogFactory) -> None:
        catalog, _ = catalog_factory()
        with pytest.raises(CatalogValidationError) as exc_info:
            catalog.import_namespace_yaml("{{{ not yaml }")
        assert any("Failed to parse bundle YAML" in e for e in exc_info.value.errors)

    def test_import_with_in_bundle_ref_succeeds(
        self, catalog_factory: CatalogFactory, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        catalog, repo = catalog_factory()
        agent_type, leaf_type = _register_agent_models(monkeypatch)
        # Pre-seed the namespace with a team so create() is happy.
        bundle = [
            Entry(
                id="team",
                kind="team",
                namespace="ns-ref",
                user_id="alice",
                model_type=_TEAM_TYPE,
                payload=_team_payload(),
            ),
            Entry(
                id="leaf",
                kind="model",
                namespace="ns-ref",
                user_id="alice",
                model_type=leaf_type,
                payload={"provider": "openai", "temperature": 0.0},
            ),
            Entry(
                id="agent",
                kind="agent",
                namespace="ns-ref",
                user_id="alice",
                model_type=agent_type,
                payload={
                    "provider": "openai",
                    "model_cfg": {"__ref__": "leaf", "__type__": leaf_type},
                },
            ),
        ]
        yaml_text = dump_namespace(bundle)
        catalog.import_namespace_yaml(yaml_text)
        stored = {e.id: e for e in repo.list_by_namespace("ns-ref")}
        assert set(stored.keys()) == {"team", "leaf", "agent"}
        # Ref markers preserved in stored payload.
        assert stored["agent"].payload["model_cfg"] == {
            "__ref__": "leaf",
            "__type__": leaf_type,
        }


# --- Story 17.2 — bundle import meta singleton ----------------------------


_NAMESPACE_META_TYPE_BUNDLE = "akgentic.catalog.models.namespace_meta.NamespaceMeta"


def _meta_entry_for_bundle(
    namespace: str,
    user_id: str,
    entry_id: str = "_meta",
    name: str = "primary",
) -> Entry:
    return Entry(
        id=entry_id,
        kind="meta",
        namespace=namespace,
        user_id=user_id,
        model_type=_NAMESPACE_META_TYPE_BUNDLE,
        description=f"meta {entry_id}",
        payload={"name": name, "description": "", "properties": {}},
    )


class TestBundleImportMetaSingleton:
    """Story 17.2 AC4 — bundle import rejects two ``kind="meta"`` entries."""

    def test_bundle_with_two_meta_entries_rejected_no_writes(
        self,
        catalog_factory: CatalogFactory,
    ) -> None:
        catalog, repo = catalog_factory()
        # Pre-seed the namespace so we can verify state is unchanged on rollback.
        team = _seed_team(catalog, namespace="tenant-42", user_id="alice")
        before = sorted(e.id for e in repo.list_by_namespace("tenant-42"))

        bundle = [
            Entry(
                id="team",
                kind="team",
                namespace="tenant-42",
                user_id="alice",
                model_type=_TEAM_TYPE,
                payload=_team_payload(),
            ),
            _meta_entry_for_bundle("tenant-42", user_id="alice", entry_id="_meta", name="A"),
            _meta_entry_for_bundle(
                "tenant-42",
                user_id="alice",
                entry_id="meta-extra",
                name="B",
            ),
        ]
        yaml_text = dump_namespace(bundle)

        with pytest.raises(CatalogValidationError) as exc_info:
            catalog.import_namespace_yaml(yaml_text)
        assert any("has multiple meta entries" in m for m in exc_info.value.errors)
        # Atomic-failure contract: the namespace state is byte-identical to
        # the pre-import state.
        after = sorted(e.id for e in repo.list_by_namespace("tenant-42"))
        assert after == before
        assert team.id == "team"

    def test_bundle_with_one_meta_entry_imports_cleanly(
        self,
        catalog_factory: CatalogFactory,
    ) -> None:
        catalog, repo = catalog_factory()
        bundle = [
            Entry(
                id="team",
                kind="team",
                namespace="tenant-42",
                user_id="alice",
                model_type=_TEAM_TYPE,
                payload=_team_payload(),
            ),
            _meta_entry_for_bundle("tenant-42", user_id="alice", name="primary"),
        ]
        yaml_text = dump_namespace(bundle)
        catalog.import_namespace_yaml(yaml_text)
        ids = {e.id for e in repo.list_by_namespace("tenant-42")}
        assert ids == {"team", "_meta"}


class TestImportBundleCrossNs:
    """Story 17.4 — cross-ns markers exempt from bundle dangling-ref rule + shareable-flag gate."""

    def test_cross_ns_ref_with_shareable_target_imports(
        self,
        catalog_factory: CatalogFactory,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Bundle agent payload references global.shared — target in shareable namespace."""
        agent_type, leaf_type = _register_agent_models(monkeypatch)
        catalog, repo = catalog_factory()
        # Seed the global target + meta with shareable=true.
        _seed_team(catalog, "global", user_id="anonymous")
        catalog.create(make_meta_entry("global", shareable=True))
        catalog.create(
            Entry(
                id="shared-prompt",
                kind="prompt",
                namespace="global",
                user_id="anonymous",
                model_type=leaf_type,
                payload={"provider": "shared"},
            )
        )
        bundle = [
            Entry(
                id="team",
                kind="team",
                namespace="tenant-A",
                user_id="anonymous",
                model_type=_TEAM_TYPE,
                payload=_team_payload(),
            ),
            Entry(
                id="agent-1",
                kind="agent",
                namespace="tenant-A",
                user_id="anonymous",
                model_type=agent_type,
                payload={
                    "model_cfg": {
                        "__ref__": "shared-prompt",
                        "__namespace__": "global",
                    }
                },
            ),
        ]
        yaml_text = dump_namespace(bundle)
        catalog.import_namespace_yaml(yaml_text)
        ids = {e.id for e in repo.list_by_namespace("tenant-A")}
        assert ids == {"team", "agent-1"}

    def test_cross_ns_ref_to_non_shareable_namespace_rejected(
        self,
        catalog_factory: CatalogFactory,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Bundle with cross-ns ref to a non-shareable namespace fails at prepare_for_write."""
        agent_type, _leaf = _register_agent_models(monkeypatch)
        catalog, _repo = catalog_factory()
        # global has no meta entry — namespace is not shareable.
        bundle = [
            Entry(
                id="team",
                kind="team",
                namespace="tenant-A",
                user_id="anonymous",
                model_type=_TEAM_TYPE,
                payload=_team_payload(),
            ),
            Entry(
                id="agent-1",
                kind="agent",
                namespace="tenant-A",
                user_id="anonymous",
                model_type=agent_type,
                payload={"model_cfg": {"__ref__": "global.shared-prompt"}},
            ),
        ]
        yaml_text = dump_namespace(bundle)
        with pytest.raises(CatalogValidationError) as exc_info:
            catalog.import_namespace_yaml(yaml_text)
        msg = " | ".join(exc_info.value.errors)
        assert "is not shareable" in msg

    def test_cross_ns_ref_target_missing_in_shareable_namespace(
        self,
        catalog_factory: CatalogFactory,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Shareable namespace exists but target id missing ⇒ standard not-found."""
        agent_type, _leaf = _register_agent_models(monkeypatch)
        catalog, _repo = catalog_factory()
        # Mark global shareable but seed no target id.
        _seed_team(catalog, "global", user_id="anonymous")
        catalog.create(make_meta_entry("global", shareable=True))
        bundle = [
            Entry(
                id="team",
                kind="team",
                namespace="tenant-A",
                user_id="anonymous",
                model_type=_TEAM_TYPE,
                payload=_team_payload(),
            ),
            Entry(
                id="agent-1",
                kind="agent",
                namespace="tenant-A",
                user_id="anonymous",
                model_type=agent_type,
                payload={"model_cfg": {"__ref__": "global.does-not-exist"}},
            ),
        ]
        yaml_text = dump_namespace(bundle)
        with pytest.raises(CatalogValidationError) as exc_info:
            catalog.import_namespace_yaml(yaml_text)
        msg = " | ".join(exc_info.value.errors)
        assert "not found in namespace" in msg
        assert "global" in msg


# --- Story 17.6 — header projection on export ------------------------------


def _seed_meta(
    catalog: Catalog,
    namespace: str,
    *,
    name: str = "Tenant",
    description: str = "primary tenant",
    properties: dict[str, str] | None = None,
    shareable: bool = False,
    public: bool = False,
    user_id: str = "alice",
) -> Entry:
    """Create a `_meta` entry in ``namespace`` and return it.

    Story 17.7 — ``shareable`` is a typed bool at the root of the meta payload.
    Story 18.2 — ``public`` is a typed bool at the root of the meta payload.
    """
    return catalog.create(
        Entry(
            id="_meta",
            kind="meta",
            namespace=namespace,
            user_id=user_id,
            model_type=_NAMESPACE_META_TYPE_BUNDLE,
            payload={
                "name": name,
                "description": description,
                "properties": properties if properties is not None else {},
                "shareable": shareable,
                "public": public,
            },
        )
    )


class TestHeaderProjection:
    """Story 17.6 AC2 — `_meta` is hoisted to top-level header fields on export.

    Behaviours pinned:
    * Meta-present → header reads from meta.payload (name / description /
      properties pass through verbatim).
    * Meta-absent → header falls back to (team.payload['name'],
      team.description, {}).
    * Meta-with-empty-name → name falls back to team for `name` ONLY;
      description and properties still come from the meta entry.
    * Properties dict propagates verbatim; empty mapping emits as `{}`.
    * The hoisted meta entry NEVER appears in `entries:`.
    """

    def test_meta_present_header_reads_from_meta(self, catalog_factory: CatalogFactory) -> None:
        catalog, _ = catalog_factory()
        _seed_team(catalog, "tenant-A")
        _seed_meta(
            catalog,
            "tenant-A",
            name="Agent Team",
            description="Manager-led team",
            properties={"tier": "gold"},
            shareable=True,
        )
        text = catalog.export_namespace_yaml("tenant-A")
        import yaml as _yaml

        doc = _yaml.safe_load(text)
        # Story 18.2 — eight top-level keys in declaration order.
        assert list(doc.keys()) == [
            "namespace",
            "user_id",
            "name",
            "description",
            "properties",
            "shareable",
            "public",
            "entries",
        ]
        assert doc["name"] == "Agent Team"
        assert doc["description"] == "Manager-led team"
        assert doc["properties"] == {"tier": "gold"}
        assert doc["shareable"] is True
        assert doc["public"] is False
        # AC2 — the meta entry is NEVER in `entries:` for bundles produced
        # by Catalog.export_namespace_yaml.
        assert "_meta" not in doc["entries"]

    def test_meta_absent_falls_back_to_team(self, catalog_factory: CatalogFactory) -> None:
        catalog, _ = catalog_factory()
        _seed_team(catalog, "tenant-B")
        _seed_agent(catalog, "tenant-B", "a")
        text = catalog.export_namespace_yaml("tenant-B")
        import yaml as _yaml

        doc = _yaml.safe_load(text)
        # team payload['name'] = 'team' (from _team_payload); team.description = '' (default).
        assert doc["name"] == "team"
        assert doc["description"] == ""
        assert doc["properties"] == {}

    def test_meta_with_empty_name_falls_back_to_team_for_name_only(
        self, catalog_factory: CatalogFactory
    ) -> None:
        """AC2 — empty meta name → fall back to team for name; keep meta description/properties."""
        catalog, _ = catalog_factory()
        _seed_team(catalog, "tenant-C")
        # Hand-build a meta entry whose payload.name is empty (NamespaceMeta
        # forbids that at validation time; bypass via the resolver-allowlist
        # by writing the model_type that points at NamespaceMeta but with a
        # raw payload that the validator would reject in normal CRUD. We do
        # NOT go through Catalog.create here — that would re-validate.
        # Instead, write directly to the repo to seed the empty-name case.)
        catalog._repository.put(
            Entry(
                id="_meta",
                kind="meta",
                namespace="tenant-C",
                user_id="alice",
                model_type=_NAMESPACE_META_TYPE_BUNDLE,
                payload={
                    "name": "",
                    "description": "tenant description",
                    "properties": {"tier": "gold"},
                    "shareable": False,
                },
            )
        )
        text = catalog.export_namespace_yaml("tenant-C")
        import yaml as _yaml

        doc = _yaml.safe_load(text)
        # name falls back to team payload's name.
        assert doc["name"] == "team"
        # description and properties still come from the meta entry.
        assert doc["description"] == "tenant description"
        assert doc["properties"] == {"tier": "gold"}

    def test_shareable_flag_round_trips(self, catalog_factory: CatalogFactory) -> None:
        """Story 17.7 / AC8 — `shareable=True` on the meta entry propagates to the bundle header."""
        catalog, _ = catalog_factory()
        _seed_team(catalog, "tenant-D")
        _seed_meta(
            catalog,
            "tenant-D",
            name="Shared Tenant",
            shareable=True,
        )
        text = catalog.export_namespace_yaml("tenant-D")
        import yaml as _yaml

        doc = _yaml.safe_load(text)
        assert doc["shareable"] is True
        # `properties` is now fully free-form (no reserved keys).
        assert doc["properties"] == {}

    def test_export_emits_public_after_shareable(self, catalog_factory: CatalogFactory) -> None:
        # Story 18.2 AC10 — assert ordering by reading the raw YAML text:
        # the ``public:`` line index is greater than ``shareable:``'s.
        catalog, _ = catalog_factory()
        _seed_team(catalog, "tenant-pub-order")
        _seed_meta(
            catalog,
            "tenant-pub-order",
            name="Public Library",
            public=True,
        )
        text = catalog.export_namespace_yaml("tenant-pub-order")
        shareable_idx = next(
            i for i, line in enumerate(text.splitlines()) if line.startswith("shareable:")
        )
        public_idx = next(
            i for i, line in enumerate(text.splitlines()) if line.startswith("public:")
        )
        entries_idx = next(
            i for i, line in enumerate(text.splitlines()) if line.startswith("entries:")
        )
        assert shareable_idx < public_idx < entries_idx
        # PyYAML default emit shape: ``public: true`` (lowercase, unquoted).
        assert "public: true" in text

    def test_public_flag_round_trips(self, catalog_factory: CatalogFactory) -> None:
        # Story 18.2 AC4 — ``public=True`` on the meta entry propagates to
        # the bundle header.
        catalog, _ = catalog_factory()
        _seed_team(catalog, "tenant-pub")
        _seed_meta(
            catalog,
            "tenant-pub",
            name="Public Tenant",
            public=True,
        )
        text = catalog.export_namespace_yaml("tenant-pub")
        import yaml as _yaml

        doc = _yaml.safe_load(text)
        assert doc["public"] is True
        # ``shareable`` independent of ``public`` — both flags can be False
        # while ``public`` is True (the forkable-library state).
        assert doc["shareable"] is False

    def test_meta_payload_omits_public_key_projects_false(
        self, catalog_factory: CatalogFactory
    ) -> None:
        # Story 18.2 AC4 — pre-18.2 meta entries (no ``public`` key in
        # payload) project to ``public=False`` on export. Defensive: bypass
        # NamespaceMeta validation by writing directly to the repo.
        catalog, _ = catalog_factory()
        _seed_team(catalog, "tenant-legacy-meta")
        catalog._repository.put(
            Entry(
                id="_meta",
                kind="meta",
                namespace="tenant-legacy-meta",
                user_id="alice",
                model_type=_NAMESPACE_META_TYPE_BUNDLE,
                payload={
                    "name": "Pre-18.2 Tenant",
                    "description": "",
                    "properties": {},
                    "shareable": False,
                },
            )
        )
        text = catalog.export_namespace_yaml("tenant-legacy-meta")
        import yaml as _yaml

        doc = _yaml.safe_load(text)
        assert doc["public"] is False


# --- Story 17.6 — header upsert on import ----------------------------------


class TestImportHeaderUpsert:
    """Story 17.6 AC11 — meta is upserted from header fields atomically with entries."""

    def _build_bundle_with_header(
        self,
        namespace: str = "tenant-X",
        user_id: str = "alice",
        name: str = "Tenant X",
        description: str = "imported tenant",
        properties: dict[str, str] | None = None,
    ) -> str:
        """Build a Story 17.6 bundle string with the header trio populated."""
        team = Entry(
            id="team",
            kind="team",
            namespace=namespace,
            user_id=user_id,
            model_type=_TEAM_TYPE,
            payload=_team_payload(),
        )
        return dump_namespace(
            [team],
            name=name,
            description=description,
            properties=properties if properties is not None else {},
        )

    def test_import_creates_meta_when_absent(self, catalog_factory: CatalogFactory) -> None:
        """Bundle has header → meta is CREATED when none existed."""
        catalog, repo = catalog_factory()
        yaml_text = self._build_bundle_with_header(
            namespace="tenant-X",
            name="X-name",
            description="X-desc",
            properties={"owner_team": "platform"},
        )
        catalog.import_namespace_yaml(yaml_text)
        meta = repo.get("tenant-X", "_meta")
        assert meta is not None
        assert meta.payload["name"] == "X-name"
        assert meta.payload["description"] == "X-desc"
        assert meta.payload["properties"] == {"owner_team": "platform"}
        # Story 17.7 — `shareable` is a typed bool at the root of the meta payload.
        assert meta.payload["shareable"] is False

    def test_import_updates_meta_when_present(self, catalog_factory: CatalogFactory) -> None:
        """Bundle has header → existing meta is UPDATED in place."""
        catalog, repo = catalog_factory()
        _seed_team(catalog, "tenant-Y")
        _seed_meta(
            catalog,
            "tenant-Y",
            name="OLD",
            description="OLD-desc",
            properties={"existing": "val"},
        )
        yaml_text = self._build_bundle_with_header(
            namespace="tenant-Y",
            name="NEW",
            description="NEW-desc",
            properties={"owner_team": "platform"},
        )
        catalog.import_namespace_yaml(yaml_text)
        meta = repo.get("tenant-Y", "_meta")
        assert meta is not None
        assert meta.payload["name"] == "NEW"
        assert meta.payload["description"] == "NEW-desc"
        assert meta.payload["properties"] == {"owner_team": "platform"}

    def test_import_legacy_bundle_skips_meta_upsert(self, catalog_factory: CatalogFactory) -> None:
        """AC11 / AC12 — pre-17.5 bundle (no header trio) leaves existing _meta untouched."""
        catalog, repo = catalog_factory()
        _seed_team(catalog, "tenant-Z")
        _seed_meta(
            catalog,
            "tenant-Z",
            name="UNCHANGED",
            description="should not move",
            properties={"keep": "me"},
        )
        # Build a pre-17.5-shape bundle by hand (no top-level header keys).
        legacy_text = (
            "namespace: tenant-Z\n"
            "user_id: alice\n"
            "entries:\n"
            "  team:\n"
            "    kind: team\n"
            "    model_type: akgentic.team.models.TeamCard\n"
            "    parent_namespace: null\n"
            "    parent_id: null\n"
            "    description: ''\n"
            f"    payload: {_team_payload()!r}\n"
        )
        catalog.import_namespace_yaml(legacy_text)
        meta = repo.get("tenant-Z", "_meta")
        assert meta is not None
        assert meta.payload["name"] == "UNCHANGED"
        assert meta.payload["description"] == "should not move"
        assert meta.payload["properties"] == {"keep": "me"}

    def test_import_no_meta_no_header_leaves_meta_absent(
        self, catalog_factory: CatalogFactory
    ) -> None:
        """Pre-17.5 bundle into an empty namespace creates no _meta entry."""
        catalog, repo = catalog_factory()
        legacy_text = (
            "namespace: tenant-W\n"
            "user_id: alice\n"
            "entries:\n"
            "  team:\n"
            "    kind: team\n"
            "    model_type: akgentic.team.models.TeamCard\n"
            "    parent_namespace: null\n"
            "    parent_id: null\n"
            "    description: ''\n"
            f"    payload: {_team_payload()!r}\n"
        )
        catalog.import_namespace_yaml(legacy_text)
        # No _meta because the bundle had no header AND no _meta in entries:.
        ids = {e.id for e in repo.list_by_namespace("tenant-W")}
        assert ids == {"team"}

    def test_import_with_explicit_meta_in_entries_wins(
        self, catalog_factory: CatalogFactory
    ) -> None:
        """AC11 defensive — an explicit `_meta` in entries: wins over the (absent) header.

        Pre-17.5 / hand-edited bundle path: the bundle has no header trio
        but explicitly declares a _meta entry under entries:. The handler
        treats _meta as a normal local-entry create — the meta-singleton
        gate in Catalog.create allows exactly one _meta per namespace, so
        the import succeeds.
        """
        catalog, repo = catalog_factory()
        bundle = [
            Entry(
                id="team",
                kind="team",
                namespace="tenant-V",
                user_id="alice",
                model_type=_TEAM_TYPE,
                payload=_team_payload(),
            ),
            Entry(
                id="_meta",
                kind="meta",
                namespace="tenant-V",
                user_id="alice",
                model_type=_NAMESPACE_META_TYPE_BUNDLE,
                payload={
                    "name": "explicit-meta",
                    "description": "from entries",
                    "properties": {},
                },
            ),
        ]
        # No header kwargs → bundle has no top-level header → meta upsert skipped;
        # the meta entry comes from `entries:` instead.
        yaml_text = dump_namespace(bundle)
        catalog.import_namespace_yaml(yaml_text)
        meta = repo.get("tenant-V", "_meta")
        assert meta is not None
        assert meta.payload["name"] == "explicit-meta"

    def test_import_round_trips_public_true(self, catalog_factory: CatalogFactory) -> None:
        # Story 18.2 AC5 — importing a bundle with ``public: true`` upserts
        # a ``_meta`` entry whose ``payload["public"] is True``; re-exporting
        # produces a bundle with ``public: true``.
        catalog, repo = catalog_factory()
        team = Entry(
            id="team",
            kind="team",
            namespace="tenant-pub-import",
            user_id="alice",
            model_type=_TEAM_TYPE,
            payload=_team_payload(),
        )
        yaml_text = dump_namespace(
            [team],
            name="Imported Public",
            public=True,
        )
        catalog.import_namespace_yaml(yaml_text)
        meta = repo.get("tenant-pub-import", "_meta")
        assert meta is not None
        assert meta.payload["public"] is True
        # Round-trip: re-export and assert the wire shape.
        text = catalog.export_namespace_yaml("tenant-pub-import")
        import yaml as _yaml

        doc = _yaml.safe_load(text)
        assert doc["public"] is True

    def test_import_legacy_bundle_no_public_defaults_false(
        self, catalog_factory: CatalogFactory
    ) -> None:
        # Story 18.2 AC5 — pre-18.2 bundle (header trio carries no
        # ``public``) upserts a ``_meta`` entry whose ``payload["public"]``
        # is ``False`` (the NamespaceMeta field default).
        catalog, repo = catalog_factory()
        team = Entry(
            id="team",
            kind="team",
            namespace="tenant-pub-legacy",
            user_id="alice",
            model_type=_TEAM_TYPE,
            payload=_team_payload(),
        )
        # ``shareable=True`` forces the header but ``public`` is omitted.
        yaml_text = dump_namespace(
            [team],
            name="Legacy",
            shareable=True,
        )
        # Strip the ``public:`` line to simulate a true pre-18.2 bundle.
        lines = [line for line in yaml_text.splitlines() if not line.startswith("public:")]
        yaml_no_public = "\n".join(lines) + "\n"
        catalog.import_namespace_yaml(yaml_no_public)
        meta = repo.get("tenant-pub-legacy", "_meta")
        assert meta is not None
        assert meta.payload["public"] is False
        # ``shareable`` still flowed through.
        assert meta.payload["shareable"] is True

    def test_bundle_header_public_strict_bool(self, catalog_factory: CatalogFactory) -> None:
        # Story 18.2 AC5 — importing a bundle whose ``public:`` value is
        # the string ``"true"`` upserts a ``_meta`` entry whose
        # ``payload["public"] is False`` (defensive projection — matches
        # ``shareable``'s shape).
        catalog, repo = catalog_factory()
        team = Entry(
            id="team",
            kind="team",
            namespace="tenant-pub-strict",
            user_id="alice",
            model_type=_TEAM_TYPE,
            payload=_team_payload(),
        )
        yaml_text = dump_namespace([team], name="Strict")
        # Inject a string ``public: 'true'`` after ``public:`` (defensive).
        yaml_with_string = yaml_text.replace("public: false", "public: 'true'")
        catalog.import_namespace_yaml(yaml_with_string)
        meta = repo.get("tenant-pub-strict", "_meta")
        assert meta is not None
        assert meta.payload["public"] is False


# --- Story 17.6 — export with external refs --------------------------------


class TestExportExternalRefs:
    """Story 17.6 reshape of Story 17.5's behaviour pins.

    Behaviours pinned (carry over from Story 17.5):
    * Cross-ns targets in shareable namespaces appear in external sections.
    * Cross-ns targets in non-shareable namespaces are silently omitted.
    * Cross-ns targets that are missing are silently omitted.
    * Same-namespace short-circuit (cross-ns marker pointing at the bundle's
      own namespace is not external).
    * Transitive reachability with cycle protection.
    * No widening — same-namespace refs inside an external target's payload
      do NOT widen the section.

    Wire shape (NEW for Story 17.6):
    * External entries appear UNDER `entries:` with composite `<ns>.<id>` keys.
    * Per-kind external sections, marked `External ref, readonly`.
    * No top-level `external_refs:` field.
    """

    def test_external_target_in_shareable_namespace_appears(
        self, catalog_factory: CatalogFactory, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        agent_type, leaf_type = _register_agent_models(monkeypatch)
        catalog, _repo = catalog_factory()
        # Seed a shareable global namespace + a target.
        _seed_team(catalog, "global", user_id="anonymous")
        catalog.create(make_meta_entry("global", shareable=True))
        catalog.create(
            Entry(
                id="shared-model",
                kind="model",
                namespace="global",
                user_id="anonymous",
                model_type=leaf_type,
                payload={"provider": "shared", "temperature": 0.0},
            )
        )
        # Seed tenant with a ref to global.shared-model. Use the
        # _AgentPayloadModel shape (provider/temperature/model_cfg) so
        # prepare_for_write succeeds — the cross-ns walker reads `model_cfg`
        # as a dict and finds the marker.
        _seed_team(catalog, "tenant-S", user_id="anonymous")
        _seed_agent(
            catalog,
            "tenant-S",
            "agent-1",
            user_id="anonymous",
            payload={
                "provider": "openai",
                "temperature": 0.0,
                "model_cfg": {"__ref__": "global.shared-model"},
            },
            model_type=agent_type,
        )
        text = catalog.export_namespace_yaml("tenant-S")
        import yaml as _yaml

        doc = _yaml.safe_load(text)
        assert "global.shared-model" in doc["entries"]
        # No top-level external_refs key.
        assert "external_refs" not in doc

    def test_external_target_non_shareable_silently_omitted(
        self, catalog_factory: CatalogFactory, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        agent_type, leaf_type = _register_agent_models(monkeypatch)
        catalog, _ = catalog_factory()
        # Seed global with a target but NO shareable-flag.
        _seed_team(catalog, "global", user_id="anonymous")
        catalog.create(
            Entry(
                id="hidden-model",
                kind="model",
                namespace="global",
                user_id="anonymous",
                model_type=leaf_type,
                payload={"provider": "hidden", "temperature": 0.0},
            )
        )
        # We cannot create a tenant entry with a cross-ns ref to a
        # non-shareable target through Catalog.create (the shareable-flag
        # gate rejects). Bypass by writing directly to the repo.
        _seed_team(catalog, "tenant-N", user_id="anonymous")
        catalog._repository.put(
            Entry(
                id="agent-1",
                kind="agent",
                namespace="tenant-N",
                user_id="anonymous",
                model_type=agent_type,
                payload={
                    "model_cfg": {"__ref__": "global.hidden-model"},
                },
            )
        )
        text = catalog.export_namespace_yaml("tenant-N")
        import yaml as _yaml

        doc = _yaml.safe_load(text)
        # The non-shareable target is silently omitted (no external section emitted).
        assert "global.hidden-model" not in doc["entries"]

    def test_external_target_missing_silently_omitted(
        self, catalog_factory: CatalogFactory, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        agent_type, _leaf = _register_agent_models(monkeypatch)
        catalog, _ = catalog_factory()
        # Mark global shareable but seed no target id.
        _seed_team(catalog, "global", user_id="anonymous")
        catalog.create(make_meta_entry("global", shareable=True))
        # Bypass the catalog gate by writing the bad ref directly.
        _seed_team(catalog, "tenant-M", user_id="anonymous")
        catalog._repository.put(
            Entry(
                id="agent-1",
                kind="agent",
                namespace="tenant-M",
                user_id="anonymous",
                model_type=agent_type,
                payload={
                    "model_cfg": {"__ref__": "global.does-not-exist"},
                },
            )
        )
        text = catalog.export_namespace_yaml("tenant-M")
        import yaml as _yaml

        doc = _yaml.safe_load(text)
        assert "global.does-not-exist" not in doc["entries"]


# --- Story 17.6 — round-trip with external sections + snapshot --------------


class TestRoundTripBundle:
    """Story 17.6 AC20 / AC21 — round-trip + snapshot regression.

    AC20 — export → save → import → re-export. Local-entries content
    byte-identical (sans non-deterministic blank-line rules — assert
    structural equality via yaml.safe_load).

    AC21 — Snapshot fixture asserts the literal YAML structure (parsed)
    of a representative export.
    """

    def test_round_trip_preserves_local_entries(
        self, catalog_factory: CatalogFactory, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        catalog, _repo = catalog_factory()
        _seed_team(catalog, "tenant-RT", user_id="alice")
        _seed_meta(
            catalog,
            "tenant-RT",
            name="Roundtrip Tenant",
            description="rt",
            properties={"owner_team": "platform"},
            shareable=True,
        )
        _seed_agent(catalog, "tenant-RT", "a-1", user_id="alice")

        yaml_a = catalog.export_namespace_yaml("tenant-RT")
        # Import into a fresh dst-ns (rewrite the namespace string).
        yaml_a_dst = yaml_a.replace("tenant-RT", "dst-ns")
        catalog.import_namespace_yaml(yaml_a_dst)
        yaml_b = catalog.export_namespace_yaml("dst-ns")

        import yaml as _yaml

        parsed_a = _yaml.safe_load(yaml_a_dst)
        parsed_b = _yaml.safe_load(yaml_b)
        # Entries dict equality (key-order-independent).
        assert parsed_a["entries"] == parsed_b["entries"]
        # Header survives the round-trip via meta upsert.
        assert parsed_a["name"] == parsed_b["name"]
        assert parsed_a["description"] == parsed_b["description"]
        assert parsed_a["properties"] == parsed_b["properties"]
        # Story 17.7 — `shareable` round-trips through the header → meta upsert.
        assert parsed_a["shareable"] == parsed_b["shareable"]
        assert parsed_a["shareable"] is True

    def test_pre_175_bundle_imports_cleanly(self, catalog_factory: CatalogFactory) -> None:
        """AC12 — a hand-written legacy pre-17.5 bundle imports without errors."""
        catalog, repo = catalog_factory()
        legacy_text = (
            "namespace: tenant-LEG\n"
            "user_id: alice\n"
            "entries:\n"
            "  team:\n"
            "    kind: team\n"
            "    model_type: akgentic.team.models.TeamCard\n"
            "    parent_namespace: null\n"
            "    parent_id: null\n"
            "    description: ''\n"
            f"    payload: {_team_payload()!r}\n"
        )
        catalog.import_namespace_yaml(legacy_text)
        ids = {e.id for e in repo.list_by_namespace("tenant-LEG")}
        # No header trio → no meta upsert; only the team is created.
        assert ids == {"team"}

    def test_legacy_bundle_with_nested_shared_property_does_not_flip_root_shareable(
        self, catalog_factory: CatalogFactory
    ) -> None:
        """Story 17.7 / AC22 — strict cutover: legacy `properties: {shared: "true"}`
        is preserved as plain string data but does NOT flip `payload["shareable"]`
        to True (no read-time fallback).

        The legacy bundle (pre-17.7) carries six top-level keys: `name`,
        `description`, `properties`, no `shareable`. The import upserts a meta
        entry from the header — `shareable` defaults to False, even though the
        legacy `properties: {shared: "true"}` sits under the meta payload's
        `properties` map.
        """
        catalog, repo = catalog_factory()
        # Build a bundle by hand: six top-level keys (no `shareable`).
        # `properties.shared = "true"` is the Story 17.4 wire shape — plain
        # string data now, NOT consulted by the catalog gate.
        legacy_text = (
            "namespace: tenant-LEGSH\n"
            "user_id: alice\n"
            "name: Legacy Tenant\n"
            "description: from-pre-17.7\n"
            "properties:\n"
            "  shared: 'true'\n"
            "entries:\n"
            "  team:\n"
            "    kind: team\n"
            "    model_type: akgentic.team.models.TeamCard\n"
            "    parent_namespace: null\n"
            "    parent_id: null\n"
            "    description: ''\n"
            f"    payload: {_team_payload()!r}\n"
        )
        catalog.import_namespace_yaml(legacy_text)
        meta = repo.get("tenant-LEGSH", "_meta")
        assert meta is not None
        # The legacy nested `shared` is preserved verbatim under properties.
        assert meta.payload["properties"] == {"shared": "true"}
        # But `payload["shareable"]` is False — strict cutover, no read-time
        # fallback to the legacy nested shape.
        assert meta.payload["shareable"] is False
        # Resolver gate evaluates False — the namespace is NOT shareable.
        assert catalog._is_namespace_shareable("tenant-LEGSH") is False

    def test_byte_identical_export_no_writes(self, catalog_factory: CatalogFactory) -> None:
        """AC20 strict byte-identical contract."""
        catalog, _ = catalog_factory()
        _seed_team(catalog, "ns-bi")
        _seed_meta(catalog, "ns-bi", name="X", shareable=True)
        _seed_agent(catalog, "ns-bi", "a")
        text_a = catalog.export_namespace_yaml("ns-bi")
        text_b = catalog.export_namespace_yaml("ns-bi")
        assert text_a == text_b


class TestSnapshotShape:
    """Story 17.6 AC21 — structural snapshot of the canonical wire format."""

    def test_canonical_export_structure(
        self, catalog_factory: CatalogFactory, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        agent_type, leaf_type = _register_agent_models(monkeypatch)
        catalog, _repo = catalog_factory()
        # Build a representative namespace:
        # - 3 local kinds: team + agent + prompt (and meta hoisted to header).
        # - 2 external kinds: model + tool (in shared global namespace).
        _seed_team(catalog, "global", user_id="anonymous")
        catalog.create(make_meta_entry("global", shareable=True))
        catalog.create(
            Entry(
                id="m1",
                kind="model",
                namespace="global",
                user_id="anonymous",
                model_type=leaf_type,
                payload={"provider": "openai", "temperature": 0.0},
            )
        )
        catalog.create(
            Entry(
                id="t1",
                kind="tool",
                namespace="global",
                user_id="anonymous",
                model_type=leaf_type,
                payload={"provider": "shared", "temperature": 0.0},
            )
        )

        _seed_team(catalog, "tenant-Snap", user_id="anonymous")
        _seed_meta(
            catalog,
            "tenant-Snap",
            name="Snap Tenant",
            description="snap",
            shareable=False,
            user_id="anonymous",
        )
        catalog.create(
            Entry(
                id="prompt-1",
                kind="prompt",
                namespace="tenant-Snap",
                user_id="anonymous",
                model_type=leaf_type,
                payload={"provider": "p", "temperature": 0.0},
            )
        )
        catalog.create(
            Entry(
                id="agent-1",
                kind="agent",
                namespace="tenant-Snap",
                user_id="anonymous",
                model_type=agent_type,
                payload={
                    "provider": "openai",
                    "temperature": 0.0,
                    "model_cfg": {"__ref__": "global.m1"},
                },
            )
        )
        # Second agent referencing the tool — keeps payloads valid against
        # the test fixture's _AgentPayloadModel (provider/temperature/model_cfg).
        catalog.create(
            Entry(
                id="agent-2",
                kind="agent",
                namespace="tenant-Snap",
                user_id="anonymous",
                model_type=agent_type,
                payload={
                    "provider": "openai",
                    "temperature": 0.0,
                    "model_cfg": {"__ref__": "global.t1"},
                },
            )
        )

        text = catalog.export_namespace_yaml("tenant-Snap")
        import yaml as _yaml

        doc = _yaml.safe_load(text)

        # Story 18.2 — eight top-level keys in order (adds ``public`` after
        # ``shareable``).
        assert list(doc.keys()) == [
            "namespace",
            "user_id",
            "name",
            "description",
            "properties",
            "shareable",
            "public",
            "entries",
        ]

        # Snapshot — local kinds present (team + agent + prompt). No `_meta`.
        local_keys = [k for k in doc["entries"] if "." not in k]
        assert "team" in local_keys
        assert "agent-1" in local_keys
        assert "prompt-1" in local_keys
        assert "_meta" not in local_keys

        # Snapshot — external kinds present with composite keys.
        composite_keys = [k for k in doc["entries"] if "." in k]
        assert "global.m1" in composite_keys
        assert "global.t1" in composite_keys

        # Section header order: local Teams → local Agents → local Prompts →
        # external Tools (External ref) → external Models (External ref).
        from akgentic.catalog.serialization import (
            _EXTERNAL_KIND_HEADERS,
            _KIND_HEADERS,
        )

        teams_pos = text.index(_KIND_HEADERS["team"])
        agents_pos = text.index(_KIND_HEADERS["agent"])
        prompts_pos = text.index(_KIND_HEADERS["prompt"])
        ext_tools_pos = text.index(_EXTERNAL_KIND_HEADERS["tool"])
        ext_models_pos = text.index(_EXTERNAL_KIND_HEADERS["model"])
        assert teams_pos < agents_pos < prompts_pos < ext_tools_pos < ext_models_pos


# --- Story 17.10 — meta-only bundle round-trip -----------------------------------


_NAMESPACE_META_TYPE_BUNDLE = "akgentic.catalog.models.namespace_meta.NamespaceMeta"


class TestMetaOnlyBundle:
    """Round-trip a meta-only bundle (header + meta + library entries, no team)."""

    def test_meta_only_bundle_round_trip(
        self, catalog_factory: CatalogFactory, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Export a meta-only namespace and re-import it — byte-identical entries."""
        catalog, _ = catalog_factory()
        agent_type, _leaf_type = _register_agent_models(monkeypatch)

        ns = "meta-only-bundle"
        # Seed a meta entry (no team) as the namespace anchor.
        catalog.create(
            Entry(
                id="_meta",
                kind="meta",
                namespace=ns,
                user_id="anonymous",
                model_type=_NAMESPACE_META_TYPE_BUNDLE,
                description="Meta-only library",
                payload={
                    "name": "Meta Only Lib",
                    "description": "A library namespace",
                    "properties": {"tier": "community"},
                    "shareable": False,
                },
            )
        )
        # Seed a model entry.
        catalog.create(
            Entry(
                id="model-a",
                kind="model",
                namespace=ns,
                user_id="anonymous",
                model_type=agent_type,
                payload={"provider": "openai"},
            )
        )

        # Export
        yaml_text = catalog.export_namespace_yaml(ns)
        assert "model-a" in yaml_text
        assert "Meta Only Lib" in yaml_text

        # Delete all entries to prepare for import
        catalog.delete(ns, "model-a")
        catalog.delete(ns, "_meta")

        # Import
        imported = catalog.import_namespace_yaml(yaml_text)
        imported_ids = {e.id for e in imported}
        assert "model-a" in imported_ids

        # Verify meta is back with consistent user_id
        meta = catalog.get(ns, "_meta")
        assert meta.kind == "meta"
        assert meta.user_id == "anonymous"
        payload = meta.payload if isinstance(meta.payload, dict) else {}
        assert payload.get("name") == "Meta Only Lib"


# --- Story 18.1 — bundle wire-format with anonymous default ----------------------


class TestAnonymousBundleWireShape:
    """Story 18.1 / AC7 + AC11 — bundle export emits ``user_id: anonymous``;
    bundle import accepts legacy ``user_id: null`` and rewrites to ``"anonymous"``.
    """

    def test_export_emits_anonymous_not_null(self, catalog_factory: CatalogFactory) -> None:
        """Community-tier export of a default-owner namespace yields ``user_id: anonymous``."""
        catalog, _ = catalog_factory()
        ns = "anon-export"
        catalog.create(
            Entry(
                id="t",
                kind="team",
                namespace=ns,
                model_type=_TEAM_TYPE,
                payload=_team_payload(),
            )
        )
        yaml_text = catalog.export_namespace_yaml(ns)
        assert "user_id: anonymous" in yaml_text
        assert "user_id: null" not in yaml_text

    def test_legacy_bundle_with_user_id_null_round_trips(
        self, catalog_factory: CatalogFactory
    ) -> None:
        """A legacy bundle whose root ``user_id: null`` parses cleanly; resulting
        entries all have ``user_id == "anonymous"``; re-export emits ``anonymous``.
        """
        catalog, _ = catalog_factory()
        legacy_yaml = (
            "namespace: legacy-anon\n"
            "user_id: null\n"
            "entries:\n"
            "  t:\n"
            "    kind: team\n"
            f"    model_type: {_TEAM_TYPE}\n"
            "    parent_namespace: null\n"
            "    parent_id: null\n"
            "    description: ''\n"
            "    payload:\n"
            f"      name: {_team_payload()['name']}\n"
            f"      description: '{_team_payload()['description']}'\n"
            f"      members: {_team_payload()['members']!r}\n"
        )
        # The rendered ``members`` list literal is invalid YAML for an empty
        # list; simplify by hand-rolling a legacy bundle through dump_namespace
        # with a None-user_id sentinel rewritten via a string substitution.
        # Build the modern bundle then swap the root user_id back to null to
        # simulate a pre-Story-18.1 export.
        modern = dump_namespace(
            [
                Entry(
                    id="t",
                    kind="team",
                    namespace="legacy-anon",
                    user_id="anonymous",
                    model_type=_TEAM_TYPE,
                    payload=_team_payload(),
                )
            ]
        )
        legacy_yaml = modern.replace("user_id: anonymous", "user_id: null", 1)
        imported = catalog.import_namespace_yaml(legacy_yaml)
        # Every imported entry has user_id == "anonymous" — the legacy null was
        # silently rewritten before any Entry was constructed.
        assert all(e.user_id == "anonymous" for e in imported)
        # Re-export the namespace and confirm the wire shape now uses
        # ``anonymous`` (the catalog never writes ``null`` again).
        re_exported = catalog.export_namespace_yaml("legacy-anon")
        assert "user_id: anonymous" in re_exported
        assert "user_id: null" not in re_exported
