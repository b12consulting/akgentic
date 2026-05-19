"""Unit tests for ``akgentic.catalog.validation`` (Story 16.3).

Covers:

* ``NamespaceValidationReport.ok`` derived-invariant.
* ``validate_entries`` global-error coverage (every class).
* ``validate_entries`` per-entry-error coverage (every class).
* Happy path.
* Zero-write guarantee (spy repository records every method call).
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import BaseModel, ValidationError
from pydantic.fields import FieldInfo

from akgentic.catalog.models.entry import Entry, EntryKind
from akgentic.catalog.resolver import REF_KEY, TYPE_KEY
from akgentic.catalog.validation import (
    EntryValidationIssue,
    NamespaceValidationReport,
    validate_entries,
)

from .conftest import make_entry, register_akgentic_test_module

_AGENT_TYPE = "akgentic.core.agent_card.AgentCard"
_TEAM_TYPE = "akgentic.team.models.TeamCard"


def _team_payload() -> dict[str, Any]:
    """Minimal valid ``TeamCard`` payload."""
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


def _agent_payload(name: str = "a") -> dict[str, Any]:
    """Minimal valid ``AgentCard`` payload."""
    return {
        "role": "r",
        "description": "",
        "skills": [],
        "agent_class": "akgentic.core.agent.Akgent",
        "config": {"name": name, "role": "r"},
        "routes_to": [],
        "metadata": {},
    }


class SpyRepository:
    """Minimal spy ``EntryRepository`` — records every method call.

    Only ``get`` is semantically useful (returns the seeded entry or ``None``);
    the rest exist so the spy satisfies the ``EntryRepository`` protocol. The
    ``calls`` list enables the AC33 zero-write assertion.
    """

    def __init__(self, entries: list[Entry] | None = None) -> None:
        self._store: dict[tuple[str, str], Entry] = (
            {(e.namespace, e.id): e for e in entries} if entries else {}
        )
        self.calls: list[tuple[str, tuple[Any, ...]]] = []

    def _record(self, name: str, args: tuple[Any, ...]) -> None:
        self.calls.append((name, args))

    def get(self, namespace: str, id: str) -> Entry | None:
        self._record("get", (namespace, id))
        return self._store.get((namespace, id))

    def put(self, entry: Entry) -> Entry:
        self._record("put", (entry,))
        self._store[(entry.namespace, entry.id)] = entry
        return entry

    def delete(self, namespace: str, id: str) -> None:
        self._record("delete", (namespace, id))
        self._store.pop((namespace, id), None)

    def list(self, query: Any) -> list[Entry]:
        self._record("list", (query,))
        return list(self._store.values())

    def list_by_namespace(self, namespace: str) -> list[Entry]:
        self._record("list_by_namespace", (namespace,))
        return [e for (ns, _), e in self._store.items() if ns == namespace]

    def get_by_kind(self, namespace: str, kind: EntryKind) -> Entry | None:
        self._record("get_by_kind", (namespace, kind))
        for (ns, _), e in self._store.items():
            if ns == namespace and e.kind == kind:
                return e
        return None

    def find_references(self, namespace: str, target_id: str) -> list[Entry]:
        self._record("find_references", (namespace, target_id))
        return []

    def count(self, method_name: str) -> int:
        return sum(1 for name, _ in self.calls if name == method_name)


def _seed_team(namespace: str = "ns-1", user_id: str = "alice") -> Entry:
    return make_entry(
        id="team",
        kind="team",
        namespace=namespace,
        user_id=user_id,
        model_type=_TEAM_TYPE,
        payload=_team_payload(),
    )


def _seed_agent(
    id: str = "agent-a",
    namespace: str = "ns-1",
    user_id: str = "alice",
    payload: dict[str, Any] | None = None,
) -> Entry:
    return make_entry(
        id=id,
        kind="agent",
        namespace=namespace,
        user_id=user_id,
        model_type=_AGENT_TYPE,
        payload=payload if payload is not None else _agent_payload(id),
    )


# --- NamespaceValidationReport.ok invariant (AC29) --------------------------


class TestReportOkInvariant:
    """``ok`` must reflect the two error lists — four construction cases."""

    def test_ok_true_no_errors_succeeds(self) -> None:
        report = NamespaceValidationReport(
            namespace="ns", ok=True, global_errors=[], entry_issues=[]
        )
        assert report.ok is True

    def test_ok_true_with_global_errors_raises(self) -> None:
        with pytest.raises(ValidationError):
            NamespaceValidationReport(
                namespace="ns", ok=True, global_errors=["boom"], entry_issues=[]
            )

    def test_ok_true_with_entry_issue_errors_raises(self) -> None:
        issue = EntryValidationIssue(entry_id="a", kind="agent", errors=["boom"])
        with pytest.raises(ValidationError):
            NamespaceValidationReport(
                namespace="ns", ok=True, global_errors=[], entry_issues=[issue]
            )

    def test_ok_false_with_no_errors_raises(self) -> None:
        with pytest.raises(ValidationError):
            NamespaceValidationReport(namespace="ns", ok=False, global_errors=[], entry_issues=[])


# --- validate_entries global-error coverage (AC30) --------------------------


class TestValidateEntriesGlobalErrors:
    """One test per global-error class."""

    def test_empty_list_returns_no_entries_error(self) -> None:
        repo = SpyRepository()
        report = validate_entries([], repo)
        assert report.ok is False
        assert report.namespace is None
        assert report.global_errors == ["namespace has no entries"]
        assert report.entry_issues == []

    def test_no_team_entry(self) -> None:
        repo = SpyRepository()
        entries = [_seed_agent("agent-a"), _seed_agent("agent-b")]
        report = validate_entries(entries, repo)
        assert report.ok is False
        assert len(report.global_errors) >= 1
        assert any("has no team entry and no meta entry" in msg for msg in report.global_errors)

    def test_multiple_team_entries(self) -> None:
        repo = SpyRepository()
        team1 = _seed_team()
        team2 = team1.model_copy(update={"id": "team-b"})
        report = validate_entries([team1, team2], repo)
        assert report.ok is False
        assert any(
            "multiple team entries" in msg and "team" in msg and "team-b" in msg
            for msg in report.global_errors
        )

    def test_mismatched_namespace_on_sub_entry(self) -> None:
        repo = SpyRepository()
        team = _seed_team(namespace="ns-1")
        # Build a second entry whose namespace disagrees.
        agent = _seed_agent(id="agent-a", namespace="ns-2")
        report = validate_entries([team, agent], repo)
        assert report.ok is False
        assert any(
            "entry 'agent-a' has namespace 'ns-2' but bundle namespace is 'ns-1'" in msg
            for msg in report.global_errors
        )

    def test_mismatched_user_id_on_sub_entry(self) -> None:
        repo = SpyRepository()
        team = _seed_team(user_id="alice")
        agent = _seed_agent(id="agent-a", user_id="bob")
        report = validate_entries([team, agent], repo)
        assert report.ok is False
        assert any("!= team user_id" in msg for msg in report.global_errors)

    def test_duplicate_ids_reported_once_each(self) -> None:
        repo = SpyRepository()
        team = _seed_team()
        a1 = _seed_agent(id="agent-a")
        a2 = _seed_agent(id="agent-a")  # same id as a1
        report = validate_entries([team, a1, a2], repo)
        dup_msgs = [m for m in report.global_errors if "duplicate entry id" in m]
        assert len(dup_msgs) == 1
        assert "'agent-a'" in dup_msgs[0]
        assert report.ok is False

    def test_dangling_ref_flagged(self) -> None:
        repo = SpyRepository()
        team = _seed_team()
        payload = _agent_payload("a")
        payload["metadata"] = {"ref": {REF_KEY: "ghost"}}
        agent = _seed_agent(id="agent-a", payload=payload)
        report = validate_entries([team, agent], repo)
        assert report.ok is False
        dangling = [m for m in report.global_errors if "dangling ref" in m]
        assert dangling, f"expected a dangling-ref message, got {report.global_errors!r}"
        assert "agent-a" in dangling[0] and "ghost" in dangling[0]

    def test_ownership_check_skipped_when_no_team(self) -> None:
        """AC11: ownership check is skipped when team count != 1 and no meta."""
        repo = SpyRepository()
        a1 = _seed_agent(id="agent-a", user_id="alice")
        a2 = _seed_agent(id="agent-b", user_id="bob")
        report = validate_entries([a1, a2], repo)
        # Must have the no-anchor message but NOT any user_id-mismatch.
        assert any("has no team entry and no meta entry" in m for m in report.global_errors)
        assert not any("!= team user_id" in m for m in report.global_errors)
        assert not any("!= meta user_id" in m for m in report.global_errors)

    # --- AC3 / Story 17.2 — meta singleton invariant in _global_checks ---

    def test_zero_meta_entries_with_team_is_ok(self) -> None:
        """AC3: zero kind=meta entries does NOT add a global error."""
        repo = SpyRepository()
        team = _seed_team()
        report = validate_entries([team], repo)
        assert not any("multiple meta entries" in m for m in report.global_errors)
        # And the report is overall ok.
        assert report.ok is True

    def test_one_meta_entry_with_team_is_ok(self) -> None:
        """AC3: exactly one kind=meta entry does NOT add a global error."""
        repo = SpyRepository()
        team = _seed_team()
        meta = make_entry(
            id="_meta",
            kind="meta",
            namespace="ns-1",
            user_id="alice",
            model_type="akgentic.catalog.models.namespace_meta.NamespaceMeta",
            payload={"name": "ns-1"},
        )
        report = validate_entries([team, meta], repo)
        assert not any("multiple meta entries" in m for m in report.global_errors)
        assert report.ok is True

    def test_two_meta_entries_flagged(self) -> None:
        """AC3: two kind=meta entries surface the multi-meta error."""
        repo = SpyRepository()
        team = _seed_team()
        meta_a = make_entry(
            id="_meta",
            kind="meta",
            namespace="ns-1",
            user_id="alice",
            model_type="akgentic.catalog.models.namespace_meta.NamespaceMeta",
            payload={"name": "primary"},
        )
        meta_b = make_entry(
            id="meta-extra",
            kind="meta",
            namespace="ns-1",
            user_id="alice",
            model_type="akgentic.catalog.models.namespace_meta.NamespaceMeta",
            payload={"name": "extra"},
        )
        report = validate_entries([team, meta_a, meta_b], repo)
        multi = [m for m in report.global_errors if "has multiple meta entries" in m]
        assert len(multi) == 1
        # Sorted ids must appear in the message.
        assert "'_meta'" in multi[0]
        assert "'meta-extra'" in multi[0]
        # Namespace is interpolated into the message.
        assert "namespace 'ns-1'" in multi[0]
        assert report.ok is False


# --- validate_entries per-entry-error coverage (AC31) -----------------------


class _DisagreeableModel(BaseModel):
    must_be_int: int


def _model_with_reserved_field(reserved_name: str) -> type[BaseModel]:
    """Return a throwaway BaseModel subclass carrying ``reserved_name`` in model_fields."""

    class _Host(BaseModel):
        placeholder: str = ""

    _Host.model_fields[reserved_name] = FieldInfo(annotation=str, default="")
    return _Host


class TestValidateEntriesPerEntryErrors:
    """One test per per-entry-error class."""

    def test_allowlist_violation_surfaces_per_entry(self) -> None:
        # Build an Entry directly via model_construct so the allowlisted-path
        # storage-layer check does not fire at construction time — we want
        # ``load_model_type`` (runtime) to reject it inside ``validate_entries``.
        team = _seed_team()
        bad = Entry.model_construct(
            id="badmodel",
            kind="model",
            namespace="ns-1",
            user_id="alice",
            model_type="builtins.dict",
            description="",
            payload={},
        )
        repo = SpyRepository()
        report = validate_entries([team, bad], repo)
        assert report.ok is False
        issues = [i for i in report.entry_issues if i.entry_id == "badmodel"]
        assert len(issues) == 1
        assert any("outside allowlist" in e for e in issues[0].errors)

    def test_transient_validation_failure(self, monkeypatch: pytest.MonkeyPatch) -> None:
        module_name = register_akgentic_test_module(
            monkeypatch,
            "tests_fixture_16_3_bad_payload",
            _DisagreeableModel=_DisagreeableModel,
        )
        path = f"{module_name}._DisagreeableModel"
        team = _seed_team()
        bad_payload = make_entry(
            id="bad-payload",
            kind="model",
            namespace="ns-1",
            user_id="alice",
            model_type=path,
            payload={"must_be_int": "not-an-int"},
        )
        repo = SpyRepository()
        report = validate_entries([team, bad_payload], repo)
        issues = [i for i in report.entry_issues if i.entry_id == "bad-payload"]
        assert issues
        assert any("payload does not validate against" in e for e in issues[0].errors)
        assert report.ok is False

    def test_ref_cycle_surfaces_per_entry(self) -> None:
        team = _seed_team()
        # Two agents whose payloads reference each other via the metadata dict.
        a_payload = _agent_payload("a")
        a_payload["metadata"] = {"ref": {REF_KEY: "b", TYPE_KEY: _AGENT_TYPE}}
        b_payload = _agent_payload("b")
        b_payload["metadata"] = {"ref": {REF_KEY: "a", TYPE_KEY: _AGENT_TYPE}}
        a = _seed_agent(id="a", payload=a_payload)
        b = _seed_agent(id="b", payload=b_payload)
        # Seed repository so populate_refs reaches the cycle during transient
        # validation (AC13: cycles surface per-entry via AC14).
        repo = SpyRepository(entries=[a, b])
        report = validate_entries([team, a, b], repo)
        assert report.ok is False
        cycle_ids = {
            i.entry_id for i in report.entry_issues if any("cycle" in e.lower() for e in i.errors)
        }
        # Per shard 05, the cycle surfaces under the per-entry list for both
        # participants as `populate_refs` walks the chain from each side.
        assert "a" in cycle_ids or "b" in cycle_ids

    def test_ref_type_mismatch_surfaces_per_entry(self) -> None:
        team = _seed_team()
        # Build a second entry (a "model" kind) and a referring agent whose
        # __type__ hint disagrees with the target's model_type.
        model_entry = make_entry(
            id="target",
            kind="model",
            namespace="ns-1",
            user_id="alice",
            model_type=_AGENT_TYPE,
            payload=_agent_payload("target"),
        )
        referrer_payload = _agent_payload("ref")
        referrer_payload["metadata"] = {
            "ref": {REF_KEY: "target", TYPE_KEY: _TEAM_TYPE}  # disagrees
        }
        referrer = _seed_agent(id="ref", payload=referrer_payload)
        repo = SpyRepository(entries=[model_entry])
        report = validate_entries([team, model_entry, referrer], repo)
        assert report.ok is False
        ref_issues = [i for i in report.entry_issues if i.entry_id == "ref"]
        assert ref_issues
        joined = " ".join(ref_issues[0].errors)
        assert "expected" in joined and "got" in joined

    def test_sentinel_key_collision_surfaces_per_entry(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        colliding_model = _model_with_reserved_field(REF_KEY)
        module_name = register_akgentic_test_module(
            monkeypatch,
            "tests_fixture_16_3_reserved",
            CollidingRefModel=colliding_model,
        )
        path = f"{module_name}.CollidingRefModel"
        team = _seed_team()
        offender = make_entry(
            id="offender",
            kind="model",
            namespace="ns-1",
            user_id="alice",
            model_type=path,
            payload={},
        )
        repo = SpyRepository()
        report = validate_entries([team, offender], repo)
        issues = [i for i in report.entry_issues if i.entry_id == "offender"]
        assert issues
        assert any("declares reserved ref-sentinel fields" in e for e in issues[0].errors)


# --- Story 17.10 — meta-only namespace validation ----------------------------


_NAMESPACE_META_TYPE = "akgentic.catalog.models.namespace_meta.NamespaceMeta"


class TestMetaOnlyNamespaceValidation:
    """Story 17.10 — meta-only entry list passes validation cleanly."""

    def test_meta_only_namespace_passes_validation(self) -> None:
        """A meta + model entry list (no team) passes validate_entries cleanly."""
        meta = make_entry(
            id="_meta",
            kind="meta",
            namespace="ns-1",
            user_id="anonymous",
            model_type=_NAMESPACE_META_TYPE,
            payload={"name": "ns-1", "description": "", "properties": {}, "shareable": False},
        )
        agent = _seed_agent(id="agent-a", user_id="anonymous")
        repo = SpyRepository(entries=[agent])
        report = validate_entries([meta, agent], repo)
        assert report.ok is True
        assert report.global_errors == []
        assert report.namespace == "ns-1"

    def test_meta_only_ownership_mismatch_flagged(self) -> None:
        """With meta anchor, ownership mismatch is still flagged."""
        meta = make_entry(
            id="_meta",
            kind="meta",
            namespace="ns-1",
            user_id="anonymous",
            model_type=_NAMESPACE_META_TYPE,
            payload={"name": "ns-1", "description": "", "properties": {}, "shareable": False},
        )
        agent = _seed_agent(id="agent-a", user_id="eve")
        repo = SpyRepository(entries=[agent])
        report = validate_entries([meta, agent], repo)
        assert report.ok is False
        assert any("!= meta user_id" in m for m in report.global_errors)


# --- Happy path (AC32) ------------------------------------------------------


class TestValidateEntriesHappyPath:
    def test_valid_bundle_returns_ok(self) -> None:
        team = _seed_team()
        # Build two valid agent entries referencing each other would create a
        # cycle — keep it as two independent agents for the happy path.
        a = _seed_agent(id="a")
        b = _seed_agent(id="b")
        repo = SpyRepository(entries=[a, b])
        report = validate_entries([team, a, b], repo)
        assert report.ok is True
        assert report.global_errors == []
        assert report.entry_issues == []
        assert report.namespace == "ns-1"


# --- Zero-write guarantee (AC33) --------------------------------------------


class TestValidateEntriesZeroWrites:
    """Every scenario must leave ``put`` / ``delete`` call counts at 0."""

    def _scenarios(self) -> list[tuple[str, list[Entry]]]:
        team = _seed_team()
        a = _seed_agent(id="agent-a")
        b = _seed_agent(id="agent-b", user_id="bob")  # ownership mismatch
        dup = _seed_agent(id="agent-a")  # duplicate id
        dangler_payload = _agent_payload("dangler")
        dangler_payload["metadata"] = {"ref": {REF_KEY: "ghost"}}
        dangler = _seed_agent(id="dangler", payload=dangler_payload)
        return [
            ("empty", []),
            ("no_team", [a]),
            ("happy", [team, a]),
            ("ownership_mismatch", [team, b]),
            ("duplicate", [team, a, dup]),
            ("dangling", [team, dangler]),
        ]

    def test_no_put_or_delete_invoked(self) -> None:
        for label, entries in self._scenarios():
            repo = SpyRepository(entries=list(entries))
            validate_entries(entries, repo)
            assert repo.count("put") == 0, f"scenario {label!r}: unexpected put()"
            assert repo.count("delete") == 0, f"scenario {label!r}: unexpected delete()"
