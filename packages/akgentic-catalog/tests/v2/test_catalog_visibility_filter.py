"""Tests for Story 18.4 — caller-identity contextvar and visibility filtering.

Covers the four-state visibility filter rule (ADR-009 §D2) on the four
boundary methods (``Catalog.list``, ``Catalog.get``, ``Catalog.clone`` —
plus the ``caller=None`` no-filtering path that preserves community-tier
behaviour byte-identically).

The contextvar composition tests (nesting, asyncio isolation, empty
``user_id`` rejection) live alongside the boundary-method tests because
they probe the same surface — ``Catalog.as_caller`` is the only public
write path for the contextvar, and the four-state filter is the only
public read path.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from akgentic.catalog.catalog import Catalog, _caller_user_id
from akgentic.catalog.models.entry import Entry
from akgentic.catalog.models.errors import CatalogValidationError, EntryNotFoundError
from akgentic.catalog.models.queries import EntryQuery

from .conftest import FakeEntryRepository, make_meta_entry

_TEAM_TYPE = "akgentic.team.models.TeamCard"
_PROMPT_TYPE = "akgentic.catalog.tests_fixture_18_4_prompt._Prompt"


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


def _seed_team(catalog: Catalog, namespace: str, user_id: str = "anonymous") -> None:
    """Seed a minimal team entry whose ``user_id`` anchors the namespace."""
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


def _put_meta(
    catalog: Catalog,
    namespace: str,
    *,
    public: bool,
    user_id: str = "anonymous",
) -> None:
    """Persist a meta entry with the given ``public`` flag (no team check)."""
    catalog._repository.put(
        make_meta_entry(namespace, public=public, user_id=user_id, name=namespace)
    )


def _make_prompt_entry(
    namespace: str,
    id: str,
    *,
    user_id: str,
) -> Entry:
    """Build a minimal prompt entry with a trivial payload (no refs)."""
    return Entry(
        id=id,
        kind="prompt",
        namespace=namespace,
        user_id=user_id,
        model_type="akgentic.catalog.models.namespace_meta.NamespaceMeta",
        payload={"name": id, "description": "", "properties": {}},
    )


def _seed_three_namespaces(catalog: Catalog) -> None:
    """Seed: tenant-A (alice), tenant-B (bob), global (admin, public=True).

    Each namespace gets a team entry plus one prompt entry. Used by
    ``TestVisibilityFilterList`` / ``TestVisibilityFilterGet`` as a
    canonical multi-tenant fixture.
    """
    _seed_team(catalog, "tenant-A", user_id="alice")
    catalog.create(_make_prompt_entry("tenant-A", "p-A", user_id="alice"))
    _seed_team(catalog, "tenant-B", user_id="bob")
    catalog.create(_make_prompt_entry("tenant-B", "p-B", user_id="bob"))
    _seed_team(catalog, "global", user_id="admin")
    catalog.create(make_meta_entry("global", public=True, user_id="admin", name="global"))
    catalog.create(_make_prompt_entry("global", "p-G", user_id="admin"))


# --- Disabled filter: caller is None preserves community-tier behaviour --------


class TestVisibilityFilterDisabled:
    """When the contextvar is ``None``, every method behaves byte-identically to today."""

    def test_no_caller_means_no_filtering(self) -> None:
        """Replaces the deleted Story 18.2 AC8 lock test.

        With ``_caller_user_id`` at its ``None`` default, ``Catalog.list``
        / ``get`` / ``clone`` all return / accept every entry regardless
        of public flag — community-tier byte-identical contract.
        """
        catalog = Catalog(FakeEntryRepository())
        _seed_three_namespaces(catalog)

        assert _caller_user_id.get() is None

        # list returns every prompt regardless of public/owner.
        rows = catalog.list(EntryQuery(kind="prompt"))
        ids = sorted(e.id for e in rows)
        assert ids == ["p-A", "p-B", "p-G"]

        # get on a private non-public namespace owned by another user
        # still returns the entry.
        entry = catalog.get("tenant-B", "p-B")
        assert entry.id == "p-B"

        # clone from a private namespace is permitted.
        cloned = catalog.clone("tenant-B", "p-B", "tenant-A", dst_user_id="alice")
        assert cloned.namespace == "tenant-A"


# --- list filter -------------------------------------------------------------


class TestVisibilityFilterList:
    """``Catalog.list`` filters by (owner OR public-namespace) when caller is set."""

    def test_list_returns_owner_entries_when_caller_is_set(self) -> None:
        catalog = Catalog(FakeEntryRepository())
        _seed_three_namespaces(catalog)
        with Catalog.as_caller("alice"):
            rows = catalog.list(EntryQuery(kind="prompt"))
        ids = sorted(e.id for e in rows)
        # alice owns p-A; admin owns p-G in public namespace; bob's p-B
        # is private to bob.
        assert "p-A" in ids
        assert "p-B" not in ids

    def test_list_returns_public_namespace_entries_when_caller_is_set(self) -> None:
        catalog = Catalog(FakeEntryRepository())
        _seed_three_namespaces(catalog)
        with Catalog.as_caller("alice"):
            rows = catalog.list(EntryQuery(kind="prompt"))
        ids = sorted(e.id for e in rows)
        # p-G is in the public global namespace; alice (non-owner) sees it.
        assert "p-G" in ids

    def test_list_excludes_private_other_owner_entries(self) -> None:
        catalog = Catalog(FakeEntryRepository())
        _seed_three_namespaces(catalog)
        with Catalog.as_caller("alice"):
            rows = catalog.list(EntryQuery(kind="prompt"))
        ids = {e.id for e in rows}
        assert "p-B" not in ids  # bob's private prompt invisible to alice

    def test_list_combines_owner_and_public_entries_in_one_query(self) -> None:
        catalog = Catalog(FakeEntryRepository())
        _seed_three_namespaces(catalog)
        with Catalog.as_caller("alice"):
            rows = catalog.list(EntryQuery(kind="prompt"))
        ids = sorted(e.id for e in rows)
        assert ids == ["p-A", "p-G"]

    def test_list_filter_no_op_when_query_user_id_matches_caller(self) -> None:
        """When ``query.user_id == caller``, the visibility filter admits every match."""
        catalog = Catalog(FakeEntryRepository())
        _seed_three_namespaces(catalog)
        with Catalog.as_caller("alice"):
            rows = catalog.list(EntryQuery(user_id="alice"))
        # alice's team + alice's prompt — no admin / bob entries leak.
        owners = {e.user_id for e in rows}
        assert owners == {"alice"}


# --- get filter --------------------------------------------------------------


class TestVisibilityFilterGet:
    """``Catalog.get`` returns / hides entries per the four-state rule."""

    def test_get_returns_entry_for_owner(self) -> None:
        catalog = Catalog(FakeEntryRepository())
        _seed_three_namespaces(catalog)
        with Catalog.as_caller("alice"):
            entry = catalog.get("tenant-A", "p-A")
        assert entry.id == "p-A"

    def test_get_returns_entry_in_public_namespace_for_non_owner(self) -> None:
        catalog = Catalog(FakeEntryRepository())
        _seed_three_namespaces(catalog)
        with Catalog.as_caller("alice"):
            entry = catalog.get("global", "p-G")
        assert entry.id == "p-G"

    def test_get_raises_not_found_for_private_other_owner(self) -> None:
        catalog = Catalog(FakeEntryRepository())
        _seed_three_namespaces(catalog)
        with Catalog.as_caller("alice"):
            with pytest.raises(EntryNotFoundError, match="not found"):
                catalog.get("tenant-B", "p-B")

    def test_get_meta_entry_visible_when_namespace_public(self) -> None:
        """Public namespace's meta entry is visible to non-owners.

        Denying access to the meta entry would create an unreachable
        invariant — the visibility flag is what makes the namespace
        public, so reading it must always be allowed in that case.
        """
        catalog = Catalog(FakeEntryRepository())
        _seed_three_namespaces(catalog)
        with Catalog.as_caller("alice"):
            meta = catalog.get("global", "_meta")
        assert meta.kind == "meta"

    def test_get_message_does_not_leak_existence_to_non_owner(self) -> None:
        """Missing-target and exists-but-invisible MUST share the same error message."""
        catalog = Catalog(FakeEntryRepository())
        _seed_three_namespaces(catalog)

        # exists but invisible to alice (bob's private prompt)
        with Catalog.as_caller("alice"):
            with pytest.raises(EntryNotFoundError) as exists_invisible:
                catalog.get("tenant-B", "p-B")

        # genuinely does not exist
        with Catalog.as_caller("alice"):
            with pytest.raises(EntryNotFoundError) as truly_missing:
                catalog.get("tenant-B", "no-such-id")

        # both messages share the substring "not found" and follow the
        # same shape — no information leak about existence.
        assert "not found" in str(exists_invisible.value)
        assert "not found" in str(truly_missing.value)


# --- clone filter ------------------------------------------------------------


class TestVisibilityFilterClone:
    """``Catalog.clone`` requires source ownership OR public source namespace."""

    def test_clone_succeeds_for_owner(self) -> None:
        catalog = Catalog(FakeEntryRepository())
        _seed_three_namespaces(catalog)
        with Catalog.as_caller("alice"):
            cloned = catalog.clone("tenant-A", "p-A", "tenant-A", dst_user_id="alice")
        # Same-namespace clone — id receives a numeric suffix.
        assert cloned.id == "p-A-2"
        assert cloned.namespace == "tenant-A"
        assert cloned.user_id == "alice"

    def test_clone_succeeds_from_public_namespace_for_non_owner(self) -> None:
        """Canonical use case: tenant clones from public ``global`` namespace."""
        catalog = Catalog(FakeEntryRepository())
        _seed_three_namespaces(catalog)
        with Catalog.as_caller("alice"):
            cloned = catalog.clone("global", "p-G", "tenant-A", dst_user_id="alice")
        assert cloned.id == "p-G"
        assert cloned.namespace == "tenant-A"
        assert cloned.user_id == "alice"

    def test_clone_rejected_from_private_other_owner_namespace(self) -> None:
        catalog = Catalog(FakeEntryRepository())
        _seed_three_namespaces(catalog)
        with Catalog.as_caller("alice"):
            with pytest.raises(CatalogValidationError, match="may not clone source"):
                catalog.clone("tenant-B", "p-B", "tenant-A", dst_user_id="alice")

    def test_clone_existence_check_fires_before_visibility_check(self) -> None:
        """A deleted source surfaces ``EntryNotFoundError``, not ``CatalogValidationError``."""
        catalog = Catalog(FakeEntryRepository())
        _seed_three_namespaces(catalog)
        with Catalog.as_caller("alice"):
            with pytest.raises(EntryNotFoundError):
                catalog.clone("tenant-B", "no-such-id", "tenant-A", dst_user_id="alice")


# --- contextvar composition --------------------------------------------------


class TestVisibilityFilterContextvarComposition:
    """``Catalog.as_caller`` semantics: nesting, asyncio isolation, validation."""

    def test_nested_as_caller_restores_outer_caller(self) -> None:
        """Nested ``as_caller`` blocks compose correctly via token-based reset."""
        assert _caller_user_id.get() is None
        with Catalog.as_caller("alice"):
            assert _caller_user_id.get() == "alice"
            with Catalog.as_caller("bob"):
                assert _caller_user_id.get() == "bob"
            assert _caller_user_id.get() == "alice"
        assert _caller_user_id.get() is None

    async def test_concurrent_asyncio_tasks_see_isolated_callers(self) -> None:
        """Two concurrent tasks with different callers MUST observe their own identity."""

        async def _capture_caller(user_id: str) -> str | None:
            with Catalog.as_caller(user_id):
                # Yield control so both tasks interleave through the
                # contextvar set/reset; each task carries its own
                # snapshot per Python contextvars semantics.
                await asyncio.sleep(0)
                return _caller_user_id.get()

        results = await asyncio.gather(
            _capture_caller("alice"),
            _capture_caller("bob"),
        )
        assert sorted(results) == ["alice", "bob"]  # type: ignore[list-item]

    def test_as_caller_rejects_empty_user_id(self) -> None:
        with pytest.raises(ValueError, match="non-empty"):
            with Catalog.as_caller(""):
                pass

    def test_visible_to_caller_helper_branches(self) -> None:
        """Direct helper coverage — owner / public-ns / private-non-owner."""
        catalog = Catalog(FakeEntryRepository())
        _seed_three_namespaces(catalog)

        # owner branch
        own = _make_prompt_entry("tenant-A", "p-other", user_id="alice")
        assert catalog._visible_to_caller(own, "alice") is True

        # public-namespace branch (entry owned by admin, namespace public)
        public_entry = catalog._repository.get("global", "p-G")
        assert public_entry is not None
        assert catalog._visible_to_caller(public_entry, "alice") is True

        # private + non-owner branch
        bob_entry = catalog._repository.get("tenant-B", "p-B")
        assert bob_entry is not None
        assert catalog._visible_to_caller(bob_entry, "alice") is False
