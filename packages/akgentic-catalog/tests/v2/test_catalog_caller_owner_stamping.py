"""Tests for Story 27.2 — catalog writes stamp the authenticated caller as owner.

ADR-028 §Decision 7: on write, when a caller identity is set
(``Catalog.as_caller``), the persisted entry's ``user_id`` IS the
authenticated caller — the incoming body / YAML ``user_id`` (and ``clone``'s
``dst_user_id``) is ignored for ownership. When no caller is set (community
tier, ``_caller_user_id`` contextvar is ``None``), behaviour is byte-unchanged:
``create`` / ``update`` keep the body ``user_id``; ``clone`` uses
``dst_user_id`` (default ``"anonymous"``); ``import_namespace_yaml`` keeps the
YAML per-entry ``user_id``.

Both branches of the stamping helper are exercised — caller-set (stamp fires)
and no-caller (community parity) — across YAML + Mongo backends via the
``catalog_factory`` fixture.
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import BaseModel

from akgentic.catalog.catalog import Catalog
from akgentic.catalog.models.entry import Entry

from .conftest import CatalogFactory, make_meta_entry, register_akgentic_test_module

_TEAM_TYPE = "akgentic.team.models.TeamCard"


class _Leaf(BaseModel):
    """Permissive leaf payload used for stamping tests."""

    provider: str = "openai"


@pytest.fixture
def leaf_type(monkeypatch: pytest.MonkeyPatch) -> str:
    module_name = register_akgentic_test_module(
        monkeypatch,
        "tests_fixture_27_2_caller_owner_stamping",
        Leaf=_Leaf,
    )
    return f"{module_name}.Leaf"


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


def _seed_team(catalog: Catalog, namespace: str, *, user_id: str = "anonymous") -> Entry:
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


def _leaf_entry(namespace: str, id: str, leaf_type: str, *, user_id: str = "anonymous") -> Entry:
    return Entry(
        id=id,
        kind="prompt",
        namespace=namespace,
        user_id=user_id,
        model_type=leaf_type,
        payload={"provider": "x"},
    )


# --- create ---------------------------------------------------------------


class TestStampOnCreate:
    def test_create_under_caller_stamps_owner(
        self, catalog_factory: CatalogFactory, leaf_type: str
    ) -> None:
        catalog, repo = catalog_factory()
        with Catalog.as_caller("gpiroux"):
            _seed_team(catalog, "ns")
            catalog.create(_leaf_entry("ns", "leaf", leaf_type, user_id="anonymous"))
        persisted = repo.get("ns", "leaf")
        assert persisted is not None
        assert persisted.user_id == "gpiroux"
        # Team entry is also stamped (AC6) — keeps the ownership anchor uniform.
        team = repo.get("ns", "team")
        assert team is not None
        assert team.user_id == "gpiroux"

    def test_create_no_caller_is_byte_unchanged(
        self, catalog_factory: CatalogFactory, leaf_type: str
    ) -> None:
        catalog, repo = catalog_factory()
        _seed_team(catalog, "ns")
        catalog.create(_leaf_entry("ns", "leaf", leaf_type, user_id="anonymous"))
        persisted = repo.get("ns", "leaf")
        assert persisted is not None
        assert persisted.user_id == "anonymous"

    def test_create_check_ownership_passes_under_caller(
        self, catalog_factory: CatalogFactory, leaf_type: str
    ) -> None:
        # A non-team/non-meta entry whose incoming user_id differs from the
        # caller still succeeds because the stamp fires BEFORE _check_ownership.
        catalog, repo = catalog_factory()
        with Catalog.as_caller("gpiroux"):
            _seed_team(catalog, "ns")
            catalog.create(_leaf_entry("ns", "leaf", leaf_type, user_id="someone-else"))
        persisted = repo.get("ns", "leaf")
        assert persisted is not None
        assert persisted.user_id == "gpiroux"


# --- update ---------------------------------------------------------------


class TestStampOnUpdate:
    def test_update_under_caller_stamps_owner(
        self, catalog_factory: CatalogFactory, leaf_type: str
    ) -> None:
        catalog, repo = catalog_factory()
        with Catalog.as_caller("gpiroux"):
            _seed_team(catalog, "ns")
            catalog.create(_leaf_entry("ns", "leaf", leaf_type, user_id="anonymous"))
            # Candidate carries a foreign user_id; the stamp must override it.
            candidate = _leaf_entry("ns", "leaf", leaf_type, user_id="someone-else")
            candidate = candidate.model_copy(update={"description": "updated"})
            catalog.update(candidate)
        persisted = repo.get("ns", "leaf")
        assert persisted is not None
        assert persisted.user_id == "gpiroux"

    def test_update_no_caller_keeps_candidate_user_id(
        self, catalog_factory: CatalogFactory, leaf_type: str
    ) -> None:
        catalog, repo = catalog_factory()
        # Seed a team owned by "owner" and a sub-entry owned by "owner".
        _seed_team(catalog, "ns", user_id="owner")
        catalog.create(_leaf_entry("ns", "leaf", leaf_type, user_id="owner"))
        candidate = _leaf_entry("ns", "leaf", leaf_type, user_id="owner").model_copy(
            update={"description": "updated"}
        )
        catalog.update(candidate)
        persisted = repo.get("ns", "leaf")
        assert persisted is not None
        assert persisted.user_id == "owner"


# --- clone ----------------------------------------------------------------


class TestStampOnClone:
    def test_clone_under_caller_supersedes_dst_user_id(
        self, catalog_factory: CatalogFactory, leaf_type: str
    ) -> None:
        catalog, repo = catalog_factory()
        # Source namespace owned by "gpiroux" so the caller can see it.
        with Catalog.as_caller("gpiroux"):
            _seed_team(catalog, "src")
            catalog.create(_leaf_entry("src", "leaf", leaf_type))
            _seed_team(catalog, "dst")
            # dst_user_id explicitly set to something else — caller supersedes it.
            catalog.clone("src", "leaf", "dst", dst_user_id="ignored-owner")
        cloned = repo.get("dst", "leaf")
        assert cloned is not None
        assert cloned.user_id == "gpiroux"

    def test_clone_no_caller_uses_dst_user_id(
        self, catalog_factory: CatalogFactory, leaf_type: str
    ) -> None:
        catalog, repo = catalog_factory()
        _seed_team(catalog, "src")
        catalog.create(_leaf_entry("src", "leaf", leaf_type))
        _seed_team(catalog, "dst")
        catalog.clone("src", "leaf", "dst", dst_user_id="bob")
        cloned = repo.get("dst", "leaf")
        assert cloned is not None
        assert cloned.user_id == "bob"

    def test_clone_no_caller_default_anonymous(
        self, catalog_factory: CatalogFactory, leaf_type: str
    ) -> None:
        catalog, repo = catalog_factory()
        _seed_team(catalog, "src")
        catalog.create(_leaf_entry("src", "leaf", leaf_type))
        _seed_team(catalog, "dst")
        catalog.clone("src", "leaf", "dst")
        cloned = repo.get("dst", "leaf")
        assert cloned is not None
        assert cloned.user_id == "anonymous"


# --- import_namespace_yaml ------------------------------------------------


class TestStampOnImport:
    def _seed_anonymous_namespace(self, catalog: Catalog, namespace: str, leaf_type: str) -> None:
        _seed_team(catalog, namespace, user_id="anonymous")
        catalog.create(make_meta_entry(namespace, shareable=False, public=False))
        catalog.create(_leaf_entry(namespace, "leaf", leaf_type, user_id="anonymous"))

    def test_import_under_caller_stamps_every_entry_and_meta(
        self, catalog_factory: CatalogFactory, leaf_type: str
    ) -> None:
        catalog, repo = catalog_factory()
        # Build a bundle from an anonymous-owned namespace (the reported bug).
        self._seed_anonymous_namespace(catalog, "src", leaf_type)
        bundle = catalog.export_namespace_yaml("src")

        # Re-import the same bundle (same namespace) under a caller.
        with Catalog.as_caller("gpiroux"):
            persisted = catalog.import_namespace_yaml(bundle)

        for entry in persisted:
            assert entry.user_id == "gpiroux", f"{entry.id} not stamped"
        # The hoisted _meta entry (upserted) is also caller-owned.
        meta = repo.get("src", "_meta")
        assert meta is not None
        assert meta.user_id == "gpiroux"
        # And the persisted team / leaf in the repository.
        team = repo.get("src", "team")
        leaf = repo.get("src", "leaf")
        assert team is not None and team.user_id == "gpiroux"
        assert leaf is not None and leaf.user_id == "gpiroux"

    def test_import_no_caller_keeps_yaml_user_id(
        self, catalog_factory: CatalogFactory, leaf_type: str
    ) -> None:
        catalog, repo = catalog_factory()
        self._seed_anonymous_namespace(catalog, "src", leaf_type)
        bundle = catalog.export_namespace_yaml("src")
        persisted = catalog.import_namespace_yaml(bundle)
        for entry in persisted:
            assert entry.user_id == "anonymous"
        meta = repo.get("src", "_meta")
        assert meta is not None
        assert meta.user_id == "anonymous"

    def test_import_bundle_uniformity_preserved_under_caller(
        self, catalog_factory: CatalogFactory, leaf_type: str
    ) -> None:
        # A multi-entry bundle whose YAML entries carry mixed/anonymous owners
        # still passes _validate_bundle_invariants under a caller (all stamped
        # to the caller first), and persists successfully.
        catalog, repo = catalog_factory()
        _seed_team(catalog, "src", user_id="anonymous")
        catalog.create(make_meta_entry("src", shareable=False, public=False))
        catalog.create(_leaf_entry("src", "leaf-a", leaf_type, user_id="anonymous"))
        catalog.create(_leaf_entry("src", "leaf-b", leaf_type, user_id="anonymous"))
        bundle = catalog.export_namespace_yaml("src")

        with Catalog.as_caller("gpiroux"):
            persisted = catalog.import_namespace_yaml(bundle)

        ids = {e.id for e in persisted}
        assert {"team", "leaf-a", "leaf-b"} <= ids
        for e in persisted:
            assert e.user_id == "gpiroux"
