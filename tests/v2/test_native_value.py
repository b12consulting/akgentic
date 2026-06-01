"""Tests for the ``NativeValue`` model and the resolver's ref-splice unwrap.

Story 26.1 / ADR-015 — the resolver unwraps a validated ``NativeValue``
instance at the ref-splice site so a typed field on a consuming entry
receives the bare scalar / list / dict instead of the wrapper.

Every behavioural AC from
``_bmad-output/akgentic-catalog/stories/26-1-add-nativevalue-model-and-resolver-unwrap.md``
is pinned here:

* AC 1-4 — ``NativeValue`` model surface (single field, serialization round-trip,
  ``load_model_type`` accepts the FQCN, no reserved fields).
* AC 5-7 — resolver behaviour: unwrap fires only for ``NativeValue`` targets;
  every other check (cycle, shareable-flag, ``__type__`` mismatch, sibling
  override merge) preserved verbatim.
* AC 9 — scalar / list / dict / sibling-override / ``__type__`` pinning /
  cross-namespace (shared + not-shared) / cycle detection / ``PromptTemplate``
  worked example.
* AC 10 — direct retrieval returns the ``Entry`` shape verbatim (no unwrap).
* AC 17 — namespace validation succeeds for a namespace containing a
  ``NativeValue`` + composite that references it.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from pydantic import BaseModel

from akgentic.catalog import NativeValue
from akgentic.catalog.catalog import Catalog
from akgentic.catalog.models.entry import Entry
from akgentic.catalog.models.errors import CatalogValidationError
from akgentic.catalog.repositories.yaml import YamlEntryRepository
from akgentic.catalog.resolver import (
    NAMESPACE_KEY,
    REF_KEY,
    TYPE_KEY,
    load_model_type,
    populate_refs,
)

from .conftest import (
    FakeEntryRepository,
    make_entry,
    make_meta_entry,
    register_akgentic_test_module,
)

_NATIVE_TYPE = "akgentic.catalog.NativeValue"


def _shareable_set(*namespaces: str) -> Callable[[str], bool]:
    """Return an ``is_namespace_shareable`` callable accepting any of ``namespaces``."""
    allowed = set(namespaces)

    def _check(ns: str) -> bool:
        return ns in allowed

    return _check


def _put_native(
    repo: FakeEntryRepository,
    *,
    id: str,
    namespace: str,
    value: Any,
    kind: str = "prompt",
    user_id: str = "anonymous",
) -> None:
    """Seed a NativeValue entry in ``repo`` under ``(namespace, id)``."""
    repo.put(
        make_entry(
            id=id,
            kind=kind,
            namespace=namespace,
            user_id=user_id,
            model_type=_NATIVE_TYPE,
            payload={"value": value},
        )
    )


# ---------------------------------------------------------------------------
# AC 1-4 — model surface
# ---------------------------------------------------------------------------


class TestNativeValueModelSurface:
    """The model has a single ``value`` field, round-trips through Pydantic, and
    does not collide with reserved ref-sentinel field names."""

    @pytest.mark.parametrize(
        "scalar",
        [
            "hello",
            42,
            3.14,
            True,
            False,
            [1, 2, 3],
            ["a", "b"],
            {"k": "v"},
            {"nested": {"x": 1}},
        ],
    )
    def test_construction_and_round_trip(self, scalar: Any) -> None:
        """AC 1 — construct + ``model_dump`` / ``model_validate`` round-trip."""
        instance = NativeValue(value=scalar)
        assert instance.value == scalar
        dumped = instance.model_dump()
        assert dumped == {"value": scalar}
        restored = NativeValue.model_validate(dumped)
        assert restored == instance

    def test_re_export_from_package_root(self) -> None:
        """AC 2 — ``NativeValue`` is importable from ``akgentic.catalog``."""
        from akgentic.catalog import NativeValue as ReExported

        assert ReExported is NativeValue

    def test_listed_in_all(self) -> None:
        """AC 2 — ``"NativeValue"`` is in ``akgentic.catalog.__all__``."""
        import akgentic.catalog as catalog_pkg

        assert "NativeValue" in catalog_pkg.__all__

    def test_no_reserved_field_collisions(self) -> None:
        """AC 4 — ``NativeValue`` declares no reserved ref-sentinel field."""
        reserved = {REF_KEY, TYPE_KEY, NAMESPACE_KEY}
        assert reserved.isdisjoint(set(NativeValue.model_fields.keys()))

    def test_load_model_type_returns_class(self) -> None:
        """AC 4 — ``load_model_type("akgentic.catalog.NativeValue")`` returns the class."""
        result = load_model_type(_NATIVE_TYPE)
        assert result is NativeValue


# ---------------------------------------------------------------------------
# AC 5, 9 — resolver scalar unwrap
# ---------------------------------------------------------------------------


class _Consumer(BaseModel):
    """Test consumer model whose ``value`` field is typed against the union of
    scalar types the resolver may unwrap from a ``NativeValue``."""

    value: str | int | float | bool | list[Any] | dict[str, Any]


@pytest.fixture
def consumer_model_type(monkeypatch: pytest.MonkeyPatch) -> str:
    """Register ``_Consumer`` under an ``akgentic.*`` module and return its FQCN."""
    module_name = register_akgentic_test_module(
        monkeypatch,
        "tests_fixture_26_1_consumer",
        Consumer=_Consumer,
    )
    return f"{module_name}.Consumer"


class TestResolverScalarUnwrap:
    """AC 9 — resolved ref to a NativeValue produces the bare scalar."""

    @pytest.mark.parametrize(
        ("scalar", "py_type"),
        [
            ("hello", str),
            (42, int),
            (3.14, float),
            (True, bool),
            (False, bool),
        ],
    )
    def test_scalar_unwrap(self, scalar: Any, py_type: type) -> None:
        repo = FakeEntryRepository()
        _put_native(repo, id="id_native", namespace="ns-1", value=scalar)
        result = populate_refs({"__ref__": "id_native"}, repo, "ns-1")
        # Unwrap fires — bare scalar, not a NativeValue.
        assert not isinstance(result, NativeValue)
        assert type(result) is py_type
        assert result == scalar

    def test_consumer_field_receives_bare_str(self, consumer_model_type: str) -> None:
        """A composite entry's typed ``str`` field gets the bare unwrapped string."""
        repo = FakeEntryRepository()
        _put_native(repo, id="id_native", namespace="ns-1", value="hello")
        composite = make_entry(
            id="composite",
            namespace="ns-1",
            model_type=consumer_model_type,
            payload={"value": {REF_KEY: "id_native"}},
        )
        repo.put(composite)
        catalog = Catalog(repo)
        resolved = catalog.resolve(composite)
        assert isinstance(resolved, _Consumer)
        assert resolved.value == "hello"
        assert type(resolved.value) is str


