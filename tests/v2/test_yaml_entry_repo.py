"""Integration tests for ``YamlEntryRepository`` (Story 15.3).

Covers AC3-AC23 (AC1-AC2 are importability checks validated by the module-load
phase; AC24-AC28 are quality-gate ACs validated by pytest, ruff, mypy, and CI
themselves). Every test uses pytest's ``tmp_path`` fixture for an isolated
filesystem root — no shared state between tests.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import pytest
import yaml

from akgentic.catalog import YamlEntryRepository as PublicYamlEntryRepository
from akgentic.catalog.models.entry import Entry
from akgentic.catalog.models.errors import CatalogValidationError
from akgentic.catalog.models.queries import EntryQuery
from akgentic.catalog.repositories.base import EntryRepository
from akgentic.catalog.repositories.yaml import YamlEntryRepository

from .conftest import make_entry

# The annotated binding exercises mypy's structural-protocol check at module
# load time (AC3); mypy strict must accept it without inheritance.
_protocol_check: type[EntryRepository] = YamlEntryRepository


class TestPackageReExport:
    """AC2: the top-level package re-exports ``YamlEntryRepository``."""

    def test_public_reexport_is_the_same_class(self) -> None:
        """`from akgentic.catalog import YamlEntryRepository` resolves to the same class."""
        assert PublicYamlEntryRepository is YamlEntryRepository


class TestProtocolConformance:
    """AC3: the repository satisfies ``EntryRepository`` structurally."""

    def test_instance_exposes_every_protocol_method(self, tmp_path: Path) -> None:
        """All seven protocol methods exist and are callable on a live instance."""
        repo = YamlEntryRepository(tmp_path)
        assert callable(repo.get)
        assert callable(repo.put)
        assert callable(repo.delete)
        assert callable(repo.list)
        assert callable(repo.list_by_namespace)
        assert callable(repo.get_by_kind)
        assert callable(repo.find_references)


class TestConstruction:
    """AC4: constructor signature and lazy-create semantics."""

    def test_construct_with_missing_root_does_not_create_directory(self, tmp_path: Path) -> None:
        """Root dir is created only on first write, not in ``__init__``."""
        missing = tmp_path / "does-not-exist"
        assert not missing.exists()
        repo = YamlEntryRepository(missing)
        assert not missing.exists()
        # `root` attribute round-trips as Path (not string-coerced).
        assert isinstance(repo._root, Path)
        assert repo._root == missing


class TestPutWrites:
    """AC5-AC9: put writes, upsert, kind-change move, verbatim serialization."""

    def test_put_creates_file_at_namespace_kind_id_path(self, tmp_path: Path) -> None:
        """AC5: put writes to root/{namespace}/{kind}/{id}.yaml."""
        repo = YamlEntryRepository(tmp_path)
        entry = make_entry(
            id="assistant",
            kind="agent",
            namespace="team-abc",
            payload={"role": "assistant"},
        )
        repo.put(entry)
        path = tmp_path / "team-abc" / "agent" / "assistant.yaml"
        assert path.exists()
        on_disk = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert Entry.model_validate(on_disk) == entry

    def test_put_returns_stored_entry_equal_to_input(self, tmp_path: Path) -> None:
        """AC6: ``put`` returns an ``Entry`` equal to the input."""
        repo = YamlEntryRepository(tmp_path)
        entry = make_entry(namespace="ns", id="e1", kind="tool")
        returned = repo.put(entry)
        assert returned == entry

    def test_put_is_upsert_for_same_namespace_id_and_kind(self, tmp_path: Path) -> None:
        """AC7: repeat ``put`` with the same (namespace, id, kind) overwrites."""
        repo = YamlEntryRepository(tmp_path)
        first = make_entry(
            namespace="ns",
            id="entry",
            kind="tool",
            payload={"v": 1},
        )
        second = make_entry(
            namespace="ns",
            id="entry",
            kind="tool",
            payload={"v": 2},
        )
        repo.put(first)
        repo.put(second)
        got = repo.get("ns", "entry")
        assert got is not None
        assert got.payload == {"v": 2}

    def test_put_kind_change_moves_file_and_prunes_old_kind_dir(self, tmp_path: Path) -> None:
        """AC8: changing kind removes the old file and the empty old kind dir."""
        repo = YamlEntryRepository(tmp_path)
        old = make_entry(namespace="ns", id="assistant", kind="agent")
        new = make_entry(namespace="ns", id="assistant", kind="tool")
        repo.put(old)
        old_path = tmp_path / "ns" / "agent" / "assistant.yaml"
        assert old_path.exists()
        repo.put(new)
        assert not old_path.exists()
        # The old kind dir was empty, so it was pruned.
        assert not (tmp_path / "ns" / "agent").exists()
        new_path = tmp_path / "ns" / "tool" / "assistant.yaml"
        assert new_path.exists()
        got = repo.get("ns", "assistant")
        assert got is not None
        assert got.kind == "tool"

    def test_put_writes_payload_with_ref_marker_verbatim(self, tmp_path: Path) -> None:
        """AC9: ``__ref__`` markers survive on disk byte-for-byte after YAML parse."""
        repo = YamlEntryRepository(tmp_path)
        entry = make_entry(
            namespace="ns",
            id="e1",
            kind="agent",
            payload={"config": {"__ref__": "target-id"}},
        )
        repo.put(entry)
        path = tmp_path / "ns" / "agent" / "e1.yaml"
        on_disk = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert on_disk["payload"] == {"config": {"__ref__": "target-id"}}


class TestGet:
    """AC10: ``get`` returns hit, miss, or wrong-namespace miss."""

    def test_get_returns_stored_entry(self, tmp_path: Path) -> None:
        """Hit: a stored (namespace, id) returns the exact entry."""
        repo = YamlEntryRepository(tmp_path)
        entry = make_entry(namespace="team-abc", id="assistant", kind="agent")
        repo.put(entry)
        assert repo.get("team-abc", "assistant") == entry

    def test_get_missing_id_returns_none(self, tmp_path: Path) -> None:
        """Miss: unknown id in a known namespace returns None."""
        repo = YamlEntryRepository(tmp_path)
        repo.put(make_entry(namespace="team-abc", id="assistant", kind="agent"))
        assert repo.get("team-abc", "unknown") is None

    def test_get_wrong_namespace_returns_none(self, tmp_path: Path) -> None:
        """Namespace isolation: same id in a different namespace returns None."""
        repo = YamlEntryRepository(tmp_path)
        repo.put(make_entry(namespace="team-abc", id="assistant", kind="agent"))
        assert repo.get("other-ns", "assistant") is None

    def test_get_on_missing_namespace_dir_returns_none(self, tmp_path: Path) -> None:
        """Missing namespace directory does not raise; returns None."""
        repo = YamlEntryRepository(tmp_path)
        assert repo.get("never-materialised", "x") is None


class TestDelete:
    """AC11, AC12: delete removes file, prunes empty dirs, no-op on miss."""

    def test_delete_removes_file_and_all_empty_parents_up_to_root(self, tmp_path: Path) -> None:
        """AC11: a lone entry's delete prunes kind dir and namespace dir."""
        repo = YamlEntryRepository(tmp_path)
        repo.put(make_entry(namespace="team-abc", id="assistant", kind="agent"))
        repo.delete("team-abc", "assistant")
        assert not (tmp_path / "team-abc" / "agent" / "assistant.yaml").exists()
        assert not (tmp_path / "team-abc" / "agent").exists()
        assert not (tmp_path / "team-abc").exists()
        # Root is preserved.
        assert tmp_path.exists()

    def test_delete_preserves_sibling_kind_and_namespace_dirs(self, tmp_path: Path) -> None:
        """AC11: deleting one entry with a sibling leaves namespace + sibling dir."""
        repo = YamlEntryRepository(tmp_path)
        repo.put(make_entry(namespace="team-abc", id="assistant", kind="agent"))
        repo.put(make_entry(namespace="team-abc", id="workspace", kind="tool"))
        repo.delete("team-abc", "assistant")
        assert not (tmp_path / "team-abc" / "agent").exists()
        assert (tmp_path / "team-abc" / "tool" / "workspace.yaml").exists()
        assert (tmp_path / "team-abc").exists()

    def test_delete_missing_entry_is_noop(self, tmp_path: Path) -> None:
        """AC12: delete with no matching file completes without raising."""
        repo = YamlEntryRepository(tmp_path)
        repo.delete("team-abc", "missing")  # Should not raise.
        # Also works when namespace dir exists but file does not.
        repo.put(make_entry(namespace="team-abc", id="assistant", kind="agent"))
        repo.delete("team-abc", "missing")  # Should not raise.
        # The real entry is still there.
        assert repo.get("team-abc", "assistant") is not None


