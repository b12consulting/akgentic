"""Tests for ``akgentic.catalog.resolver.load_model_type`` and the cross-ns shareable-flag gate.

The cross-namespace tests in this file pin ADR-008 §D2 (updated 2026-05-08
rev 2) — canonical ``__namespace__`` sentinel + ``<ns>.<id>`` shorthand
parsing, shareable-flag gate (target namespace's ``_meta`` carries
``payload["shareable"] is True``), cycle detection across namespaces, and
the ``populate_refs`` ``is_namespace_shareable`` keyword. The historic
cross-ns ``user_id`` ownership gate has been removed — entries in
shareable namespaces are referenceable cross-namespace regardless of
their owner.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import pytest
from pydantic import BaseModel
from pydantic.fields import FieldInfo

from akgentic.catalog.models.errors import CatalogValidationError
from akgentic.catalog.resolver import (
    NAMESPACE_KEY,
    REF_KEY,
    TYPE_KEY,
    load_model_type,
    populate_refs,
)

from .conftest import FakeEntryRepository, make_entry, register_akgentic_test_module


def _model_with_reserved_field(reserved_name: str) -> type[BaseModel]:
    """Return a throwaway BaseModel subclass with ``reserved_name`` in ``model_fields``.

    Pydantic 2 silently drops dunder-named fields through both the normal class
    body and ``create_model``. We sidestep the limitation by manually inserting
    a ``FieldInfo`` into ``model_fields`` on a plain subclass — good enough for
    the resolver's ``reserved_name in cls.model_fields`` check.
    """

    class _Host(BaseModel):
        placeholder: str = ""

    _Host.model_fields[reserved_name] = FieldInfo(annotation=str, default="")
    return _Host


def _shareable_set(*namespaces: str) -> Callable[[str], bool]:
    """Return an ``is_namespace_shareable`` callable accepting any of ``namespaces``."""
    allowed = set(namespaces)

    def _check(ns: str) -> bool:
        return ns in allowed

    return _check


class TestResolverConstants:
    """REF_KEY / TYPE_KEY have the expected literal values."""

    def test_ref_key_value(self) -> None:
        assert REF_KEY == "__ref__"

    def test_type_key_value(self) -> None:
        assert TYPE_KEY == "__type__"


class TestLoadModelTypeAllowlist:
    """Non-allowlisted paths are rejected by ``load_model_type``."""

    def test_rejects_os_system(self) -> None:
        with pytest.raises(CatalogValidationError) as exc_info:
            load_model_type("os.system")
        assert len(exc_info.value.errors) == 1
        message = exc_info.value.errors[0]
        assert "outside allowlist" in message
        assert "os.system" in message

    def test_rejects_builtins_eval(self) -> None:
        with pytest.raises(CatalogValidationError) as exc_info:
            load_model_type("builtins.eval")
        assert "outside allowlist" in exc_info.value.errors[0]
        assert "builtins.eval" in exc_info.value.errors[0]


class TestLoadModelTypeReservedKeys:
    """Classes declaring ``__ref__`` or ``__type__`` fields are rejected."""

    def test_ref_key_collision_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        colliding_model = _model_with_reserved_field(REF_KEY)
        module_name = register_akgentic_test_module(
            monkeypatch, "tests_fixture_15_1_ref", CollidingRefModel=colliding_model
        )

        with pytest.raises(CatalogValidationError) as exc_info:
            load_model_type(f"{module_name}.CollidingRefModel")

        message = exc_info.value.errors[0]
        assert "reserved ref-sentinel fields" in message
        assert REF_KEY in message

    def test_type_key_collision_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        colliding_model = _model_with_reserved_field(TYPE_KEY)
        module_name = register_akgentic_test_module(
            monkeypatch,
            "tests_fixture_15_1_type",
            CollidingTypeModel=colliding_model,
        )

        with pytest.raises(CatalogValidationError) as exc_info:
            load_model_type(f"{module_name}.CollidingTypeModel")

        message = exc_info.value.errors[0]
        assert "reserved ref-sentinel fields" in message
        assert TYPE_KEY in message


class TestLoadModelTypeNonBaseModel:
    """Non-BaseModel classes are rejected."""

    def test_rejects_dataclass(self, monkeypatch: pytest.MonkeyPatch) -> None:
        @dataclass
        class NotAModel:
            x: int = 0

        module_name = register_akgentic_test_module(
            monkeypatch, "tests_fixture_15_1_notmodel", NotAModel=NotAModel
        )

        with pytest.raises(CatalogValidationError) as exc_info:
            load_model_type(f"{module_name}.NotAModel")

        assert "is not a Pydantic BaseModel subclass" in exc_info.value.errors[0]

    def test_rejects_plain_function(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def some_function() -> None:
            return None

        module_name = register_akgentic_test_module(
            monkeypatch, "tests_fixture_15_1_fn", some_function=some_function
        )

        with pytest.raises(CatalogValidationError) as exc_info:
            load_model_type(f"{module_name}.some_function")

        assert "is not a Pydantic BaseModel subclass" in exc_info.value.errors[0]


class TestLoadModelTypeHappyPath:
    """A real akgentic.* BaseModel class resolves by identity."""

    def test_loads_agent_card(self) -> None:
        from akgentic.core.agent_card import AgentCard

        result = load_model_type("akgentic.core.agent_card.AgentCard")
        assert result is AgentCard


class TestNamespaceKeyConstant:
    """Story 17.3 / AC2 — ``NAMESPACE_KEY`` literal value + reserved-key gate."""

    def test_namespace_key_value(self) -> None:
        assert NAMESPACE_KEY == "__namespace__"

    def test_namespace_key_collision_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A class declaring a Pydantic field ``__namespace__`` is rejected."""
        colliding_model = _model_with_reserved_field(NAMESPACE_KEY)
        module_name = register_akgentic_test_module(
            monkeypatch,
            "tests_fixture_17_3_ns",
            CollidingNsModel=colliding_model,
        )

        with pytest.raises(CatalogValidationError) as exc_info:
            load_model_type(f"{module_name}.CollidingNsModel")

        message = exc_info.value.errors[0]
        assert "reserved ref-sentinel fields" in message
        assert NAMESPACE_KEY in message


