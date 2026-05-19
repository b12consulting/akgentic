"""Tests for ``akgentic.catalog.resolver.resolve`` — AC13 through AC16."""

from __future__ import annotations

import pytest
from pydantic import BaseModel

from akgentic.catalog.models.errors import CatalogValidationError
from akgentic.catalog.resolver import resolve

from .conftest import FakeEntryRepository, make_entry, register_akgentic_test_module


class TestResolveHappyPath:
    """AC13 — happy path: model_type names a BaseModel; payload validates."""

    def test_resolves_to_model_instance(self, monkeypatch: pytest.MonkeyPatch) -> None:
        class SimpleModel(BaseModel):
            name: str
            count: int = 0

        module_name = register_akgentic_test_module(
            monkeypatch, "tests_fixture_15_2_resolve_simple", SimpleModel=SimpleModel
        )
        entry = make_entry(
            model_type=f"{module_name}.SimpleModel",
            payload={"name": "alpha", "count": 3},
        )
        repo = FakeEntryRepository()
        result = resolve(entry, repo)
        assert isinstance(result, SimpleModel)
        assert result.name == "alpha"
        assert result.count == 3

    def test_resolves_with_nested_refs(self, monkeypatch: pytest.MonkeyPatch) -> None:
        class ChildModel(BaseModel):
            provider: str

        class ParentModel(BaseModel):
            name: str
            child: ChildModel

        module_name = register_akgentic_test_module(
            monkeypatch,
            "tests_fixture_15_2_resolve_nested",
            ChildModel=ChildModel,
            ParentModel=ParentModel,
        )
        repo = FakeEntryRepository()
        repo.put(
            make_entry(
                id="child-id",
                namespace="ns-1",
                model_type=f"{module_name}.ChildModel",
                payload={"provider": "openai"},
            )
        )
        entry = make_entry(
            model_type=f"{module_name}.ParentModel",
            payload={"name": "parent", "child": {"__ref__": "child-id"}},
        )
        result = resolve(entry, repo)
        assert isinstance(result, ParentModel)
        assert result.child.provider == "openai"


class TestResolveAllowlistPassthrough:
    """AC14 — errors from ``load_model_type`` propagate unchanged."""

    def test_non_allowlisted_raises_from_loader(self) -> None:
        repo = FakeEntryRepository()
        # Build an Entry with a known-good model_type, then mutate to test loader rejection.
        # We need to bypass the storage-side allowlist — use model_construct to skip validation.
        from akgentic.catalog.models.entry import Entry

        entry = Entry.model_construct(
            id="e",
            kind="tool",
            namespace="ns-1",
            model_type="os.system",
            description="",
            payload={},
        )
        with pytest.raises(CatalogValidationError) as exc_info:
            resolve(entry, repo)
        assert "outside allowlist" in exc_info.value.errors[0]

    def test_non_basemodel_raises_from_loader(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Non-BaseModel attributes surface the BaseModel subclass check unchanged."""
        module_name = register_akgentic_test_module(
            monkeypatch, "tests_fixture_15_2_resolve_non_bm", not_a_model=42
        )
        entry = make_entry(model_type=f"{module_name}.not_a_model")
        repo = FakeEntryRepository()
        with pytest.raises(CatalogValidationError) as exc_info:
            resolve(entry, repo)
        assert "is not a Pydantic BaseModel subclass" in exc_info.value.errors[0]


class TestResolvePopulateFailures:
    """AC15 — errors from ``populate_refs`` propagate unchanged."""

    def test_missing_ref_propagates(self, monkeypatch: pytest.MonkeyPatch) -> None:
        class Model(BaseModel):
            value: dict[str, int]

        module_name = register_akgentic_test_module(
            monkeypatch, "tests_fixture_15_2_resolve_missing", Model=Model
        )
        entry = make_entry(
            model_type=f"{module_name}.Model",
            payload={"value": {"__ref__": "missing"}},
        )
        repo = FakeEntryRepository()
        with pytest.raises(CatalogValidationError) as exc_info:
            resolve(entry, repo)
        assert "not found" in exc_info.value.errors[0]

    def test_cycle_propagates(self, monkeypatch: pytest.MonkeyPatch) -> None:
        class Model(BaseModel):
            value: dict[str, int]

        module_name = register_akgentic_test_module(
            monkeypatch, "tests_fixture_15_2_resolve_cycle", Model=Model
        )
        repo = FakeEntryRepository()
        repo.put(
            make_entry(
                id="A",
                namespace="ns-1",
                model_type=f"{module_name}.Model",
                payload={"value": {"__ref__": "A"}},
            )
        )
        entry = make_entry(
            model_type=f"{module_name}.Model",
            payload={"value": {"__ref__": "A"}},
        )
        # Put entry itself into repo so it can be found — then cycle through A.
        with pytest.raises(CatalogValidationError) as exc_info:
            resolve(entry, repo)
        assert "cycle" in exc_info.value.errors[0].lower()


class TestResolveValidationFailure:
    """AC16 — Pydantic ValidationError is wrapped with substring-stable message."""

    def test_model_validate_failure_wrapped(self, monkeypatch: pytest.MonkeyPatch) -> None:
        class StrictModel(BaseModel):
            count: int

        module_name = register_akgentic_test_module(
            monkeypatch, "tests_fixture_15_2_resolve_strict", StrictModel=StrictModel
        )
        entry = make_entry(
            model_type=f"{module_name}.StrictModel",
            payload={"count": "not-an-int"},
        )
        repo = FakeEntryRepository()
        with pytest.raises(CatalogValidationError) as exc_info:
            resolve(entry, repo)
        msg = exc_info.value.errors[0]
        assert "Payload does not validate against" in msg
        assert f"{module_name}.StrictModel" in msg

    def test_validation_error_chained(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The original ``ValidationError`` is retained via ``raise ... from e``."""
        from pydantic import ValidationError

        class StrictModel(BaseModel):
            count: int

        module_name = register_akgentic_test_module(
            monkeypatch, "tests_fixture_15_2_resolve_chain", StrictModel=StrictModel
        )
        entry = make_entry(
            model_type=f"{module_name}.StrictModel",
            payload={"count": "bad"},
        )
        repo = FakeEntryRepository()
        try:
            resolve(entry, repo)
        except CatalogValidationError as e:
            assert isinstance(e.__cause__, ValidationError)
        else:
            pytest.fail("resolve did not raise CatalogValidationError")
