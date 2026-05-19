"""Tests for ``akgentic.catalog.resolver.populate_refs``.

Story 15.2 established the cycle / missing-target / ``__type__``-mismatch
semantics (AC3 through AC12). Story 15.6 extended the splice semantics: a
ref-marker position now resolves to a **typed Pydantic instance** built from
the referenced entry's ``model_type`` (not a raw dict). These tests cover
both — the 15.2 error-path contracts are preserved verbatim (substring-stable
messages), the 15.6 happy paths now assert on model instances and use a
permissive ``Anything`` test model (``extra="allow"``) so bare payloads
validate and round-trip via ``model_dump`` for equality checks.
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import BaseModel, ConfigDict

from akgentic.catalog.models.errors import CatalogValidationError
from akgentic.catalog.resolver import populate_refs

from .conftest import FakeEntryRepository, make_entry, register_akgentic_test_module


class Anything(BaseModel):
    """Permissive test model — ``extra='allow'`` so any payload validates.

    Used as the ``model_type`` for ref targets in populate_refs tests that
    want to exercise splice semantics without asserting on a specific
    subclass shape. ``model_dump()`` round-trips the input dict, so existing
    dict-equality assertions port cleanly to ``assert result.model_dump() == ...``.
    """

    model_config = ConfigDict(extra="allow")


@pytest.fixture
def anything_model_type(monkeypatch: pytest.MonkeyPatch) -> str:
    """Register ``Anything`` under an ``akgentic.*`` module and return its FQCN."""
    module_name = register_akgentic_test_module(
        monkeypatch, "tests_fixture_15_6_populate_refs_anything", Anything=Anything
    )
    return f"{module_name}.Anything"


class TestLeafPassthrough:
    """Non-dict / non-list leaves pass through unchanged."""

    @pytest.mark.parametrize(
        "leaf",
        ["text", 42, 3.14, True, False, None],
    )
    def test_scalar_leaves_identity(self, leaf: Any) -> None:
        repo = FakeEntryRepository()
        result = populate_refs(leaf, repo, "ns-1")
        assert result == leaf
        assert type(result) is type(leaf)

    def test_empty_dict(self) -> None:
        repo = FakeEntryRepository()
        result = populate_refs({}, repo, "ns-1")
        assert result == {}

    def test_empty_list(self) -> None:
        repo = FakeEntryRepository()
        result = populate_refs([], repo, "ns-1")
        assert result == []


class TestDictRecursion:
    """Plain dicts recurse into values and build a fresh container."""

    def test_plain_dict_unchanged_values(self) -> None:
        repo = FakeEntryRepository()
        node = {"provider": "openai", "model": "gpt-4.1"}
        result = populate_refs(node, repo, "ns-1")
        assert result == node
        # Recursion builds a fresh dict — input is not mutated.
        node["provider"] = "mutated"
        assert result["provider"] == "openai"

    def test_nested_dicts_recurse(self) -> None:
        repo = FakeEntryRepository()
        node = {"outer": {"inner": {"deep": 1}}}
        result = populate_refs(node, repo, "ns-1")
        assert result == {"outer": {"inner": {"deep": 1}}}
        # Distinct containers at each level.
        assert result is not node
        assert result["outer"] is not node["outer"]


class TestListRecursion:
    """Lists recurse element-wise and build a fresh container."""

    def test_mixed_list(self, anything_model_type: str) -> None:
        repo = FakeEntryRepository()
        repo.put(
            make_entry(id="x", namespace="ns-1", model_type=anything_model_type, payload={"k": 1})
        )
        node = [{"__ref__": "x"}, "literal", 42]
        result = populate_refs(node, repo, "ns-1")
        assert len(result) == 3
        assert isinstance(result[0], Anything)
        assert result[0].model_dump() == {"k": 1}
        assert result[1] == "literal"
        assert result[2] == 42

    def test_nested_lists(self) -> None:
        repo = FakeEntryRepository()
        node: Any = [[1, 2], [3, [4, 5]]]
        result = populate_refs(node, repo, "ns-1")
        assert result == [[1, 2], [3, [4, 5]]]
        assert result is not node


class TestRefReplacement:
    """Story 15.6 — ref dict replaced by a typed instance of target's model_type."""

    def test_flat_ref_replaced(self, anything_model_type: str) -> None:
        repo = FakeEntryRepository()
        repo.put(
            make_entry(
                id="target-id",
                namespace="ns-1",
                model_type=anything_model_type,
                payload={"provider": "openai", "model": "gpt-4.1"},
            )
        )
        result = populate_refs({"__ref__": "target-id"}, repo, "ns-1")
        assert isinstance(result, Anything)
        assert result.model_dump() == {"provider": "openai", "model": "gpt-4.1"}

    def test_nested_ref_in_target_resolves_recursively(self, anything_model_type: str) -> None:
        repo = FakeEntryRepository()
        repo.put(
            make_entry(
                id="leaf",
                namespace="ns-1",
                model_type=anything_model_type,
                payload={"value": 7},
            )
        )
        repo.put(
            make_entry(
                id="middle",
                namespace="ns-1",
                model_type=anything_model_type,
                payload={"child": {"__ref__": "leaf"}},
            )
        )
        result = populate_refs({"__ref__": "middle"}, repo, "ns-1")
        assert isinstance(result, Anything)
        # The nested ref was resolved into a typed instance before the outer
        # instance validated — confirm the child survived as a typed instance.
        child = result.child  # type: ignore[attr-defined]
        assert isinstance(child, Anything)
        assert child.model_dump() == {"value": 7}


