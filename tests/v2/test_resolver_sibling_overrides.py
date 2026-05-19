"""Tests for Story 20.1 / ADR-010 — ``__ref__`` sibling overrides.

A ref-marker dict may carry non-reserved sibling keys alongside ``__ref__``
and ``__type__``. The resolver treats them as a **shallow override**: the
siblings are merged on top of the resolved target payload (top-level
``dict.update`` semantics) before the target's ``model_type`` validates the
result.

These tests exercise every acceptance criterion from
``_bmad-output/akgentic-catalog/stories/20-1-ref-sibling-overrides.md``:

* AC #1 — shallow override merges onto the target payload.
* AC #2 — no-override backwards compatibility (covered by
  :mod:`.test_populate_refs` baseline plus an explicit assertion here).
* AC #3 — overrides are shallow (top-level replacement, not deep merge).
* AC #4 — override values may themselves contain ``__ref__``.
* AC #5 — reserved keys (``__ref__`` / ``__type__``) retain their ADR-008
  roles.
* AC #6 — missing-field survival via base.
* AC #7 — invalid override names the target's ``model_type``.
* AC #8 — cycle detection unchanged across override resolution.
* AC #9 — ``reconcile_refs`` / ``prepare_for_write`` preserves ref +
  sibling overrides verbatim.
* AC #10 — ``find_references`` (delete guard) unchanged.
* AC #11 — one shared prompt entry works across multiple agents via
  per-agent ``params`` overrides.
"""

from __future__ import annotations

import copy
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any

import akgentic.tool  # noqa: F401 — load real ToolCard subclasses for the fixture bundle
import pytest
from pydantic import BaseModel, ConfigDict

from akgentic.catalog.models.errors import CatalogValidationError
from akgentic.catalog.repositories.yaml import YamlEntryRepository
from akgentic.catalog.resolver import populate_refs, prepare_for_write

from .conftest import FakeEntryRepository, make_entry, register_akgentic_test_module


class Anything(BaseModel):
    """Permissive test model — ``extra='allow'`` so any payload validates.

    Mirrors the helper class in ``test_populate_refs`` so sibling-override
    tests that do not need a realistic model shape can build throw-away
    entries quickly. ``model_dump()`` round-trips the input dict, keeping
    the dict-equality assertions below tight.
    """

    model_config = ConfigDict(extra="allow")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def anything_model_type(monkeypatch: pytest.MonkeyPatch) -> str:
    """Register ``Anything`` under an ``akgentic.*`` module and return its FQCN."""
    module_name = register_akgentic_test_module(
        monkeypatch,
        "tests_fixture_20_1_sibling_overrides_anything",
        Anything=Anything,
    )
    return f"{module_name}.Anything"


@pytest.fixture
def prompt_template_module() -> str:
    """Return the FQCN of the real ``akgentic.llm.PromptTemplate`` class.

    The resolver's allowlist requires ``akgentic.*`` prefixes, so the tests
    below use the production ``PromptTemplate`` directly rather than a stand-in.
    """
    # Imported lazily inside the fixture so module-import fails are surfaced
    # in the actual test, not at collection time.
    from akgentic.llm.prompts import PromptTemplate  # noqa: F401 — import-only check

    return "akgentic.llm.prompts.PromptTemplate"


# ---------------------------------------------------------------------------
# Task 4.1 — AC #1: motivating PromptTemplate case
# ---------------------------------------------------------------------------