class _Coord(BaseModel):
    """Throwaway model for cross-ns ref payload validation."""

    text: str = ""


@pytest.fixture
def coord_module(monkeypatch: pytest.MonkeyPatch) -> str:
    """Register ``_Coord`` under an ``akgentic.*`` path so model_type loads it."""
    return register_akgentic_test_module(monkeypatch, "tests_fixture_17_3_coord", Coord=_Coord)


class TestCrossNamespaceShorthandParsing:
    """Story 17.3 / AC3 — ``__ref__`` value with ``.`` is split on the FIRST dot.

    Preserved verbatim from Story 17.3 — the parsing rules are unchanged
    by the shareable-flag migration. Only the gate name + assertion substring
    move from ``"is not in the allowlist"`` to ``"is not shareable"``.
    """

    def test_single_dot_parses_ns_then_id(self, coord_module: str) -> None:
        repo = FakeEntryRepository()
        repo.put(
            make_entry(
                id="prompt",
                namespace="global",
                model_type=f"{coord_module}.Coord",
                payload={"text": "hi"},
            )
        )
        result = populate_refs(
            {"__ref__": "global.prompt"},
            repo,
            "tenant-A",
            is_namespace_shareable=_shareable_set("global"),
        )
        assert isinstance(result, _Coord)
        assert result.text == "hi"

    def test_multi_dot_id_split_on_first_only(self, coord_module: str) -> None:
        repo = FakeEntryRepository()
        repo.put(
            make_entry(
                id="x.y.z",
                namespace="global",
                model_type=f"{coord_module}.Coord",
                payload={"text": "compound"},
            )
        )
        result = populate_refs(
            {"__ref__": "global.x.y.z"},
            repo,
            "tenant-A",
            is_namespace_shareable=_shareable_set("global"),
        )
        assert isinstance(result, _Coord)
        assert result.text == "compound"

    def test_no_dot_is_same_namespace(self, coord_module: str) -> None:
        """No dot ⇒ same-namespace ref, shareable-flag gate not consulted."""
        repo = FakeEntryRepository()
        repo.put(
            make_entry(
                id="local-prompt",
                namespace="tenant-A",
                model_type=f"{coord_module}.Coord",
                payload={"text": "local"},
            )
        )
        # No is_namespace_shareable callable — same-ns refs work unconditionally.
        result = populate_refs({"__ref__": "local-prompt"}, repo, "tenant-A")
        assert isinstance(result, _Coord)
        assert result.text == "local"

    def test_dot_only_prefix_yields_empty_namespace_rejected(
        self,
        coord_module: str,  # noqa: ARG002
    ) -> None:
        """``"."<id>"`` ⇒ namespace="" — gated by the shareable-flag check."""
        repo = FakeEntryRepository()
        with pytest.raises(CatalogValidationError) as exc_info:
            populate_refs(
                {"__ref__": ".foo"},
                repo,
                "tenant-A",
                is_namespace_shareable=_shareable_set("global"),
            )
        msg = exc_info.value.errors[0]
        assert "is not shareable" in msg


