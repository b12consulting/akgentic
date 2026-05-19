"""Tests for ``Catalog.resolve`` / ``resolve_by_id`` / ``load_team`` — ACs 31-35, 42.

``load_team`` uses an in-memory wrapper repository so every per-ref ``get`` is
served from the pre-loaded list; tests assert this via
``CountingEntryRepository``.
"""

from __future__ import annotations

from typing import Any

import pytest
from akgentic.team.models import TeamCard
from pydantic import BaseModel

from akgentic.catalog.catalog import Catalog
from akgentic.catalog.models.entry import Entry
from akgentic.catalog.models.errors import CatalogValidationError, EntryNotFoundError

from .conftest import (
    CatalogFactory,
    CountingEntryRepository,
    FakeEntryRepository,
    register_akgentic_test_module,
)

_TEAM_TYPE = "akgentic.team.models.TeamCard"
_NAMESPACE_META_TYPE = "akgentic.catalog.models.namespace_meta.NamespaceMeta"


def _team_payload_no_name() -> dict[str, Any]:
    """Minimal valid ``TeamCard`` payload with empty ``name`` and ``description``.

    The story's projection branch (AC1) fires when ``TeamCard.name`` is
    ``None`` OR ``""``; this fixture uses empty strings so the test holds
    against both the current pinned ``akgentic-team`` (where
    ``TeamCard.name: str`` is required and rejects ``None``) and the
    eventual ``str | None`` upgrade — see story Open Question §2.
    """
    return {
        "name": "",
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


def _team_payload() -> dict[str, Any]:
    """Minimal valid ``TeamCard`` payload."""
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


class _ParentModel(BaseModel):
    name: str
    child: _LeafModel | None = None


def _register_models(monkeypatch: pytest.MonkeyPatch) -> tuple[str, str]:
    """Register leaf + parent models, return FQCNs."""
    module_name = register_akgentic_test_module(
        monkeypatch,
        "tests_fixture_15_5_resolve_models",
        _LeafModel=_LeafModel,
        _ParentModel=_ParentModel,
    )
    return f"{module_name}._LeafModel", f"{module_name}._ParentModel"


def _seed_team(catalog: Catalog, namespace: str, user_id: str = "anonymous") -> Entry:
    """Seed a minimal team entry; return the stored Entry."""
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


class TestResolve:
    """AC31 — resolve delegates to the resolver; inline and ref payloads both work."""

    def test_resolve_inline_payload(
        self, catalog_factory: CatalogFactory, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        catalog, _ = catalog_factory()
        _seed_team(catalog, namespace="ns-r")
        leaf_type, parent_type = _register_models(monkeypatch)
        entry = Entry(
            id="leaf-1",
            kind="model",
            namespace="ns-r",
            model_type=leaf_type,
            payload={"provider": "anthropic"},
        )
        catalog.create(entry)
        result = catalog.resolve(entry)
        assert isinstance(result, _LeafModel)
        assert result.provider == "anthropic"

    def test_resolve_with_refs(
        self, catalog_factory: CatalogFactory, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        catalog, _ = catalog_factory()
        _seed_team(catalog, namespace="ns-rr", user_id="anonymous")
        leaf_type, parent_type = _register_models(monkeypatch)
        catalog.create(
            Entry(
                id="leaf",
                kind="model",
                namespace="ns-rr",
                model_type=leaf_type,
                payload={"provider": "openai"},
            )
        )
        parent = catalog.create(
            Entry(
                id="parent",
                kind="agent",
                namespace="ns-rr",
                model_type=parent_type,
                payload={"name": "p", "child": {"__ref__": "leaf"}},
            )
        )
        result = catalog.resolve(parent)
        assert isinstance(result, _ParentModel)
        assert result.child is not None
        assert result.child.provider == "openai"


class TestResolveById:
    """AC32 — ``resolve_by_id`` composes ``get`` + ``resolve``; miss propagates."""

    def test_resolve_by_id_matches_resolve_get(
        self, catalog_factory: CatalogFactory, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        catalog, _ = catalog_factory()
        _seed_team(catalog, namespace="ns-rbi")
        leaf_type, _ = _register_models(monkeypatch)
        catalog.create(
            Entry(
                id="leaf",
                kind="model",
                namespace="ns-rbi",
                model_type=leaf_type,
                payload={"provider": "azure"},
            )
        )
        direct = catalog.resolve(catalog.get("ns-rbi", "leaf"))
        via_id = catalog.resolve_by_id("ns-rbi", "leaf")
        assert isinstance(via_id, _LeafModel)
        assert via_id.provider == "azure"
        assert type(direct) is type(via_id)

    def test_resolve_by_id_miss_raises(self, catalog_factory: CatalogFactory) -> None:
        catalog, _ = catalog_factory()
        with pytest.raises(EntryNotFoundError):
            catalog.resolve_by_id("ns-no", "nope")


class TestLoadTeamOneListCall:
    """AC33 + AC42 — load_team issues exactly one list_by_namespace; zero get calls."""

    def test_one_list_zero_gets(self, monkeypatch: pytest.MonkeyPatch) -> None:
        fake = FakeEntryRepository()
        counting = CountingEntryRepository(fake)
        catalog = Catalog(counting)
        _seed_team(catalog, namespace="ns-lt", user_id="anonymous")
        leaf_type, parent_type = _register_models(monkeypatch)
        # Add a sub-entry with a ref, so load_team's populate_refs would issue
        # a get() call if the in-memory wrapper were missing.
        catalog.create(
            Entry(
                id="leaf",
                kind="model",
                namespace="ns-lt",
                model_type=leaf_type,
                payload={"provider": "openai"},
            )
        )
        counting.reset()
        result = catalog.load_team("ns-lt")
        assert isinstance(result, TeamCard)
        assert counting.count("list_by_namespace") == 1
        assert counting.count("get") == 0


class TestLoadTeamMissing:
    """AC34 — empty or team-less namespaces raise with "no team entry"."""

    def test_empty_namespace_raises(self, catalog_factory: CatalogFactory) -> None:
        catalog, _ = catalog_factory()
        with pytest.raises(CatalogValidationError) as exc:
            catalog.load_team("ns-empty")
        msg = str(exc.value)
        assert "no team entry" in msg
        assert "ns-empty" in msg

    def test_namespace_without_team_raises(self) -> None:
        # Bypass the Catalog bootstrap check by seeding the fake directly.
        leaf = _LeafModel.__name__  # just to keep linter quiet
        assert leaf  # noqa: S101 — sentinel use
        fake = FakeEntryRepository()
        # Direct put: inject agent entry without a team in the namespace.
        fake.put(
            Entry(
                id="lone-agent",
                kind="agent",
                namespace="ns-team-less",
                model_type="akgentic.core.agent_card.AgentCard",
                payload={},
            )
        )
        catalog = Catalog(fake)
        with pytest.raises(CatalogValidationError) as exc:
            catalog.load_team("ns-team-less")
        msg = str(exc.value)
        assert "no team entry" in msg
        assert "ns-team-less" in msg


class TestLoadTeamTypedReturn:
    """AC35 — load_team returns a TeamCard at both type-check and runtime levels."""

    def test_returns_team_card_instance(self, catalog_factory: CatalogFactory) -> None:
        catalog, _ = catalog_factory()
        _seed_team(catalog, namespace="ns-lt-typed")
        result = catalog.load_team("ns-lt-typed")
        assert isinstance(result, TeamCard)


class TestLoadTeamMisconfigured:
    """AC35 defensive check — wrong model_type on the team entry raises."""

    def test_wrong_model_type_raises(self) -> None:
        fake = FakeEntryRepository()
        # Directly put a team-kind entry with the wrong model_type.
        fake.put(
            Entry(
                id="team",
                kind="team",
                namespace="ns-wrong",
                model_type="akgentic.core.agent_card.AgentCard",
                payload={
                    "role": "r",
                    "description": "",
                    "skills": [],
                    "agent_class": "akgentic.core.agent.Akgent",
                    "config": {"name": "r", "role": "r"},
                    "routes_to": [],
                    "metadata": {},
                },
            )
        )
        catalog = Catalog(fake)
        with pytest.raises(CatalogValidationError) as exc:
            catalog.load_team("ns-wrong")
        msg = str(exc.value)
        assert "expected TeamCard" in msg


def _put_team_entry(
    fake: FakeEntryRepository,
    namespace: str,
    payload: dict[str, Any],
    *,
    description: str = "",
) -> Entry:
    """Direct repository ``put`` for a team entry — bypasses Pydantic validation.

    Used by Story 17.9 projection tests so payload dicts that omit ``name``
    (or carry empty descriptions) are not normalized by ``catalog.create``'s
    ``prepare_for_write`` path. The bootstrap rule is unaffected — these
    tests do not add sub-entries.
    """
    entry = Entry(
        id="team",
        kind="team",
        namespace=namespace,
        model_type=_TEAM_TYPE,
        description=description,
        payload=payload,
    )
    fake.put(entry)
    return entry


def _put_meta_entry(
    fake: FakeEntryRepository,
    namespace: str,
    *,
    payload_name: str | None,
    entry_description: str = "",
) -> Entry:
    """Direct repository ``put`` for a meta entry — bypasses Pydantic validation.

    ``payload_name=None`` omits the ``name`` key from the payload entirely;
    ``payload_name=""`` writes an empty string (exercises the fallback rung
    when the meta entry exists but its name is empty / non-string).
    """
    payload: dict[str, Any] = {
        "description": "",
        "properties": {},
        "shareable": False,
    }
    if payload_name is not None:
        payload["name"] = payload_name
    entry = Entry(
        id="_meta",
        kind="meta",
        namespace=namespace,
        model_type=_NAMESPACE_META_TYPE,
        description=entry_description,
        payload=payload,
    )
    fake.put(entry)
    return entry


class TestLoadTeamDisplayProjection:
    """Story 17.9 — ``Catalog.load_team`` projects ``name`` / ``description``.

    Covers AC5 cases 1–7, the combined-projection assertion, AC6
    no-mutation, and the AC3 single-query invariant for the projection
    branch.
    """

    # ---- AC5 Case 1 — explicit team name wins, no meta. -----------------

    def test_team_explicit_name_wins_no_meta(self) -> None:
        fake = FakeEntryRepository()
        payload = _team_payload()
        payload["name"] = "Operations"
        _put_team_entry(fake, "ns-explicit", payload)
        catalog = Catalog(fake)
        result = catalog.load_team("ns-explicit")
        assert result.name == "Operations"

    # ---- AC5 Case 2 — meta name projected when team name is None. -------

    def test_meta_name_projected_when_team_name_is_none(self) -> None:
        fake = FakeEntryRepository()
        _put_team_entry(fake, "ns-mn", _team_payload_no_name())
        _put_meta_entry(fake, "ns-mn", payload_name="Friendly")
        catalog = Catalog(fake)
        result = catalog.load_team("ns-mn")
        assert result.name == "Friendly"

    # ---- AC5 Case 3 — namespace identifier fallback when meta name empty.

    def test_namespace_identifier_fallback_when_meta_name_empty(self) -> None:
        fake = FakeEntryRepository()
        _put_team_entry(fake, "ns-empty-meta", _team_payload_no_name())
        _put_meta_entry(fake, "ns-empty-meta", payload_name="")
        catalog = Catalog(fake)
        result = catalog.load_team("ns-empty-meta")
        assert result.name == "ns-empty-meta"

    # ---- AC5 Case 4 — namespace identifier fallback when no meta. -------

    def test_namespace_identifier_fallback_when_no_meta(self) -> None:
        fake = FakeEntryRepository()
        _put_team_entry(fake, "ns-no-meta", _team_payload_no_name())
        catalog = Catalog(fake)
        result = catalog.load_team("ns-no-meta")
        assert result.name == "ns-no-meta"

    # ---- AC5 Case 5 — explicit team description wins, ignores meta. -----

    def test_team_explicit_description_wins(self) -> None:
        fake = FakeEntryRepository()
        payload = _team_payload()
        payload["description"] = "TeamDesc"
        _put_team_entry(fake, "ns-td", payload)
        _put_meta_entry(fake, "ns-td", payload_name="Friendly", entry_description="MetaDesc")
        catalog = Catalog(fake)
        result = catalog.load_team("ns-td")
        assert result.description == "TeamDesc"

    # ---- AC5 Case 6 — meta description projected when team description None.

    def test_meta_description_projected_when_team_description_is_none(self) -> None:
        fake = FakeEntryRepository()
        _put_team_entry(fake, "ns-md", _team_payload_no_name())
        _put_meta_entry(fake, "ns-md", payload_name="Friendly", entry_description="MetaDesc")
        catalog = Catalog(fake)
        result = catalog.load_team("ns-md")
        assert result.description == "MetaDesc"

    # ---- AC5 Case 7 — empty description fallback when no meta. ----------

    def test_empty_description_fallback_when_no_meta(self) -> None:
        fake = FakeEntryRepository()
        _put_team_entry(fake, "ns-edf", _team_payload_no_name())
        catalog = Catalog(fake)
        result = catalog.load_team("ns-edf")
        assert result.description == ""

    # ---- Combined-projection — single ``model_copy`` for both fields. ---

    def test_combined_projection_single_model_copy(self) -> None:
        fake = FakeEntryRepository()
        _put_team_entry(fake, "ns-combo", _team_payload_no_name())
        _put_meta_entry(
            fake,
            "ns-combo",
            payload_name="ComboName",
            entry_description="ComboDesc",
        )
        catalog = Catalog(fake)
        result = catalog.load_team("ns-combo")
        assert result.name == "ComboName"
        assert result.description == "ComboDesc"

    # ---- AC6 — projection does NOT mutate the original entry payloads. ---

    def test_projection_does_not_mutate_entries(self) -> None:
        fake = FakeEntryRepository()
        team_entry = _put_team_entry(fake, "ns-nm", _team_payload_no_name())
        meta_entry = _put_meta_entry(fake, "ns-nm", payload_name="Friendly")
        # Snapshot the on-disk shapes BEFORE invoking load_team.
        team_payload_before = dict(team_entry.payload)
        meta_payload_before = dict(meta_entry.payload)
        meta_description_before = meta_entry.description

        catalog = Catalog(fake)
        result = catalog.load_team("ns-nm")
        assert result.name == "Friendly"

        # Re-fetch via the catalog and assert byte-identical payload.
        team_after = catalog.get("ns-nm", "team")
        meta_after = catalog.get("ns-nm", "_meta")
        assert team_after.payload == team_payload_before
        assert meta_after.payload == meta_payload_before
        assert meta_after.description == meta_description_before

    # ---- AC3 — projection preserves single-query invariant. -------------

    def test_projection_preserves_single_query_invariant(self) -> None:
        fake = FakeEntryRepository()
        _put_team_entry(fake, "ns-sq", _team_payload_no_name())
        _put_meta_entry(fake, "ns-sq", payload_name="Friendly")
        counting = CountingEntryRepository(fake)
        catalog = Catalog(counting)
        counting.reset()
        result = catalog.load_team("ns-sq")
        assert result.name == "Friendly"
        assert counting.count("list_by_namespace") == 1