class TestShallowOverrideMergesMotivating:
    """AC #1 — the concrete ``PromptTemplate`` reuse case."""

    def test_ref_with_params_override_shallow_merges(self, prompt_template_module: str) -> None:
        from akgentic.llm.prompts import PromptTemplate

        repo = FakeEntryRepository()
        repo.put(
            make_entry(
                id="id_team_prompt",
                namespace="ns-1",
                model_type=prompt_template_module,
                payload={
                    "template": "You are a helpful {role}. {instructions}",
                    "params": {"role": "assistant", "instructions": "Be helpful."},
                },
            )
        )
        marker = {
            "__ref__": "id_team_prompt",
            "params": {
                "role": "Manager",
                "instructions": "Coordinate the team effectively.",
            },
        }
        result = populate_refs(marker, repo, "ns-1")

        assert isinstance(result, PromptTemplate)
        assert result.template == "You are a helpful {role}. {instructions}"
        assert result.params == {
            "role": "Manager",
            "instructions": "Coordinate the team effectively.",
        }

    def test_target_payload_not_mutated(self, prompt_template_module: str) -> None:
        """The target entry's payload must not be mutated by the override merge."""
        repo = FakeEntryRepository()
        target_payload: dict[str, Any] = {
            "template": "T",
            "params": {"role": "assistant"},
        }
        repo.put(
            make_entry(
                id="id_prompt",
                namespace="ns-1",
                model_type=prompt_template_module,
                payload=target_payload,
            )
        )
        snapshot = copy.deepcopy(target_payload)
        populate_refs({"__ref__": "id_prompt", "params": {"role": "Manager"}}, repo, "ns-1")
        assert target_payload == snapshot

    def test_fresh_instance_on_each_call(self, prompt_template_module: str) -> None:
        """Re-resolving the same ref with a different override yields a fresh instance."""
        repo = FakeEntryRepository()
        repo.put(
            make_entry(
                id="id_prompt",
                namespace="ns-1",
                model_type=prompt_template_module,
                payload={"template": "T", "params": {"role": "assistant"}},
            )
        )
        a = populate_refs({"__ref__": "id_prompt", "params": {"role": "Manager"}}, repo, "ns-1")
        b = populate_refs({"__ref__": "id_prompt", "params": {"role": "Expert"}}, repo, "ns-1")
        assert a is not b
        assert a.params == {"role": "Manager"}  # type: ignore[attr-defined]
        assert b.params == {"role": "Expert"}  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Task 4.2 — AC #2: no-override backwards compatibility
# ---------------------------------------------------------------------------


class TestNoOverrideBackwardsCompatible:
    """AC #2 — ref-marker without siblings behaves exactly as before."""

    def test_pure_ref_only(self, anything_model_type: str) -> None:
        repo = FakeEntryRepository()
        repo.put(
            make_entry(
                id="target",
                namespace="ns-1",
                model_type=anything_model_type,
                payload={"k": 1, "v": "literal"},
            )
        )
        result = populate_refs({"__ref__": "target"}, repo, "ns-1")
        assert isinstance(result, Anything)
        assert result.model_dump() == {"k": 1, "v": "literal"}

    def test_ref_with_only_type_hint(self, anything_model_type: str) -> None:
        """`__type__` is reserved and is NOT treated as an override."""
        repo = FakeEntryRepository()
        repo.put(
            make_entry(
                id="target",
                namespace="ns-1",
                model_type=anything_model_type,
                payload={"k": 1},
            )
        )
        marker = {"__ref__": "target", "__type__": anything_model_type}
        result = populate_refs(marker, repo, "ns-1")
        assert isinstance(result, Anything)
        assert result.model_dump() == {"k": 1}


# ---------------------------------------------------------------------------
# Task 4.3 — AC #3: shallow merge, not deep
# ---------------------------------------------------------------------------


class TestOverrideIsShallow:
    """AC #3 — the override replaces the base's top-level value wholesale."""

    def test_override_replaces_nested_dict_wholesale(self, prompt_template_module: str) -> None:
        from akgentic.llm.prompts import PromptTemplate

        repo = FakeEntryRepository()
        repo.put(
            make_entry(
                id="id_prompt",
                namespace="ns-1",
                model_type=prompt_template_module,
                # PromptTemplate.params is ``dict[str, str]``, so both keys are
                # strings — this exercises the shallow-merge rule directly.
                payload={"template": "T", "params": {"role": "assistant", "tone": "neutral"}},
            )
        )
        marker = {"__ref__": "id_prompt", "params": {"role": "Manager"}}
        result = populate_refs(marker, repo, "ns-1")

        assert isinstance(result, PromptTemplate)
        # Shallow merge: the override's ``params`` replaces the base's ``params``
        # entirely. ``tone`` is GONE because the override does not re-declare it.
        assert result.params == {"role": "Manager"}


# ---------------------------------------------------------------------------
# Task 4.4 — AC #4: override values may themselves be ref markers
# ---------------------------------------------------------------------------