# ---------------------------------------------------------------------------
# AC 9 — list and dict unwrap
# ---------------------------------------------------------------------------


class TestResolverListAndDictUnwrap:
    def test_list_unwrap(self) -> None:
        repo = FakeEntryRepository()
        _put_native(repo, id="id_list", namespace="ns-1", value=["a", "b", "c"])
        result = populate_refs({"__ref__": "id_list"}, repo, "ns-1")
        assert result == ["a", "b", "c"]
        assert type(result) is list

    def test_dict_unwrap(self) -> None:
        repo = FakeEntryRepository()
        _put_native(repo, id="id_dict", namespace="ns-1", value={"k": "v"})
        result = populate_refs({"__ref__": "id_dict"}, repo, "ns-1")
        assert result == {"k": "v"}
        assert type(result) is dict

    def test_nested_dict_unwrap(self) -> None:
        """A NativeValue carrying a nested dict (no refs) unwraps verbatim.

        The wrapping ``BaseModel`` invariant is preserved; the consumer
        receives the bare dict for downstream use.
        """
        repo = FakeEntryRepository()
        _put_native(
            repo,
            id="id_dict",
            namespace="ns-1",
            value={"outer": {"inner": "literal"}},
        )
        result = populate_refs({"__ref__": "id_dict"}, repo, "ns-1")
        assert result == {"outer": {"inner": "literal"}}
        assert type(result) is dict

    def test_value_dict_with_embedded_ref_resolves_through_generic_walk(self) -> None:
        """The generic ``populate_refs`` walk DOES recurse into ``NativeValue.value``.

        ADR-015 §"Risks" notes a wish that ``NativeValue`` be opaque to the
        resolver beyond the unwrap. In practice the unwrap is one line on top
        of the existing recursion (Story 26.1 AC 5: "the existing checks ...
        nested-ref recursion via populate_refs ... are preserved exactly as
        they are today"). The generic walk therefore resolves any
        ``{__ref__: ...}`` it encounters inside ``NativeValue.value`` before
        the unwrap fires. This is the catalog author's responsibility to
        avoid — the anti-pattern is called out in the README and ADR but is
        not mechanically blocked.

        This test pins the actual behaviour so the contract is captured in
        an executable test rather than left implicit.
        """
        repo = FakeEntryRepository()
        _put_native(repo, id="id_inner", namespace="ns-1", value="resolved")
        _put_native(
            repo,
            id="id_dict_with_ref",
            namespace="ns-1",
            value={"nested": {REF_KEY: "id_inner"}},
        )
        result = populate_refs({"__ref__": "id_dict_with_ref"}, repo, "ns-1")
        # The embedded ref is resolved by the generic walk before NativeValue
        # validates the populated payload — the unwrap returns the resolved
        # dict, not the raw author-written dict.
        assert result == {"nested": "resolved"}