class TestList:
    """AC13: every EntryQuery filter is applied; combines with AND."""

    def _seed(self, repo: YamlEntryRepository) -> None:
        """Seed two namespaces with varied kinds, user_ids, descriptions."""
        repo.put(
            make_entry(
                namespace="ns-a",
                id="a1",
                kind="agent",
                user_id="alice",
                description="alpha one",
            )
        )
        repo.put(
            make_entry(
                namespace="ns-a",
                id="a2",
                kind="tool",
                user_id="anonymous",
                description="alpha two",
            )
        )
        repo.put(
            make_entry(
                namespace="ns-b",
                id="b1",
                kind="agent",
                user_id="bob",
                description="beta one",
            )
        )

    def test_list_filter_by_namespace(self, tmp_path: Path) -> None:
        repo = YamlEntryRepository(tmp_path)
        self._seed(repo)
        got = repo.list(EntryQuery(namespace="ns-a"))
        assert sorted(e.id for e in got) == ["a1", "a2"]

    def test_list_filter_by_kind(self, tmp_path: Path) -> None:
        repo = YamlEntryRepository(tmp_path)
        self._seed(repo)
        got = repo.list(EntryQuery(kind="agent"))
        assert sorted(e.id for e in got) == ["a1", "b1"]

    def test_list_filter_by_id_is_exact_match(self, tmp_path: Path) -> None:
        """AC13: ``id`` is exact match, not substring."""
        repo = YamlEntryRepository(tmp_path)
        self._seed(repo)
        got = repo.list(EntryQuery(id="a1"))
        assert len(got) == 1
        assert got[0].id == "a1"
        # Substring of "a1" — should return nothing.
        assert repo.list(EntryQuery(id="a")) == []

    def test_list_filter_by_user_id_exact(self, tmp_path: Path) -> None:
        repo = YamlEntryRepository(tmp_path)
        self._seed(repo)
        got = repo.list(EntryQuery(user_id="alice"))
        assert sorted(e.id for e in got) == ["a1"]

    def test_list_filter_user_id_set_true_keeps_only_scoped(self, tmp_path: Path) -> None:
        repo = YamlEntryRepository(tmp_path)
        self._seed(repo)
        got = repo.list(EntryQuery(user_id_set=True))
        assert sorted(e.id for e in got) == ["a1", "b1"]

    def test_list_filter_user_id_set_false_keeps_only_global(self, tmp_path: Path) -> None:
        repo = YamlEntryRepository(tmp_path)
        self._seed(repo)
        got = repo.list(EntryQuery(user_id_set=False))
        assert sorted(e.id for e in got) == ["a2"]

    def test_list_filter_description_contains_substring(self, tmp_path: Path) -> None:
        repo = YamlEntryRepository(tmp_path)
        self._seed(repo)
        got = repo.list(EntryQuery(description_contains="beta"))
        assert [e.id for e in got] == ["b1"]

    def test_list_combined_filters_use_and_semantics(self, tmp_path: Path) -> None:
        """AC13: multiple fields combine with AND."""
        repo = YamlEntryRepository(tmp_path)
        self._seed(repo)
        got = repo.list(EntryQuery(namespace="ns-a", kind="tool", user_id_set=False))
        assert [e.id for e in got] == ["a2"]

    def test_list_no_filters_spans_every_namespace(self, tmp_path: Path) -> None:
        repo = YamlEntryRepository(tmp_path)
        self._seed(repo)
        got = repo.list(EntryQuery())
        assert sorted(e.id for e in got) == ["a1", "a2", "b1"]

    def test_list_with_no_root_dir_returns_empty(self, tmp_path: Path) -> None:
        missing = tmp_path / "missing-root"
        repo = YamlEntryRepository(missing)
        assert repo.list(EntryQuery()) == []