class TestOverrideValueIsRef:
    """AC #4 — a ref-marker inside an override resolves recursively."""

    def test_override_value_is_a_ref(self, anything_model_type: str) -> None:
        repo = FakeEntryRepository()
        # Target for the outer ref — has some default fields.
        repo.put(
            make_entry(
                id="id_parent",
                namespace="ns-1",
                model_type=anything_model_type,
                payload={"name": "base-parent", "nested": {"default": True}},
            )
        )
        # Target for the override ref — the override key wants this value.
        repo.put(
            make_entry(
                id="id_child",
                namespace="ns-1",
                model_type=anything_model_type,
                payload={"leaf": 42},
            )
        )
        marker = {"__ref__": "id_parent", "nested": {"__ref__": "id_child"}}
        result = populate_refs(marker, repo, "ns-1")

        assert isinstance(result, Anything)
        assert result.name == "base-parent"  # type: ignore[attr-defined]
        # The override value became a typed Anything instance via populate_refs
        # recursion BEFORE the merge ran.
        child = result.nested  # type: ignore[attr-defined]
        assert isinstance(child, Anything)
        assert child.model_dump() == {"leaf": 42}


# ---------------------------------------------------------------------------
# Task 4.5 — AC #8: cycle detection threads through override resolution
# ---------------------------------------------------------------------------


class TestCycleViaOverride:
    """AC #8 — A → B.override → A triggers the existing ``cycle`` error."""

    def test_override_cycle_raises_cycle_error(self, anything_model_type: str) -> None:
        repo = FakeEntryRepository()
        # A's payload is trivial — the cycle is established via B's OVERRIDE
        # value pointing back at A, not via A's payload.
        repo.put(
            make_entry(
                id="A",
                namespace="ns-1",
                model_type=anything_model_type,
                payload={"via_b": {"__ref__": "B", "extra": {"__ref__": "A"}}},
            )
        )
        repo.put(
            make_entry(
                id="B",
                namespace="ns-1",
                model_type=anything_model_type,
                payload={"k": 1},
            )
        )
        with pytest.raises(CatalogValidationError) as exc_info:
            populate_refs({"__ref__": "A"}, repo, "ns-1")
        msg = exc_info.value.errors[0].lower()
        assert "cycle" in msg
        # The closing id is A (the outer ref), which is detected when the
        # override value tries to re-enter it via B.
        assert "a" in msg


# ---------------------------------------------------------------------------
# Task 4.6 — AC #6: override may omit a required field (base supplies it)
# ---------------------------------------------------------------------------


