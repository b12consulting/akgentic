"""Tests for ``akgentic.catalog.resolver.prepare_for_write`` — AC22 through AC25."""

from __future__ import annotations

import copy
from typing import Any

import pytest
from pydantic import BaseModel

from akgentic.catalog import resolver as resolver_module
from akgentic.catalog.models.errors import CatalogValidationError
from akgentic.catalog.resolver import populate_refs, prepare_for_write

from .conftest import FakeEntryRepository, make_entry, register_akgentic_test_module


class TestPipelineOrdering:
    """AC23 — the five pipeline steps run in order, short-circuiting on failure."""

    def test_steps_run_in_order(self, monkeypatch: pytest.MonkeyPatch) -> None:
        class Simple(BaseModel):
            x: int = 0

        module_name = register_akgentic_test_module(
            monkeypatch, "tests_fixture_15_2_prepare_order", Simple=Simple
        )

        call_log: list[str] = []

        real_populate = resolver_module.populate_refs
        real_load = resolver_module.load_model_type
        real_reconcile = resolver_module.reconcile_refs

        def spy_populate(node: Any, repo: Any, ns: str, visiting: Any = None, **kwargs: Any) -> Any:
            call_log.append("populate_refs")
            return real_populate(node, repo, ns, visiting, **kwargs)

        def spy_load(path: str) -> Any:
            call_log.append("load_model_type")
            return real_load(path)

        original_validate = Simple.model_validate.__func__  # type: ignore[attr-defined]

        def spy_validate(cls_arg: Any, data: Any, **kwargs: Any) -> Any:
            call_log.append("model_validate")
            return original_validate(cls_arg, data, **kwargs)

        def spy_reconcile(inp: Any, dumped: Any) -> Any:
            call_log.append("reconcile_refs")
            return real_reconcile(inp, dumped)

        monkeypatch.setattr(resolver_module, "populate_refs", spy_populate)
        monkeypatch.setattr(resolver_module, "load_model_type", spy_load)
        monkeypatch.setattr(resolver_module, "reconcile_refs", spy_reconcile)
        monkeypatch.setattr(Simple, "model_validate", classmethod(spy_validate))

        entry = make_entry(
            model_type=f"{module_name}.Simple",
            payload={"x": 1},
        )
        repo = FakeEntryRepository()
        prepare_for_write(entry, repo)

        # ``populate_refs`` is recursive, so it may appear multiple times at
        # the head; ``reconcile_refs`` is similarly recursive at the tail. What
        # matters for AC23 is that the FIRST occurrence of each step is in the
        # documented order.
        firsts: list[str] = []
        for step in call_log:
            if step not in firsts:
                firsts.append(step)
        assert firsts == [
            "populate_refs",
            "load_model_type",
            "model_validate",
            "reconcile_refs",
        ]
        # Also assert every step was seen at least once.
        assert set(call_log) >= {
            "populate_refs",
            "load_model_type",
            "model_validate",
            "reconcile_refs",
        }

    def test_short_circuit_on_populate_failure(self) -> None:
        repo = FakeEntryRepository()
        entry = make_entry(payload={"value": {"__ref__": "missing"}})
        with pytest.raises(CatalogValidationError) as exc_info:
            prepare_for_write(entry, repo)
        assert "not found" in exc_info.value.errors[0]

    def test_short_circuit_on_load_failure(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Non-BaseModel attribute surfaces ``is not a Pydantic BaseModel subclass``."""
        module_name = register_akgentic_test_module(
            monkeypatch, "tests_fixture_15_2_prepare_bad_load", not_a_model=42
        )
        entry = make_entry(model_type=f"{module_name}.not_a_model", payload={})
        repo = FakeEntryRepository()
        with pytest.raises(CatalogValidationError) as exc_info:
            prepare_for_write(entry, repo)
        assert "is not a Pydantic BaseModel subclass" in exc_info.value.errors[0]

    def test_short_circuit_on_validate_failure(self, monkeypatch: pytest.MonkeyPatch) -> None:
        class StrictModel(BaseModel):
            count: int

        module_name = register_akgentic_test_module(
            monkeypatch, "tests_fixture_15_2_prepare_bad_validate", StrictModel=StrictModel
        )
        entry = make_entry(
            model_type=f"{module_name}.StrictModel",
            payload={"count": "not-an-int"},
        )
        repo = FakeEntryRepository()
        with pytest.raises(CatalogValidationError) as exc_info:
            prepare_for_write(entry, repo)
        msg = exc_info.value.errors[0]
        assert "Payload does not validate against" in msg
        assert f"{module_name}.StrictModel" in msg


class TestRoundTripInvariant:
    """AC24 — ``populate_refs(stored) == obj.model_dump(exclude_unset=True)``."""

    def test_round_trip_with_inline_and_ref_fields(self, monkeypatch: pytest.MonkeyPatch) -> None:
        class Child(BaseModel):
            provider: str
            temperature: float = 0.7

        class Parent(BaseModel):
            name: str
            child: Child

        module_name = register_akgentic_test_module(
            monkeypatch,
            "tests_fixture_15_2_prepare_round_trip",
            Child=Child,
            Parent=Parent,
        )
        repo = FakeEntryRepository()
        repo.put(
            make_entry(
                id="child-id",
                namespace="ns-1",
                model_type=f"{module_name}.Child",
                payload={"provider": "openai"},  # temperature unset → default 0.7
            )
        )
        input_payload: dict[str, Any] = {
            "name": "parent",
            "child": {"__ref__": "child-id"},
        }
        entry = make_entry(
            model_type=f"{module_name}.Parent",
            payload=input_payload,
        )
        prepared = prepare_for_write(entry, repo)

        # Rehydrate the stored payload — after Story 15.6, populate_refs returns
        # typed instances at ref-marker positions, so we compare by running both
        # sides through a final ``Parent.model_validate`` and dumping to dict.
        # The semantic invariant (the stored payload round-trips to the same
        # runtime shape as the input payload) is preserved.
        rehydrated_obj = Parent.model_validate(
            populate_refs(prepared.payload, repo, prepared.namespace)
        )
        input_obj = Parent.model_validate(populate_refs(entry.payload, repo, entry.namespace))
        assert rehydrated_obj.model_dump(mode="python", exclude_unset=True) == input_obj.model_dump(
            mode="python", exclude_unset=True
        )

    def test_ref_marker_preserved_in_stored(self, monkeypatch: pytest.MonkeyPatch) -> None:
        class Child(BaseModel):
            provider: str

        class Parent(BaseModel):
            name: str
            child: Child

        module_name = register_akgentic_test_module(
            monkeypatch,
            "tests_fixture_15_2_prepare_ref_preserved",
            Child=Child,
            Parent=Parent,
        )
        repo = FakeEntryRepository()
        repo.put(
            make_entry(
                id="child-id",
                namespace="ns-1",
                model_type=f"{module_name}.Child",
                payload={"provider": "openai"},
            )
        )
        entry = make_entry(
            model_type=f"{module_name}.Parent",
            payload={"name": "p", "child": {"__ref__": "child-id"}},
        )
        prepared = prepare_for_write(entry, repo)
        assert prepared.payload["child"] == {"__ref__": "child-id"}


class TestImmutability:
    """AC22 — ``prepare_for_write`` returns a new ``Entry`` and does not mutate input."""

    def test_returns_new_entry(self, monkeypatch: pytest.MonkeyPatch) -> None:
        class Simple(BaseModel):
            x: int = 0

        module_name = register_akgentic_test_module(
            monkeypatch, "tests_fixture_15_2_prepare_new_entry", Simple=Simple
        )
        entry = make_entry(model_type=f"{module_name}.Simple", payload={"x": 1})
        repo = FakeEntryRepository()
        prepared = prepare_for_write(entry, repo)
        assert prepared is not entry

    def test_input_entry_not_mutated(self, monkeypatch: pytest.MonkeyPatch) -> None:
        class Child(BaseModel):
            provider: str
            temperature: float = 0.7

        class Parent(BaseModel):
            name: str
            child: Child

        module_name = register_akgentic_test_module(
            monkeypatch,
            "tests_fixture_15_2_prepare_immut",
            Child=Child,
            Parent=Parent,
        )
        repo = FakeEntryRepository()
        repo.put(
            make_entry(
                id="child-id",
                namespace="ns-1",
                model_type=f"{module_name}.Child",
                payload={"provider": "openai"},
            )
        )
        entry = make_entry(
            model_type=f"{module_name}.Parent",
            payload={"name": "p", "child": {"__ref__": "child-id"}},
        )
        payload_snapshot = copy.deepcopy(entry.payload)
        prepare_for_write(entry, repo)
        assert entry.payload == payload_snapshot


class TestOwnershipNotRun:
    """AC25 — ``prepare_for_write`` does NOT invoke ``get_by_kind`` / ownership check."""

    def test_get_by_kind_not_called(self, monkeypatch: pytest.MonkeyPatch) -> None:
        class Simple(BaseModel):
            x: int = 0

        module_name = register_akgentic_test_module(
            monkeypatch, "tests_fixture_15_2_prepare_no_ownership", Simple=Simple
        )

        called: list[str] = []

        class SpyRepo(FakeEntryRepository):
            def get_by_kind(self, namespace: str, kind: Any) -> Any:
                called.append("get_by_kind")
                return super().get_by_kind(namespace, kind)

        entry = make_entry(model_type=f"{module_name}.Simple", payload={"x": 1})
        repo = SpyRepo()
        prepare_for_write(entry, repo)
        assert called == []