class TestListByNamespace:
    """AC14: return every entry in a namespace regardless of kind."""

    def test_returns_every_kind_in_namespace(self, tmp_path: Path) -> None:
        repo = YamlEntryRepository(tmp_path)
        repo.put(make_entry(namespace="team-abc", id="team", kind="team"))
        repo.put(make_entry(namespace="team-abc", id="manager", kind="agent"))
        repo.put(make_entry(namespace="team-abc", id="assistant", kind="agent"))
        repo.put(make_entry(namespace="team-abc", id="workspace", kind="tool"))
        repo.put(make_entry(namespace="team-abc", id="greeting", kind="prompt"))
        got = repo.list_by_namespace("team-abc")
        assert {e.id for e in got} == {
            "team",
            "manager",
            "assistant",
            "workspace",
            "greeting",
        }
        assert len(got) == 5

    def test_missing_namespace_returns_empty_list(self, tmp_path: Path) -> None:
        repo = YamlEntryRepository(tmp_path)
        assert repo.list_by_namespace("never-materialised") == []


class TestGetByKind:
    """AC15: get_by_kind returns a single entry, first in sorted order, or None."""

    def test_returns_singleton_when_exactly_one(self, tmp_path: Path) -> None:
        repo = YamlEntryRepository(tmp_path)
        entry = make_entry(namespace="team-abc", id="team", kind="team")
        repo.put(entry)
        got = repo.get_by_kind("team-abc", "team")
        assert got == entry

    def test_returns_none_when_kind_dir_absent(self, tmp_path: Path) -> None:
        repo = YamlEntryRepository(tmp_path)
        # Put an entry of a different kind so the namespace dir exists.
        repo.put(make_entry(namespace="team-abc", id="assistant", kind="agent"))
        assert repo.get_by_kind("team-abc", "team") is None

    def test_returns_none_when_namespace_dir_absent(self, tmp_path: Path) -> None:
        repo = YamlEntryRepository(tmp_path)
        assert repo.get_by_kind("never-materialised", "team") is None

    def test_returns_alphabetically_first_on_duplicate_kind(self, tmp_path: Path) -> None:
        """AC15: multiple files in a kind dir return the first by filename sort."""
        repo = YamlEntryRepository(tmp_path)
        # Deliberately write two team entries (corruption state) to the same kind dir.
        # ``put`` would never produce this because a single (namespace, id) is the
        # key — we write by hand to exercise the read path.
        kind_dir = tmp_path / "team-abc" / "team"
        kind_dir.mkdir(parents=True)
        team_b = make_entry(namespace="team-abc", id="team-b", kind="team")
        team_a = make_entry(namespace="team-abc", id="team-a", kind="team")
        for entry in (team_a, team_b):
            path = kind_dir / f"{entry.id}.yaml"
            path.write_text(
                yaml.dump(
                    entry.model_dump(mode="json"),
                    default_flow_style=False,
                    sort_keys=False,
                    allow_unicode=True,
                ),
                encoding="utf-8",
            )
        got = repo.get_by_kind("team-abc", "team")
        assert got is not None
        assert got.id == "team-a"  # alphabetically first


