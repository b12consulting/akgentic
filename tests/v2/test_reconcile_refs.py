"""Tests for ``akgentic.catalog.resolver.reconcile_refs`` — AC17 through AC21."""

from __future__ import annotations

import copy

import pytest

from akgentic.catalog.resolver import reconcile_refs


class TestLeafRules:
    """AC17 — leaves on the input side take the ``dumped_node`` value."""

    def test_leaf_string_dumped_wins(self) -> None:
        assert reconcile_refs("input-literal", "dumped-literal") == "dumped-literal"

    def test_leaf_int_dumped_wins(self) -> None:
        assert reconcile_refs(1, 2) == 2

    def test_both_none(self) -> None:
        assert reconcile_refs(None, None) is None


class TestRefWinsVerbatim:
    """AC18 — ``{"__ref__": ...}`` markers win verbatim over any dumped value."""

    def test_flat_ref_identity_preserved(self) -> None:
        input_node = {"__ref__": "X"}
        dumped_node = {"some": "dumped", "thing": 1}
        result = reconcile_refs(input_node, dumped_node)
        assert result is input_node  # identity preserved

    def test_ref_with_type_hint(self) -> None:
        input_node = {"__ref__": "X", "__type__": "akgentic.llm.ModelConfig"}
        result = reconcile_refs(input_node, {"unrelated": True})
        assert result is input_node

    def test_nested_ref_wins(self) -> None:
        input_node = {"outer": {"__ref__": "X"}}
        dumped = {"outer": {"provider": "openai"}}
        result = reconcile_refs(input_node, dumped)
        # Top-level is a fresh dict, but the nested ref identity is preserved.
        assert result["outer"] is input_node["outer"]


class TestDictBranches:
    """AC19 — input keys recursed; unset-but-refed keys preserved; others dropped."""

    def test_key_in_dumped_recurses(self) -> None:
        input_node = {"provider": "openai"}
        dumped = {"provider": "normalised", "temperature": 0.7}
        result = reconcile_refs(input_node, dumped)
        assert result == {"provider": "normalised"}  # dumped wins for non-ref leaf

    def test_key_missing_with_ref_value_preserved(self) -> None:
        """Classic unset-but-refed branch: ref-valued key absent from dumped."""
        input_node = {"routes_to": [], "model_cfg": {"__ref__": "id_gpt_41"}}
        dumped = {"routes_to": []}  # model_cfg omitted by exclude_unset=True
        result = reconcile_refs(input_node, dumped)
        assert result == {"routes_to": [], "model_cfg": {"__ref__": "id_gpt_41"}}

    def test_key_missing_non_ref_is_dropped(self) -> None:
        input_node = {"a": "present", "b": "unset-in-dumped"}
        dumped = {"a": "dumped-a"}
        result = reconcile_refs(input_node, dumped)
        assert result == {"a": "dumped-a"}

    def test_dumped_has_extra_keys_those_are_dropped(self) -> None:
        """Only input keys are iterated — dumped extras never appear."""
        input_node = {"a": 1}
        dumped = {"a": 9, "extra": "ignored"}
        result = reconcile_refs(input_node, dumped)
        assert result == {"a": 9}

    def test_dumped_not_a_dict_all_keys_missing(self) -> None:
        """When dumped shape mismatches a dict input, only ref keys survive."""
        input_node = {"value": "something", "ref": {"__ref__": "X"}}
        dumped = "not-a-dict"
        result = reconcile_refs(input_node, dumped)
        # "value" key is non-ref and missing from (non-dict) dumped → dropped.
        # "ref" key is a ref marker and missing from dumped → preserved verbatim.
        assert result == {"ref": {"__ref__": "X"}}


class TestListPairwise:
    """AC20 — lists walked pairwise with strict length; mismatched shape falls through."""

    def test_equal_length_lists_recurse(self) -> None:
        input_node = [{"a": 1}, "literal"]
        dumped = [{"a": 9}, "dumped-literal"]
        result = reconcile_refs(input_node, dumped)
        assert result == [{"a": 9}, "dumped-literal"]

    def test_mismatched_length_raises_value_error(self) -> None:
        with pytest.raises(ValueError):
            reconcile_refs([1, 2, 3], [1, 2])

    def test_list_with_refs(self) -> None:
        input_node = [{"__ref__": "X"}, {"a": 1}]
        dumped = [{"ignored": True}, {"a": 9}]
        result = reconcile_refs(input_node, dumped)
        assert result == [{"__ref__": "X"}, {"a": 9}]

    def test_shape_mismatch_falls_through_to_leaf(self) -> None:
        """Input is a list but dumped is not → dumped leaf wins (AC20 final sentence)."""
        result = reconcile_refs([1, 2], "dumped-scalar")
        assert result == "dumped-scalar"


class TestNoMutation:
    """AC21 — neither input nor dumped tree is mutated by reconcile_refs."""

    def test_input_and_dumped_unchanged(self) -> None:
        input_node = {
            "routes_to": [],
            "model_cfg": {"__ref__": "id_gpt_41"},
            "agents": [{"role": "R1"}],
        }
        dumped = {"routes_to": [], "agents": [{"role": "R1-normed"}]}
        input_snapshot = copy.deepcopy(input_node)
        dumped_snapshot = copy.deepcopy(dumped)
        reconcile_refs(input_node, dumped)
        assert input_node == input_snapshot
        assert dumped == dumped_snapshot