class TestExplicitAndShorthandAgreement:
    """Story 17.3 / AC4 — explicit + shorthand same → ok; different → reject.

    Preserved verbatim from Story 17.3 — the parsing rules are unchanged.
    """

    def test_agreement_accepted(self, coord_module: str) -> None:
        repo = FakeEntryRepository()
        repo.put(
            make_entry(
                id="x",
                namespace="global",
                model_type=f"{coord_module}.Coord",
                payload={"text": "ok"},
            )
        )
        result = populate_refs(
            {"__ref__": "global.x", "__namespace__": "global"},
            repo,
            "tenant-A",
            is_namespace_shareable=_shareable_set("global"),
        )
        assert isinstance(result, _Coord)

    def test_disagreement_rejected(self) -> None:
        repo = FakeEntryRepository()
        with pytest.raises(CatalogValidationError) as exc_info:
            populate_refs(
                {"__ref__": "A.x", "__namespace__": "B"},
                repo,
                "tenant-A",
                is_namespace_shareable=_shareable_set("A", "B"),
            )
        msg = exc_info.value.errors[0]
        assert "shorthand 'ns.id' and explicit __namespace__" in msg
        assert "'A'" in msg
        assert "'B'" in msg

    def test_canonical_form_no_dot_uses_namespace_verbatim(self, coord_module: str) -> None:
        """``__namespace__`` set + ``__ref__`` without dot uses ``__namespace__``."""
        repo = FakeEntryRepository()
        repo.put(
            make_entry(
                id="bare-id",
                namespace="global",
                model_type=f"{coord_module}.Coord",
                payload={"text": "verbatim"},
            )
        )
        result = populate_refs(
            {"__ref__": "bare-id", "__namespace__": "global"},
            repo,
            "tenant-A",
            is_namespace_shareable=_shareable_set("global"),
        )
        assert isinstance(result, _Coord)
        assert result.text == "verbatim"


class TestPopulateRefsKwarg:
    """``populate_refs`` accepts the ``is_namespace_shareable`` keyword argument."""

    def test_default_no_kwarg_same_ns(self, coord_module: str) -> None:
        """No kwarg passed ⇒ same-ns refs work, no behaviour change."""
        repo = FakeEntryRepository()
        repo.put(
            make_entry(
                id="local",
                namespace="tenant-A",
                model_type=f"{coord_module}.Coord",
                payload={"text": "ok"},
            )
        )
        result = populate_refs({"__ref__": "local"}, repo, "tenant-A")
        assert isinstance(result, _Coord)


class TestCrossNamespaceShareableFlagGate:
    """Story 17.4 — shareable-flag gate fires before repository lookup."""

    def test_no_shareable_callable_rejects_cross_ns(self) -> None:
        repo = FakeEntryRepository()
        with pytest.raises(CatalogValidationError) as exc_info:
            populate_refs(
                {"__ref__": "global.x"},
                repo,
                "tenant-A",
                is_namespace_shareable=None,
            )
        msg = exc_info.value.errors[0]
        assert "is not shareable" in msg
        assert "global.x" in msg
        assert "'global'" in msg

    def test_namespace_not_shareable_rejected(self) -> None:
        repo = FakeEntryRepository()
        with pytest.raises(CatalogValidationError) as exc_info:
            populate_refs(
                {"__ref__": "other-ns.x"},
                repo,
                "tenant-A",
                is_namespace_shareable=_shareable_set("global"),
            )
        msg = exc_info.value.errors[0]
        assert "is not shareable" in msg
        assert "'other-ns'" in msg

    def test_shareable_namespace_resolves(self, coord_module: str) -> None:
        repo = FakeEntryRepository()
        repo.put(
            make_entry(
                id="p",
                namespace="global",
                model_type=f"{coord_module}.Coord",
                payload={"text": "shared"},
            )
        )
        result = populate_refs(
            {"__ref__": "global.p"},
            repo,
            "tenant-A",
            is_namespace_shareable=_shareable_set("global"),
        )
        assert isinstance(result, _Coord)
        assert result.text == "shared"

    def test_gate_fires_before_repo_lookup(self) -> None:
        """Denied cross-ns ref raises shareable-flag error even if target id is missing."""
        repo = FakeEntryRepository()  # empty repo — no global.does-not-exist
        with pytest.raises(CatalogValidationError) as exc_info:
            populate_refs(
                {"__ref__": "global.does-not-exist"},
                repo,
                "tenant-A",
                is_namespace_shareable=None,
            )
        # The "is not shareable" message wins; "not found" is never reached.
        assert "is not shareable" in exc_info.value.errors[0]

    def test_same_ns_unaffected_by_no_shareable_callable(self, coord_module: str) -> None:
        """A same-namespace ref resolves regardless of the shareable-flag callable."""
        repo = FakeEntryRepository()
        repo.put(
            make_entry(
                id="p",
                namespace="tenant-A",
                model_type=f"{coord_module}.Coord",
                payload={"text": "ok"},
            )
        )
        result = populate_refs(
            {"__ref__": "p"},
            repo,
            "tenant-A",
            is_namespace_shareable=None,
        )
        assert isinstance(result, _Coord)