class TestFindReferences:
    """AC16: walk every payload, match dict __ref__ keys, namespace-isolated."""

    def test_finds_ref_at_nested_dict_depth(self, tmp_path: Path) -> None:
        repo = YamlEntryRepository(tmp_path)
        repo.put(
            make_entry(
                namespace="team-abc",
                id="agent-1",
                kind="agent",
                payload={"config": {"model_cfg": {"__ref__": "id_gpt_41"}}},
            )
        )
        got = repo.find_references("team-abc", "id_gpt_41")
        assert [e.id for e in got] == ["agent-1"]

    def test_finds_ref_inside_list(self, tmp_path: Path) -> None:
        repo = YamlEntryRepository(tmp_path)
        repo.put(
            make_entry(
                namespace="team-abc",
                id="agent-2",
                kind="agent",
                payload={"config": {"tools": [{"__ref__": "id_gpt_41"}]}},
            )
        )
        got = repo.find_references("team-abc", "id_gpt_41")
        assert [e.id for e in got] == ["agent-2"]

    def test_finds_multiple_references(self, tmp_path: Path) -> None:
        """AC16: two refs in one namespace return two entries (unordered)."""
        repo = YamlEntryRepository(tmp_path)
        repo.put(
            make_entry(
                namespace="team-abc",
                id="agent-1",
                kind="agent",
                payload={"config": {"model_cfg": {"__ref__": "id_gpt_41"}}},
            )
        )
        repo.put(
            make_entry(
                namespace="team-abc",
                id="agent-2",
                kind="agent",
                payload={"config": {"tools": [{"__ref__": "id_gpt_41"}]}},
            )
        )
        got = repo.find_references("team-abc", "id_gpt_41")
        assert sorted(e.id for e in got) == ["agent-1", "agent-2"]

    def test_does_not_match_wrong_target_id(self, tmp_path: Path) -> None:
        repo = YamlEntryRepository(tmp_path)
        repo.put(
            make_entry(
                namespace="team-abc",
                id="agent-1",
                kind="agent",
                payload={"config": {"model_cfg": {"__ref__": "other-target"}}},
            )
        )
        assert repo.find_references("team-abc", "id_gpt_41") == []

    def test_does_not_match_raw_string_payload(self, tmp_path: Path) -> None:
        """AC16: a plain string equal to ``__ref__`` is not a match (must be dict key)."""
        repo = YamlEntryRepository(tmp_path)
        repo.put(
            make_entry(
                namespace="team-abc",
                id="agent-1",
                kind="agent",
                payload={"note": "__ref__", "other": "id_gpt_41"},
            )
        )
        assert repo.find_references("team-abc", "id_gpt_41") == []

    def test_namespace_isolation(self, tmp_path: Path) -> None:
        """Refs in another namespace are not returned."""
        repo = YamlEntryRepository(tmp_path)
        repo.put(
            make_entry(
                namespace="team-abc",
                id="agent-1",
                kind="agent",
                payload={"config": {"model_cfg": {"__ref__": "id_gpt_41"}}},
            )
        )
        repo.put(
            make_entry(
                namespace="other-ns",
                id="agent-2",
                kind="agent",
                payload={"config": {"model_cfg": {"__ref__": "id_gpt_41"}}},
            )
        )
        got = repo.find_references("team-abc", "id_gpt_41")
        assert [e.id for e in got] == ["agent-1"]