class TestTypeMismatch:
    """``__type__`` mismatch raises ``CatalogValidationError`` (Story 15.2 preserved)."""

    def test_type_mismatch_raises(self) -> None:
        repo = FakeEntryRepository()
        repo.put(
            make_entry(
                id="target-id",
                namespace="ns-1",
                model_type="akgentic.llm.OtherConfig",
                payload={},
            )
        )
        marker = {"__ref__": "target-id", "__type__": "akgentic.llm.ModelConfig"}
        with pytest.raises(CatalogValidationError) as exc_info:
            populate_refs(marker, repo, "ns-1")
        assert len(exc_info.value.errors) == 1
        msg = exc_info.value.errors[0]
        assert "expected akgentic.llm.ModelConfig" in msg
        assert "got akgentic.llm.OtherConfig" in msg
        assert "target-id" in msg

    def test_type_match_succeeds(self, anything_model_type: str) -> None:
        repo = FakeEntryRepository()
        repo.put(
            make_entry(
                id="target-id",
                namespace="ns-1",
                model_type=anything_model_type,
                payload={"provider": "openai"},
            )
        )
        marker = {"__ref__": "target-id", "__type__": anything_model_type}
        result = populate_refs(marker, repo, "ns-1")
        assert isinstance(result, Anything)
        assert result.model_dump() == {"provider": "openai"}

    def test_absent_type_skips_check(self, anything_model_type: str) -> None:
        """Match-all case: no ``__type__`` in marker → no type check runs."""
        repo = FakeEntryRepository()
        repo.put(
            make_entry(
                id="target-id",
                namespace="ns-1",
                model_type=anything_model_type,
                payload={"k": 1},
            )
        )
        result = populate_refs({"__ref__": "target-id"}, repo, "ns-1")
        assert isinstance(result, Anything)
        assert result.model_dump() == {"k": 1}


class TestMissingTarget:
    """Missing target raises ``CatalogValidationError`` (Story 15.2 preserved)."""

    def test_missing_target_raises(self) -> None:
        repo = FakeEntryRepository()
        with pytest.raises(CatalogValidationError) as exc_info:
            populate_refs({"__ref__": "missing-id"}, repo, "ns-1")
        assert len(exc_info.value.errors) == 1
        msg = exc_info.value.errors[0]
        assert "not found" in msg
        assert "missing-id" in msg
        assert "ns-1" in msg


class TestCycleDetection:
    """Ref cycles raise ``CatalogValidationError`` (Story 15.2 preserved)."""

    def test_three_hop_cycle_raises(self, anything_model_type: str) -> None:
        repo = FakeEntryRepository()
        repo.put(
            make_entry(
                id="A",
                namespace="ns-1",
                model_type=anything_model_type,
                payload={"x": {"__ref__": "B"}},
            )
        )
        repo.put(
            make_entry(
                id="B",
                namespace="ns-1",
                model_type=anything_model_type,
                payload={"y": {"__ref__": "C"}},
            )
        )
        repo.put(
            make_entry(
                id="C",
                namespace="ns-1",
                model_type=anything_model_type,
                payload={"z": {"__ref__": "A"}},
            )
        )
        with pytest.raises(CatalogValidationError) as exc_info:
            populate_refs({"__ref__": "A"}, repo, "ns-1")
        msg = exc_info.value.errors[0].lower()
        assert "cycle" in msg
        assert "a" in msg  # id of the entry closing the cycle
        assert "ns-1" in msg

    def test_self_cycle_raises(self, anything_model_type: str) -> None:
        repo = FakeEntryRepository()
        repo.put(
            make_entry(
                id="A",
                namespace="ns-1",
                model_type=anything_model_type,
                payload={"self": {"__ref__": "A"}},
            )
        )
        with pytest.raises(CatalogValidationError) as exc_info:
            populate_refs({"__ref__": "A"}, repo, "ns-1")
        msg = exc_info.value.errors[0].lower()
        assert "cycle" in msg
        assert "a" in msg
        assert "ns-1" in msg

    def test_one_hop_ref_does_not_raise(self, anything_model_type: str) -> None:
        repo = FakeEntryRepository()
        repo.put(
            make_entry(id="A", namespace="ns-1", model_type=anything_model_type, payload={"v": 1})
        )
        result = populate_refs({"__ref__": "A"}, repo, "ns-1")
        assert isinstance(result, Anything)
        assert result.model_dump() == {"v": 1}