class TestCrossNamespaceUserIdAcceptance:
    """Cross-ns refs accept any owner once the target namespace is shareable.

    The historic gate that rejected non-``"anonymous"`` cross-ns targets has
    been removed; ``meta.shareable`` is the single data-driven cross-ns
    eligibility check.
    """

    def test_user_scoped_target_accepted(self, coord_module: str) -> None:
        repo = FakeEntryRepository()
        repo.put(
            make_entry(
                id="user-p",
                namespace="global",
                user_id="alice",
                model_type=f"{coord_module}.Coord",
                payload={"text": "user-only"},
            )
        )
        result = populate_refs(
            {"__ref__": "global.user-p"},
            repo,
            "tenant-A",
            is_namespace_shareable=_shareable_set("global"),
        )
        assert isinstance(result, _Coord)
        assert result.text == "user-only"

    def test_global_target_accepted(self, coord_module: str) -> None:
        repo = FakeEntryRepository()
        repo.put(
            make_entry(
                id="global-p",
                namespace="global",
                user_id="anonymous",
                model_type=f"{coord_module}.Coord",
                payload={"text": "shared"},
            )
        )
        result = populate_refs(
            {"__ref__": "global.global-p"},
            repo,
            "tenant-A",
            is_namespace_shareable=_shareable_set("global"),
        )
        assert isinstance(result, _Coord)

    def test_admin_owned_target_resolves_via_canonical_marker(self, coord_module: str) -> None:
        """Admin-owned target resolves identically via shorthand and canonical marker shapes."""
        repo = FakeEntryRepository()
        repo.put(
            make_entry(
                id="admin-prompt",
                namespace="global",
                user_id="admin",
                model_type=f"{coord_module}.Coord",
                payload={"text": "admin-content"},
            )
        )
        shareable = _shareable_set("global")
        shorthand_result = populate_refs(
            {"__ref__": "global.admin-prompt"},
            repo,
            "tenant-A",
            is_namespace_shareable=shareable,
        )
        canonical_result = populate_refs(
            {"__ref__": "admin-prompt", "__namespace__": "global"},
            repo,
            "tenant-A",
            is_namespace_shareable=shareable,
        )
        assert isinstance(shorthand_result, _Coord)
        assert isinstance(canonical_result, _Coord)
        assert shorthand_result.text == "admin-content"
        assert canonical_result.text == "admin-content"
        assert shorthand_result.model_dump() == canonical_result.model_dump()


class _RefHolder(BaseModel):
    """A small ref-holder model used for cycle tests."""

    next: _Coord | None = None


class TestCrossNamespaceCycleDetection:
    """Story 17.3 / AC11 — 3-hop cross-ns cycle reuses the existing message.

    Preserved verbatim — cycle detection is unchanged by the shareable-flag
    migration.
    """

    def test_three_hop_cross_ns_cycle(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Register both Coord and RefHolder as akgentic.* model_types.
        module_name = register_akgentic_test_module(
            monkeypatch,
            "tests_fixture_17_3_holder",
            Coord=_Coord,
            RefHolder=_RefHolder,
        )
        repo = FakeEntryRepository()
        # tenant-A.x → global.y → tenant-A.x (cycle returns to start)
        repo.put(
            make_entry(
                id="x",
                namespace="tenant-A",
                model_type=f"{module_name}.RefHolder",
                payload={"next": {"__ref__": "global.y"}},
            )
        )
        repo.put(
            make_entry(
                id="y",
                namespace="global",
                model_type=f"{module_name}.RefHolder",
                payload={"next": {"__ref__": "tenant-A.x"}},
            )
        )
        with pytest.raises(CatalogValidationError) as exc_info:
            populate_refs(
                {"__ref__": "x"},
                repo,
                "tenant-A",
                is_namespace_shareable=_shareable_set("tenant-A", "global"),
            )
        assert "Reference cycle detected" in exc_info.value.errors[0]