# ---------------------------------------------------------------------------
# AC 9 — sibling override on NativeValue ref
# ---------------------------------------------------------------------------


class TestSiblingOverrideOnNativeValueRef:
    """A non-reserved sibling on the ref marker shallow-merges into the
    target payload before validation, so ``value: <override>`` replaces the
    carried scalar before the unwrap fires."""

    @pytest.mark.parametrize(
        ("base_value", "override_value", "expected_type"),
        [
            ("base", "override", str),
            (1, 99, int),
        ],
    )
    def test_value_override_replaces_scalar(
        self, base_value: Any, override_value: Any, expected_type: type
    ) -> None:
        repo = FakeEntryRepository()
        _put_native(repo, id="id_native", namespace="ns-1", value=base_value)
        result = populate_refs({"__ref__": "id_native", "value": override_value}, repo, "ns-1")
        assert result == override_value
        assert type(result) is expected_type


# ---------------------------------------------------------------------------
# AC 9 — __type__ pinning success and mismatch
# ---------------------------------------------------------------------------


class _Other(BaseModel):
    """Throwaway non-NativeValue model used in __type__ mismatch tests."""

    text: str = ""


@pytest.fixture
def other_model_type(monkeypatch: pytest.MonkeyPatch) -> str:
    module_name = register_akgentic_test_module(
        monkeypatch,
        "tests_fixture_26_1_other",
        Other=_Other,
    )
    return f"{module_name}.Other"