class TestDuplicateDetection:
    """AC17, AC18: duplicate stems and path/body mismatches raise on scan."""

    def _write_yaml(self, path: Path, payload: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            yaml.dump(payload, default_flow_style=False, sort_keys=False, allow_unicode=True),
            encoding="utf-8",
        )

    def test_duplicate_id_across_kinds_raises_on_first_read(self, tmp_path: Path) -> None:
        """AC17: same id under agent/ and tool/ raises CatalogValidationError."""
        repo = YamlEntryRepository(tmp_path)
        agent = make_entry(namespace="team-abc", id="assistant", kind="agent")
        tool = make_entry(namespace="team-abc", id="assistant", kind="tool")
        self._write_yaml(
            tmp_path / "team-abc" / "agent" / "assistant.yaml",
            agent.model_dump(mode="json"),
        )
        self._write_yaml(
            tmp_path / "team-abc" / "tool" / "assistant.yaml",
            tool.model_dump(mode="json"),
        )
        with pytest.raises(CatalogValidationError) as exc_info:
            repo.list_by_namespace("team-abc")
        msg = str(exc_info.value)
        assert "Duplicate id 'assistant'" in msg
        assert str(tmp_path / "team-abc" / "agent" / "assistant.yaml") in msg
        assert str(tmp_path / "team-abc" / "tool" / "assistant.yaml") in msg

    def test_namespace_mismatch_in_file_body_raises(self, tmp_path: Path) -> None:
        """AC18: file under ns-a/ whose YAML says namespace: ns-b raises."""
        repo = YamlEntryRepository(tmp_path)
        bad = make_entry(namespace="different-ns", id="assistant", kind="agent")
        path = tmp_path / "team-abc" / "agent" / "assistant.yaml"
        self._write_yaml(path, bad.model_dump(mode="json"))
        with pytest.raises(CatalogValidationError) as exc_info:
            repo.list_by_namespace("team-abc")
        msg = str(exc_info.value)
        assert str(path) in msg
        assert "different-ns" in msg
        assert "team-abc" in msg

    def test_kind_mismatch_in_file_body_raises(self, tmp_path: Path) -> None:
        """AC18: file under agent/ whose YAML says kind: tool raises."""
        repo = YamlEntryRepository(tmp_path)
        bad = make_entry(namespace="team-abc", id="assistant", kind="tool")
        path = tmp_path / "team-abc" / "agent" / "assistant.yaml"
        self._write_yaml(path, bad.model_dump(mode="json"))
        with pytest.raises(CatalogValidationError) as exc_info:
            repo.list_by_namespace("team-abc")
        msg = str(exc_info.value)
        assert str(path) in msg
        assert "tool" in msg
        assert "agent" in msg

    def test_id_mismatch_between_file_body_and_stem_raises(self, tmp_path: Path) -> None:
        """AC18: file body id != filename stem raises."""
        repo = YamlEntryRepository(tmp_path)
        bad = make_entry(namespace="team-abc", id="different-id", kind="agent")
        path = tmp_path / "team-abc" / "agent" / "assistant.yaml"
        self._write_yaml(path, bad.model_dump(mode="json"))
        with pytest.raises(CatalogValidationError) as exc_info:
            repo.list_by_namespace("team-abc")
        msg = str(exc_info.value)
        assert str(path) in msg
        assert "different-id" in msg
        assert "assistant" in msg


