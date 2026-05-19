"""Tests for the v2 ``EntryQuery`` and ``CloneRequest`` models."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from akgentic.catalog.models.queries import CloneRequest, EntryQuery


class TestEntryQueryImport:
    """AC3 — EntryQuery imports and every field defaults to None."""

    def test_default_construction_all_none(self) -> None:
        query = EntryQuery()
        expected_fields = {
            "namespace",
            "kind",
            "id",
            "user_id",
            "user_id_set",
            "description_contains",
        }
        assert set(EntryQuery.model_fields.keys()) == expected_fields
        for name in expected_fields:
            assert getattr(query, name) is None, f"{name} should default to None"


class TestEntryQueryUserIdSetTriState:
    """AC3 — ``user_id_set`` accepts all three tri-state values."""

    @pytest.mark.parametrize("value", [None, True, False])
    def test_tri_state_values_validate(self, value: bool | None) -> None:
        query = EntryQuery(user_id_set=value)
        assert query.user_id_set is value


class TestEntryQueryFieldShapes:
    """Spot-checks on constraints inherited from the v2 NonEmptyStr alias."""

    def test_empty_namespace_rejected(self) -> None:
        with pytest.raises(ValidationError):
            EntryQuery(namespace="")

    def test_kind_literal_constrained(self) -> None:
        with pytest.raises(ValidationError):
            EntryQuery(kind="unknown-kind")  # type: ignore[arg-type]

    def test_description_contains_is_plain_str(self) -> None:
        # description_contains is a plain str | None — empty string is allowed
        # (substring containment against an empty string is a separate concern).
        query = EntryQuery(description_contains="")
        assert query.description_contains == ""


class TestCloneRequestImport:
    """AC4 — CloneRequest required/optional field set."""

    def test_minimal_clone_request(self) -> None:
        req = CloneRequest(src_namespace="ns-a", src_id="entry-1", dst_namespace="ns-b")
        assert req.src_namespace == "ns-a"
        assert req.src_id == "entry-1"
        assert req.dst_namespace == "ns-b"
        assert req.dst_user_id == "anonymous"

    def test_dst_user_id_accepts_value(self) -> None:
        req = CloneRequest(
            src_namespace="ns-a",
            src_id="entry-1",
            dst_namespace="ns-b",
            dst_user_id="alice",
        )
        assert req.dst_user_id == "alice"

    @pytest.mark.parametrize(
        "missing",
        ["src_namespace", "src_id", "dst_namespace"],
    )
    def test_required_fields_missing_raises(self, missing: str) -> None:
        kwargs: dict[str, str] = {
            "src_namespace": "ns-a",
            "src_id": "entry-1",
            "dst_namespace": "ns-b",
        }
        kwargs.pop(missing)
        with pytest.raises(ValidationError):
            CloneRequest(**kwargs)  # type: ignore[arg-type]

    def test_empty_dst_namespace_rejected(self) -> None:
        # dst_namespace must be NonEmptyStr — empty is rejected.
        with pytest.raises(ValidationError):
            CloneRequest(src_namespace="ns-a", src_id="entry-1", dst_namespace="")