class TestTypePinning:
    def test_type_pinning_success(self) -> None:
        """``__type__: akgentic.catalog.NativeValue`` on a NativeValue target works."""
        repo = FakeEntryRepository()
        _put_native(repo, id="id_native", namespace="ns-1", value="hi")
        result = populate_refs({"__ref__": "id_native", "__type__": _NATIVE_TYPE}, repo, "ns-1")
        # Unwrap still fires when __type__ pins NativeValue.
        assert result == "hi"

    def test_type_pinning_mismatch_native_target_other_expected(
        self, other_model_type: str
    ) -> None:
        """Pinning a non-NativeValue ``__type__`` against a NativeValue target
        raises the existing ``expected ... got ...`` mismatch error."""
        repo = FakeEntryRepository()
        _put_native(repo, id="id_native", namespace="ns-1", value="hi")
        with pytest.raises(CatalogValidationError) as exc_info:
            populate_refs({"__ref__": "id_native", "__type__": other_model_type}, repo, "ns-1")
        msg = exc_info.value.errors[0]
        assert "expected" in msg
        assert "got" in msg
        assert other_model_type in msg

    def test_type_pinning_mismatch_other_target_native_expected(
        self, other_model_type: str
    ) -> None:
        """Pinning ``__type__: NativeValue`` against a non-NativeValue target
        raises the same substring-stable mismatch error."""
        repo = FakeEntryRepository()
        repo.put(
            make_entry(
                id="id_other",
                namespace="ns-1",
                model_type=other_model_type,
                payload={"text": "hi"},
            )
        )
        with pytest.raises(CatalogValidationError) as exc_info:
            populate_refs({"__ref__": "id_other", "__type__": _NATIVE_TYPE}, repo, "ns-1")
        msg = exc_info.value.errors[0]
        assert "expected" in msg
        assert "got" in msg
        assert _NATIVE_TYPE in msg


# ---------------------------------------------------------------------------
# AC 9 — cross-namespace NativeValue refs
# ---------------------------------------------------------------------------


class TestCrossNamespaceNativeValue:
    """The shareable-flag gate runs unchanged for NativeValue targets — the
    unwrap happens after the gate, not before."""

    def test_shared_namespace_unwrap(self) -> None:
        repo = FakeEntryRepository()
        _put_native(repo, id="id_native", namespace="global", value="shared")
        result = populate_refs(
            {"__ref__": "id_native", "__namespace__": "global"},
            repo,
            "tenant-A",
            is_namespace_shareable=_shareable_set("global"),
        )
        assert result == "shared"

    def test_shorthand_shared_namespace_unwrap(self) -> None:
        repo = FakeEntryRepository()
        _put_native(repo, id="id_native", namespace="global", value="shared")
        result = populate_refs(
            {"__ref__": "global.id_native"},
            repo,
            "tenant-A",
            is_namespace_shareable=_shareable_set("global"),
        )
        assert result == "shared"

    def test_not_shared_namespace_rejected(self) -> None:
        """Same shape but the target namespace is NOT shareable — the
        resolver raises the existing ``"is not shareable"`` error, not a
        NativeValue-specific message."""
        repo = FakeEntryRepository()
        _put_native(repo, id="id_native", namespace="global", value="shared")
        with pytest.raises(CatalogValidationError) as exc_info:
            populate_refs(
                {"__ref__": "id_native", "__namespace__": "global"},
                repo,
                "tenant-A",
                # No shareable callable — defaults to "no namespace is shareable".
            )
        assert "is not shareable" in exc_info.value.errors[0]


# ---------------------------------------------------------------------------
# AC 9 — cycle detection through NativeValue
# ---------------------------------------------------------------------------


class _Composite(BaseModel):
    """Test composite model with a payload pointing at a nested ref."""

    label: str
    payload: dict[str, Any]


@pytest.fixture
def composite_model_type(monkeypatch: pytest.MonkeyPatch) -> str:
    module_name = register_akgentic_test_module(
        monkeypatch,
        "tests_fixture_26_1_composite",
        Composite=_Composite,
    )
    return f"{module_name}.Composite"