class TestEmptyYaml:
    """AC19: empty files are skipped with a warning, not an error."""

    def test_empty_file_skipped_with_warning(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        repo = YamlEntryRepository(tmp_path)
        repo.put(make_entry(namespace="team-abc", id="assistant", kind="agent"))
        # Hand-write an empty file alongside the valid one.
        empty_path = tmp_path / "team-abc" / "agent" / "empty.yaml"
        empty_path.write_text("", encoding="utf-8")
        repo.reload()  # Invalidate cache so the next read rescans.
        with caplog.at_level(logging.WARNING):
            got = repo.list_by_namespace("team-abc")
        assert [e.id for e in got] == ["assistant"]
        assert "empty YAML file skipped" in caplog.text
        assert str(empty_path) in caplog.text

    def test_whitespace_only_file_skipped(self, tmp_path: Path) -> None:
        repo = YamlEntryRepository(tmp_path)
        path = tmp_path / "team-abc" / "agent" / "ws.yaml"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("   \n   \n", encoding="utf-8")
        assert repo.list_by_namespace("team-abc") == []

    def test_tilde_only_file_skipped(self, tmp_path: Path) -> None:
        repo = YamlEntryRepository(tmp_path)
        path = tmp_path / "team-abc" / "agent" / "nil.yaml"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("~\n", encoding="utf-8")
        assert repo.list_by_namespace("team-abc") == []


class TestRoundTrip:
    """AC20, AC21: no re-dumping; ref markers and __type__ round-trip at depth."""

    def test_mixed_inline_and_ref_payload_round_trips(self, tmp_path: Path) -> None:
        """AC20: inline + ref payload survives put/get byte-exactly (dict equality)."""
        repo = YamlEntryRepository(tmp_path)
        payload: dict[str, Any] = {
            "config": {
                "model_cfg": {"__ref__": "id_gpt_41"},
                "name": "Helper",
                "description": "",  # explicit empty must survive (no dropping)
            }
        }
        entry = make_entry(namespace="ns", id="agent", kind="agent", payload=payload)
        repo.put(entry)
        got = repo.get("ns", "agent")
        assert got is not None
        assert got.payload == payload

    def test_nested_type_key_plus_ref_round_trip(self, tmp_path: Path) -> None:
        """AC21: __type__ alongside __ref__ survives in list positions."""
        repo = YamlEntryRepository(tmp_path)
        payload: dict[str, Any] = {
            "config": {
                "tools": [
                    {"__ref__": "id_tool_a"},
                    {"__ref__": "id_tool_b", "__type__": "akgentic.tool.ToolCard"},
                ]
            }
        }
        entry = make_entry(namespace="ns", id="agent", kind="agent", payload=payload)
        repo.put(entry)
        got = repo.get("ns", "agent")
        assert got is not None
        assert got.payload == payload


class TestReadConsistency:
    """AC22, AC23: writes invalidate cache; reload is a public invalidator."""

    def test_second_put_is_visible_on_next_read(self, tmp_path: Path) -> None:
        """AC22: put -> read -> put -> read yields the second put's state."""
        repo = YamlEntryRepository(tmp_path)
        first = make_entry(namespace="ns", id="e1", kind="tool", payload={"v": 1})
        repo.put(first)
        read1 = repo.get("ns", "e1")
        assert read1 is not None and read1.payload == {"v": 1}
        second = make_entry(namespace="ns", id="e1", kind="tool", payload={"v": 2})
        repo.put(second)
        read2 = repo.get("ns", "e1")
        assert read2 is not None and read2.payload == {"v": 2}

    def test_reload_with_no_args_is_callable(self, tmp_path: Path) -> None:
        """AC23: reload(None) works and does not raise."""
        repo = YamlEntryRepository(tmp_path)
        repo.put(make_entry(namespace="ns", id="e1", kind="tool"))
        repo.reload()
        # After reload, reads still work.
        assert repo.get("ns", "e1") is not None

    def test_reload_with_namespace_is_callable(self, tmp_path: Path) -> None:
        """AC23: reload("ns") works and does not raise (missing key is fine)."""
        repo = YamlEntryRepository(tmp_path)
        repo.reload("never-touched")  # Missing-key branch.
        repo.put(make_entry(namespace="ns", id="e1", kind="tool"))
        repo.reload("ns")
        assert repo.get("ns", "e1") is not None


class TestMetaEntryRoundTrip:
    """Story 17.2 — YAML repo round-trips a ``kind="meta"`` entry."""

    def test_meta_entry_round_trips(self, tmp_path: Path) -> None:
        repo = YamlEntryRepository(tmp_path)
        meta = make_entry(
            id="_meta",
            kind="meta",
            namespace="tenant-42",
            user_id="alice",
            model_type="akgentic.catalog.models.namespace_meta.NamespaceMeta",
            description="namespace meta",
            payload={"name": "Tenant 42", "description": "primary tenant", "properties": {}},
        )
        repo.put(meta)
        # File lands under root/{namespace}/meta/{id}.yaml.
        path = tmp_path / "tenant-42" / "meta" / "_meta.yaml"
        assert path.exists()
        # All three readers return it.
        assert repo.get("tenant-42", "_meta") == meta
        rows = repo.list_by_namespace("tenant-42")
        assert any(e.id == "_meta" and e.kind == "meta" for e in rows)
        assert repo.get_by_kind("tenant-42", "meta") == meta

    def test_meta_entry_public_field_round_trips_typed_bool(self, tmp_path: Path) -> None:
        # Story 18.2 AC7 — ``public=True`` survives the byte-identical
        # write/read cycle as a typed Python bool (no truthy-string coercion).
        repo = YamlEntryRepository(tmp_path)
        meta = make_entry(
            id="_meta",
            kind="meta",
            namespace="tenant-pub",
            user_id="alice",
            model_type="akgentic.catalog.models.namespace_meta.NamespaceMeta",
            description="public namespace",
            payload={
                "name": "Tenant Pub",
                "description": "",
                "properties": {},
                "shareable": False,
                "public": True,
            },
        )
        repo.put(meta)
        # On-disk YAML uses PyYAML's default emit shape: ``public: true``
        # (lowercase, unquoted) — not ``True``, not ``"true"``.
        path = tmp_path / "tenant-pub" / "meta" / "_meta.yaml"
        text = path.read_text(encoding="utf-8")
        assert "public: true" in text
        # Read-back surfaces the value as a typed bool.
        loaded = repo.get("tenant-pub", "_meta")
        assert loaded is not None
        assert isinstance(loaded.payload, dict)
        assert loaded.payload["public"] is True

    def test_meta_entry_public_false_round_trips(self, tmp_path: Path) -> None:
        # Symmetry — ``public=False`` round-trips as a typed bool too.
        repo = YamlEntryRepository(tmp_path)
        meta = make_entry(
            id="_meta",
            kind="meta",
            namespace="tenant-priv",
            user_id="alice",
            model_type="akgentic.catalog.models.namespace_meta.NamespaceMeta",
            description="private namespace",
            payload={
                "name": "Tenant Priv",
                "description": "",
                "properties": {},
                "shareable": False,
                "public": False,
            },
        )
        repo.put(meta)
        loaded = repo.get("tenant-priv", "_meta")
        assert loaded is not None
        assert isinstance(loaded.payload, dict)
        assert loaded.payload["public"] is False


class TestUserIdAnonymousPersistedShape:
    """Story 18.1 / AC4 — entries with the default ``user_id`` persist as
    ``user_id: anonymous`` (a bare string), not ``null`` or ``~``.
    """

    def test_default_user_id_writes_anonymous_to_yaml_file(self, tmp_path: Path) -> None:
        repo = YamlEntryRepository(tmp_path)
        repo.put(make_entry(id="e1", kind="tool", namespace="ns-anon"))
        path = tmp_path / "ns-anon" / "tool" / "e1.yaml"
        text = path.read_text(encoding="utf-8")
        assert "user_id: anonymous" in text
        assert "user_id: null" not in text
        assert "user_id: ~" not in text


class TestStaleLineageKeysAreSilentlyDropped:
    """ADR-010 back-compat — pre-existing YAML files carrying ``parent_namespace`` /
    ``parent_id`` keys must still load cleanly.

    Pydantic's default ``extra='ignore'`` silently drops the unknown kwargs;
    the loaded ``Entry`` has neither field, and a subsequent ``put`` writes
    the seven-field shape (no lineage keys in the on-disk dump).
    """

    def test_stale_lineage_keys_on_disk_load_successfully(self, tmp_path: Path) -> None:
        repo = YamlEntryRepository(tmp_path)
        # Hand-write a YAML file carrying both stale lineage keys (the pre-ADR-010
        # shape an operator may have on disk).
        target_dir = tmp_path / "ns-legacy" / "tool"
        target_dir.mkdir(parents=True)
        legacy_text = (
            "id: legacy-tool\n"
            "kind: tool\n"
            "namespace: ns-legacy\n"
            "user_id: anonymous\n"
            "parent_namespace: src-ns\n"
            "parent_id: src-id\n"
            "model_type: akgentic.tool.search.SearchTool\n"
            "description: legacy entry with stale lineage keys\n"
            "payload:\n"
            "  name: Legacy Tool\n"
            "  description: legacy entry with stale lineage keys\n"
        )
        (target_dir / "legacy-tool.yaml").write_text(legacy_text, encoding="utf-8")

        # Loading should succeed; the loaded Entry has neither lineage attribute.
        loaded = repo.get("ns-legacy", "legacy-tool")
        assert loaded is not None
        dumped = loaded.model_dump()
        assert "parent_namespace" not in dumped
        assert "parent_id" not in dumped

    def test_put_emits_seven_field_shape(self, tmp_path: Path) -> None:
        """A round-trip writes only the seven Entry fields — no lineage keys."""
        repo = YamlEntryRepository(tmp_path)
        repo.put(make_entry(id="fresh", kind="tool", namespace="ns-fresh"))
        path = tmp_path / "ns-fresh" / "tool" / "fresh.yaml"
        text = path.read_text(encoding="utf-8")
        loaded = yaml.safe_load(text)
        assert set(loaded.keys()) == {
            "id",
            "kind",
            "namespace",
            "user_id",
            "model_type",
            "description",
            "payload",
        }
