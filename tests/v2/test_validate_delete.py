"""Tests for ``akgentic.catalog.resolver.validate_delete`` — AC26 through AC29."""

from __future__ import annotations

from akgentic.catalog.resolver import validate_delete

from .conftest import FakeEntryRepository, make_entry


class TestNotFound:
    """AC26 — target missing → single-element list with ``"not found"``."""

    def test_missing_target_returns_not_found_message(self) -> None:
        repo = FakeEntryRepository()
        result = validate_delete("ns-1", "missing-id", repo)
        assert len(result) == 1
        assert "not found" in result[0]
        assert "ns-1" in result[0]
        assert "missing-id" in result[0]


class TestNoReferrers:
    """AC27 — existing entry with no inbound refs → empty list (safe to delete)."""

    def test_no_inbound_refs_returns_empty(self) -> None:
        repo = FakeEntryRepository()
        repo.put(make_entry(id="target", namespace="ns-1", payload={"v": 1}))
        result = validate_delete("ns-1", "target", repo)
        assert result == []


class TestOneReferrer:
    """AC28 — single inbound referrer → one blocker message."""

    def test_single_referrer_blocks(self) -> None:
        repo = FakeEntryRepository()
        repo.put(make_entry(id="target", namespace="ns-1", payload={"v": 1}))
        repo.put(
            make_entry(
                id="ref-1",
                kind="agent",
                namespace="ns-1",
                payload={"ptr": {"__ref__": "target"}},
            )
        )
        result = validate_delete("ns-1", "target", repo)
        assert len(result) == 1
        msg = result[0]
        assert "ref-1" in msg
        assert "agent" in msg
        assert "target" in msg
        assert "ns-1" in msg


class TestMultipleReferrers:
    """AC28 — multiple referrers → one message each, order mirrors find_references."""

    def test_multiple_referrers_all_listed(self) -> None:
        repo = FakeEntryRepository()
        repo.put(make_entry(id="target", namespace="ns-1", payload={"v": 1}))
        # Insert in a deterministic order — FakeEntryRepository.find_references
        # returns in insertion (dict) order.
        repo.put(
            make_entry(
                id="ref-a",
                kind="team",
                namespace="ns-1",
                payload={"ptr": {"__ref__": "target"}},
            )
        )
        repo.put(
            make_entry(
                id="ref-b",
                kind="agent",
                namespace="ns-1",
                payload={"ptr": {"__ref__": "target"}},
            )
        )
        result = validate_delete("ns-1", "target", repo)
        assert len(result) == 2
        # Order mirrors find_references' output (insertion order for the fake).
        assert "ref-a" in result[0]
        assert "team" in result[0]
        assert "ref-b" in result[1]
        assert "agent" in result[1]


class TestUniformRule:
    """AC29 — no branching on ``user_id`` or ``kind``."""

    def test_enterprise_target_with_user_inbound_blocks(self) -> None:
        """Enterprise entry (user_id="anonymous") with a user-owned inbound ref is blocked."""
        repo = FakeEntryRepository()
        repo.put(
            make_entry(id="target", namespace="ns-shared", user_id="anonymous", payload={"v": 1})
        )
        repo.put(
            make_entry(
                id="user-ref",
                kind="agent",
                namespace="ns-shared",
                user_id="user-42",
                payload={"ptr": {"__ref__": "target"}},
            )
        )
        result = validate_delete("ns-shared", "target", repo)
        assert len(result) == 1
        assert "user-ref" in result[0]
        assert "ns-shared" in result[0]

    def test_cross_namespace_inbound_is_not_consulted(self) -> None:
        """A ref in namespace M must never count against a delete in namespace N."""
        repo = FakeEntryRepository()
        repo.put(make_entry(id="target", namespace="ns-N", payload={"v": 1}))
        repo.put(
            make_entry(
                id="ref-in-M",
                kind="agent",
                namespace="ns-M",
                payload={"ptr": {"__ref__": "target"}},
            )
        )
        result = validate_delete("ns-N", "target", repo)
        assert result == []