class TestCycleDetectionThroughNativeValue:
    """A composite payload that references the same NativeValue twice from
    different positions resolves fine — cycle detection is per-chain.

    A genuine cycle through a non-NativeValue intermediary still raises with
    the existing ``"cycle"`` substring.
    """

    def test_two_positions_same_native_resolves(self) -> None:
        """A composite payload that references the same NativeValue twice
        from sibling positions in the same outer payload resolves — cycle
        detection is per-ref-chain, not per-marker.
        """
        repo = FakeEntryRepository()
        _put_native(repo, id="id_native", namespace="ns-1", value="hi")
        outer_payload = {
            "first": {REF_KEY: "id_native"},
            "second": {REF_KEY: "id_native"},
        }
        result = populate_refs(outer_payload, repo, "ns-1")
        # Both positions unwrap to the bare scalar — no cycle.
        assert result == {"first": "hi", "second": "hi"}

    def test_non_native_cycle_still_detected(self, composite_model_type: str) -> None:
        """A genuine cycle through a non-NativeValue intermediary still
        raises with the existing ``"cycle"`` substring; adding a NativeValue
        ref in another corner of the payload does not silence it."""
        repo = FakeEntryRepository()
        _put_native(repo, id="id_native", namespace="ns-1", value="hi")
        # Two composites referencing each other through the ``payload`` field.
        repo.put(
            make_entry(
                id="comp-a",
                namespace="ns-1",
                model_type=composite_model_type,
                payload={
                    "label": "a",
                    "payload": {REF_KEY: "comp-b"},
                },
            )
        )
        repo.put(
            make_entry(
                id="comp-b",
                namespace="ns-1",
                model_type=composite_model_type,
                payload={
                    "label": "b",
                    "payload": {REF_KEY: "comp-a"},
                },
            )
        )
        with pytest.raises(CatalogValidationError) as exc_info:
            populate_refs({REF_KEY: "comp-a"}, repo, "ns-1")
        assert "cycle" in exc_info.value.errors[0]


# ---------------------------------------------------------------------------
# AC 9 — PromptTemplate worked example
# ---------------------------------------------------------------------------


class _PromptLike(BaseModel):
    """Local 2-field BaseModel substituting for the ADR-015 worked example.

    The story prefers using ``akgentic.llm.prompts.PromptTemplate`` directly,
    but the production ``PromptTemplate`` does not declare a ``role`` field —
    its fields are ``template: str`` and ``params: dict[str, str]``. The
    worked example in ADR-015 names ``template`` and ``role`` as two bare
    string fields. To pin the worked-example contract exactly as described
    in the ADR (two bare ``str`` fields populated through NativeValue refs),
    this test uses a local 2-field model rather than overloading
    ``PromptTemplate.params`` for a role string.
    """

    template: str
    role: str


@pytest.fixture
def prompt_like_model_type(monkeypatch: pytest.MonkeyPatch) -> str:
    module_name = register_akgentic_test_module(
        monkeypatch,
        "tests_fixture_26_1_prompt_like",
        PromptLike=_PromptLike,
    )
    return f"{module_name}.PromptLike"


class TestPromptTemplateWorkedExample:
    """ADR-015 §Worked example, end-to-end through ``Catalog.resolve_by_id``."""

    def test_resolves_to_prompt_like_with_bare_strings(
        self, prompt_like_model_type: str, tmp_path: Any
    ) -> None:
        repo = YamlEntryRepository(tmp_path)
        catalog = Catalog(repo)
        # Anchor the namespace with a team entry — required to satisfy
        # Layer 2's "namespace initialized" check before any sub-entry is
        # created. Use a meta entry rather than a team so we do not need to
        # depend on TeamCard's payload shape here.
        catalog.create(make_meta_entry("agent-team", shareable=False, user_id="anonymous"))
        catalog.create(
            Entry(
                id="id_team_template",
                kind="prompt",
                namespace="agent-team",
                user_id="anonymous",
                model_type=_NATIVE_TYPE,
                description="System-prompt template body",
                payload={"value": "You are {role}. Collaborate with your team."},
            )
        )
        catalog.create(
            Entry(
                id="id_team_role",
                kind="prompt",
                namespace="agent-team",
                user_id="anonymous",
                model_type=_NATIVE_TYPE,
                description="Default role label",
                payload={"value": "a helpful team member"},
            )
        )
        catalog.create(
            Entry(
                id="id_team_prompt",
                kind="prompt",
                namespace="agent-team",
                user_id="anonymous",
                model_type=prompt_like_model_type,
                description="Default team-member system prompt",
                payload={
                    "template": {REF_KEY: "id_team_template"},
                    "role": {REF_KEY: "id_team_role"},
                },
            )
        )

        resolved = catalog.resolve_by_id("agent-team", "id_team_prompt")
        assert isinstance(resolved, _PromptLike)
        assert resolved.template == "You are {role}. Collaborate with your team."
        assert resolved.role == "a helpful team member"
        # Both unwrapped to bare strings, not NativeValue wrappers.
        assert type(resolved.template) is str
        assert type(resolved.role) is str