class TestBaseSuppliesOmittedField:
    """AC #6 — missing-field survival via base."""

    def test_override_omitting_required_field_succeeds_via_base(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        class StrictModel(BaseModel):
            required_field: str
            other: str = "default"

        module_name = register_akgentic_test_module(
            monkeypatch,
            "tests_fixture_20_1_base_supplies_missing",
            StrictModel=StrictModel,
        )
        repo = FakeEntryRepository()
        repo.put(
            make_entry(
                id="id_strict",
                namespace="ns-1",
                model_type=f"{module_name}.StrictModel",
                payload={"required_field": "from-base", "other": "base-other"},
            )
        )
        # Override omits ``required_field`` entirely — base's value survives
        # the shallow merge.
        marker = {"__ref__": "id_strict", "other": "overridden"}
        result = populate_refs(marker, repo, "ns-1")

        assert isinstance(result, StrictModel)
        assert result.required_field == "from-base"
        assert result.other == "overridden"


# ---------------------------------------------------------------------------
# Task 4.7 — AC #7: invalid override value names target's model_type
# ---------------------------------------------------------------------------


class TestInvalidOverrideError:
    """AC #7 — the error substring must name the target entry and model_type."""

    def test_invalid_override_value_names_target_model_type(
        self, prompt_template_module: str
    ) -> None:
        repo = FakeEntryRepository()
        repo.put(
            make_entry(
                id="id_prompt",
                namespace="ns-1",
                model_type=prompt_template_module,
                payload={"template": "T", "params": {"role": "assistant"}},
            )
        )
        # ``params`` must be a dict[str, str]; passing a string is a type error.
        marker: dict[str, Any] = {"__ref__": "id_prompt", "params": "not a dict"}
        with pytest.raises(CatalogValidationError) as exc_info:
            populate_refs(marker, repo, "ns-1")
        msg = exc_info.value.errors[0]
        assert "Payload of 'id_prompt' does not validate against" in msg
        assert prompt_template_module in msg


# ---------------------------------------------------------------------------
# Task 5 — AC #9: reconcile_refs / prepare_for_write preserves ref + siblings
# ---------------------------------------------------------------------------


class TestWritePathRoundTrip:
    """AC #9 — stored payload preserves the full ref-marker dict verbatim."""

    def test_prepare_for_write_preserves_ref_and_siblings(
        self, prompt_template_module: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from akgentic.llm.prompts import PromptTemplate

        class Parent(BaseModel):
            name: str
            prompt: PromptTemplate

        module_name = register_akgentic_test_module(
            monkeypatch,
            "tests_fixture_20_1_write_path_parent",
            Parent=Parent,
            PromptTemplate=PromptTemplate,
        )

        repo = FakeEntryRepository()
        repo.put(
            make_entry(
                id="id_prompt",
                namespace="ns-1",
                model_type=prompt_template_module,
                payload={"template": "T", "params": {"role": "assistant"}},
            )
        )
        input_payload: dict[str, Any] = {
            "name": "parent",
            "prompt": {"__ref__": "id_prompt", "params": {"role": "Manager"}},
        }
        entry = make_entry(
            id="id_parent",
            namespace="ns-1",
            model_type=f"{module_name}.Parent",
            payload=input_payload,
        )
        prepared = prepare_for_write(entry, repo)

        # The whole ref-marker dict (including siblings) must survive verbatim.
        assert prepared.payload["prompt"] == {
            "__ref__": "id_prompt",
            "params": {"role": "Manager"},
        }

    def test_round_trip_resolves_to_same_typed_instance(
        self, prompt_template_module: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Re-resolving the stored payload matches the author's intent."""
        from akgentic.llm.prompts import PromptTemplate

        class Parent(BaseModel):
            name: str
            prompt: PromptTemplate

        module_name = register_akgentic_test_module(
            monkeypatch,
            "tests_fixture_20_1_write_path_round_trip",
            Parent=Parent,
            PromptTemplate=PromptTemplate,
        )

        repo = FakeEntryRepository()
        repo.put(
            make_entry(
                id="id_prompt",
                namespace="ns-1",
                model_type=prompt_template_module,
                payload={"template": "T", "params": {"role": "assistant"}},
            )
        )
        entry = make_entry(
            id="id_parent",
            namespace="ns-1",
            model_type=f"{module_name}.Parent",
            payload={
                "name": "p",
                "prompt": {"__ref__": "id_prompt", "params": {"role": "Manager"}},
            },
        )
        prepared = prepare_for_write(entry, repo)

        # Re-resolve the stored payload end-to-end; it should produce the same
        # in-memory shape as the author's original intent.
        rehydrated = Parent.model_validate(
            populate_refs(prepared.payload, repo, prepared.namespace)
        )
        original = Parent.model_validate(populate_refs(entry.payload, repo, entry.namespace))
        assert rehydrated.model_dump(mode="python", exclude_unset=True) == original.model_dump(
            mode="python", exclude_unset=True
        )
        assert rehydrated.prompt.params == {"role": "Manager"}


# ---------------------------------------------------------------------------
# Task 6 — AC #10: find_references returns the entry even with overrides
# ---------------------------------------------------------------------------


class TestFindReferencesWithOverrides:
    """AC #10 — sibling overrides do not hide the inbound ref edge."""

    def test_find_references_sees_ref_with_overrides(
        self, prompt_template_module: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from akgentic.llm.prompts import PromptTemplate

        class Parent(BaseModel):
            name: str
            prompt: PromptTemplate

        module_name = register_akgentic_test_module(
            monkeypatch,
            "tests_fixture_20_1_find_refs",
            Parent=Parent,
            PromptTemplate=PromptTemplate,
        )

        repo = FakeEntryRepository()
        repo.put(
            make_entry(
                id="id_prompt",
                namespace="ns-1",
                model_type=prompt_template_module,
                payload={"template": "T", "params": {}},
            )
        )
        parent = make_entry(
            id="id_parent",
            namespace="ns-1",
            model_type=f"{module_name}.Parent",
            payload={
                "name": "p",
                "prompt": {"__ref__": "id_prompt", "params": {"role": "Manager"}},
            },
        )
        repo.put(parent)

        referrers = repo.find_references("ns-1", "id_prompt")
        assert len(referrers) == 1
        assert referrers[0].id == "id_parent"


# ---------------------------------------------------------------------------
# Task 7 — AC #11: shared prompt entry across multiple agents via fixture
# ---------------------------------------------------------------------------


FIXTURE_ROOT = Path(__file__).parent.parent / "fixtures" / "sibling_overrides"


@pytest.fixture
def sibling_overrides_repo(tmp_path: Path) -> YamlEntryRepository:
    """Copy the sibling-overrides fixture bundle into ``tmp_path`` and return a repo.

    The bundle lives under ``tests/fixtures/sibling_overrides/`` and models the
    AC #11 end-to-end shape: one shared ``prompt`` entry plus two agents that
    reference it with different ``params:`` overrides.
    """
    dest = tmp_path / "catalog"
    shutil.copytree(FIXTURE_ROOT, dest)
    return YamlEntryRepository(dest)


class TestSharedPromptAcrossAgents:
    """AC #11 — one shared prompt, two agents, different ``params`` each."""

    def test_validate_namespace_ok(self, sibling_overrides_repo: YamlEntryRepository) -> None:
        from akgentic.catalog.catalog import Catalog

        catalog = Catalog(sibling_overrides_repo)
        report = catalog.validate_namespace("sibling-overrides-v1")
        assert report.ok, f"Namespace report was not ok: {report}"
        assert report.global_errors == []
        assert report.entry_issues == []

    @pytest.mark.parametrize(
        ("agent_id", "expected_params"),
        [
            ("manager", {"role": "Manager", "instructions": "Coordinate the team."}),
            ("assistant", {"role": "Assistant", "instructions": "Provide clear answers."}),
        ],
    )
    def test_agent_prompts_share_template_with_own_params(
        self,
        sibling_overrides_repo: YamlEntryRepository,
        agent_id: str,
        expected_params: dict[str, str],
    ) -> None:
        agent_entry = sibling_overrides_repo.get("sibling-overrides-v1", agent_id)
        assert agent_entry is not None
        populated = populate_refs(
            agent_entry.payload, sibling_overrides_repo, agent_entry.namespace
        )
        from akgentic.llm.prompts import PromptTemplate

        prompt = populated["config"]["prompt"]
        assert isinstance(prompt, PromptTemplate)
        # Shared template — identical across both agents.
        assert prompt.template == "You are a helpful {role}. {instructions}"
        # Per-agent overrides.
        assert prompt.params == expected_params


class TestCrossNamespaceSiblingOverrides:
    """Story 17.4 — overrides on a cross-ns ref merge in the target's namespace."""

    @staticmethod
    def _shareable_set(*namespaces: str) -> Callable[[str], bool]:
        allowed = set(namespaces)

        def _check(ns: str) -> bool:
            return ns in allowed

        return _check

    def test_override_on_cross_ns_ref_merges(self, monkeypatch: pytest.MonkeyPatch) -> None:
        module_name = register_akgentic_test_module(
            monkeypatch,
            "tests_fixture_17_4_sibling_target",
            Target=Anything,
        )
        repo = FakeEntryRepository()
        repo.put(
            make_entry(
                id="shared-prompt",
                namespace="global",
                user_id="anonymous",
                model_type=f"{module_name}.Target",
                payload={"role": "Helper", "tone": "calm"},
            )
        )
        result = populate_refs(
            {"__ref__": "global.shared-prompt", "role": "Manager"},
            repo,
            "tenant-A",
            is_namespace_shareable=self._shareable_set("global"),
        )
        assert isinstance(result, Anything)
        dumped = result.model_dump()
        # Override field wins.
        assert dumped["role"] == "Manager"
        # Target's other field preserved.
        assert dumped["tone"] == "calm"

    def test_nested_cross_ns_ref_in_override_gated_by_shareable_flag(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A nested cross-ns ref inside an override value is gated."""
        module_name = register_akgentic_test_module(
            monkeypatch,
            "tests_fixture_17_4_sibling_nested",
            Target=Anything,
        )
        repo = FakeEntryRepository()
        repo.put(
            make_entry(
                id="shared",
                namespace="global",
                user_id="anonymous",
                model_type=f"{module_name}.Target",
                payload={"x": 1},
            )
        )
        # The override's nested ref points at a non-shareable namespace.
        with pytest.raises(CatalogValidationError) as exc_info:
            populate_refs(
                {
                    "__ref__": "global.shared",
                    "params": {"nested_ref": {"__ref__": "other-ns.x"}},
                },
                repo,
                "tenant-A",
                is_namespace_shareable=self._shareable_set("global"),
            )
        msg = exc_info.value.errors[0]
        assert "is not shareable" in msg
        assert "other-ns.x" in msg