class TestVisitingNotShared:
    """``_visiting`` is per-ref-chain, not global across siblings (Story 15.2)."""

    def test_same_target_on_two_siblings_is_not_a_cycle(self, anything_model_type: str) -> None:
        repo = FakeEntryRepository()
        repo.put(
            make_entry(
                id="shared",
                namespace="ns-1",
                model_type=anything_model_type,
                payload={"provider": "openai"},
            )
        )
        node = {"left": {"__ref__": "shared"}, "right": {"__ref__": "shared"}}
        result = populate_refs(node, repo, "ns-1")
        assert isinstance(result["left"], Anything)
        assert isinstance(result["right"], Anything)
        assert result["left"].model_dump() == {"provider": "openai"}
        assert result["right"].model_dump() == {"provider": "openai"}

    def test_caller_visiting_set_not_mutated(self, anything_model_type: str) -> None:
        """A fresh set passed by a caller is never mutated by populate_refs."""
        repo = FakeEntryRepository()
        repo.put(
            make_entry(id="A", namespace="ns-1", model_type=anything_model_type, payload={"k": 1})
        )
        caller_set: set[tuple[str, str]] = set()
        populate_refs({"__ref__": "A"}, repo, "ns-1", caller_set)
        assert caller_set == set()


class TestNamespaceForwarding:
    """Namespace is forwarded unchanged; never derived from target (Story 15.2)."""

    def test_namespace_forwarded_through_nested_refs(self, anything_model_type: str) -> None:
        """The recursion must keep calling repo.get with the original namespace."""
        repo = FakeEntryRepository()
        repo.put(
            make_entry(
                id="A",
                namespace="ns-alpha",
                model_type=anything_model_type,
                payload={"v": {"__ref__": "B"}},
            )
        )
        repo.put(
            make_entry(
                id="B",
                namespace="ns-alpha",
                model_type=anything_model_type,
                payload={"leaf": 1},
            )
        )
        result = populate_refs({"__ref__": "A"}, repo, "ns-alpha")
        assert isinstance(result, Anything)
        # The nested ref was itself resolved to an Anything instance.
        v = result.v  # type: ignore[attr-defined]
        assert isinstance(v, Anything)
        assert v.model_dump() == {"leaf": 1}

    def test_target_in_other_namespace_invisible(self, anything_model_type: str) -> None:
        """Cross-namespace resolution is structurally impossible."""
        repo = FakeEntryRepository()
        repo.put(
            make_entry(
                id="A", namespace="ns-beta", model_type=anything_model_type, payload={"v": 1}
            )
        )
        with pytest.raises(CatalogValidationError) as exc_info:
            populate_refs({"__ref__": "A"}, repo, "ns-alpha")
        assert "not found" in exc_info.value.errors[0]
        assert "ns-alpha" in exc_info.value.errors[0]


class TestTypedSpliceValidationFailure:
    """Story 15.6 AC #5 — target payload invalid → error names target's id + model_type."""

    def test_validation_error_names_target_id_and_model_type(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        class StrictModel(BaseModel):
            required_field: str

        module_name = register_akgentic_test_module(
            monkeypatch, "tests_fixture_15_6_strict_model", StrictModel=StrictModel
        )
        fqcn = f"{module_name}.StrictModel"
        repo = FakeEntryRepository()
        repo.put(
            make_entry(
                id="broken-target",
                namespace="ns-1",
                model_type=fqcn,
                payload={},  # missing required_field
            )
        )
        with pytest.raises(CatalogValidationError) as exc_info:
            populate_refs({"__ref__": "broken-target"}, repo, "ns-1")
        assert len(exc_info.value.errors) == 1
        msg = exc_info.value.errors[0]
        # Points at the offending entry, not the caller.
        assert "broken-target" in msg
        assert fqcn in msg
        assert "does not validate" in msg