# ---------------------------------------------------------------------------
# AC 10 — direct retrieval returns the Entry shape verbatim
# ---------------------------------------------------------------------------


class TestDirectRetrievalNoUnwrap:
    """``Catalog.get`` on a NativeValue entry returns the Entry like any
    other entry — the unwrap is resolver-side, not service-side."""

    def test_get_returns_entry_with_native_payload(self, tmp_path: Any) -> None:
        repo = YamlEntryRepository(tmp_path)
        catalog = Catalog(repo)
        catalog.create(make_meta_entry("ns-direct", shareable=False, user_id="anonymous"))
        catalog.create(
            Entry(
                id="id_native",
                kind="prompt",
                namespace="ns-direct",
                user_id="anonymous",
                model_type=_NATIVE_TYPE,
                payload={"value": "scalar"},
            )
        )
        entry = catalog.get("ns-direct", "id_native")
        assert isinstance(entry, Entry)
        assert entry.model_type == _NATIVE_TYPE
        assert entry.payload == {"value": "scalar"}
        # Symmetric with any other entry: caller validates explicitly if it
        # wants the typed form.
        wrapper = NativeValue.model_validate(entry.payload)
        assert wrapper.value == "scalar"


# ---------------------------------------------------------------------------
# AC 11-13 — repository round-trips (YAML, Mongo, Postgres if available)
# ---------------------------------------------------------------------------


class TestRepositoryRoundTripYaml:
    """A NativeValue entry round-trips through ``YamlEntryRepository``
    byte-equality on every field, including the payload."""

    def test_put_get_round_trip(self, tmp_path: Any) -> None:
        repo = YamlEntryRepository(tmp_path)
        entry = make_entry(
            id="id_native",
            kind="prompt",
            namespace="ns-1",
            model_type=_NATIVE_TYPE,
            payload={"value": "round-tripped"},
        )
        repo.put(entry)
        got = repo.get("ns-1", "id_native")
        assert got == entry


# ---------------------------------------------------------------------------
# AC 17 — namespace validation passes for a NativeValue + composite-that-refs-it
# ---------------------------------------------------------------------------


class TestNamespaceValidationWithNativeValue:
    """Layer 3 namespace validation succeeds when the namespace contains a
    NativeValue and a composite entry that references it — the transient
    validation step runs ``populate_refs`` (which unwraps) and then
    ``cls.model_validate`` (which sees the bare scalar at the typed
    field)."""

    def test_validate_namespace_ok(self, tmp_path: Any, consumer_model_type: str) -> None:
        repo = YamlEntryRepository(tmp_path)
        catalog = Catalog(repo)
        catalog.create(make_meta_entry("ns-validate", shareable=False, user_id="anonymous"))
        catalog.create(
            Entry(
                id="id_native",
                kind="prompt",
                namespace="ns-validate",
                user_id="anonymous",
                model_type=_NATIVE_TYPE,
                payload={"value": "hi"},
            )
        )
        catalog.create(
            Entry(
                id="id_consumer",
                kind="prompt",
                namespace="ns-validate",
                user_id="anonymous",
                model_type=consumer_model_type,
                payload={"value": {REF_KEY: "id_native"}},
            )
        )
        report = catalog.validate_namespace("ns-validate")
        assert report.ok is True
        assert report.namespace == "ns-validate"
        assert report.entry_issues == []
        assert report.global_errors == []
