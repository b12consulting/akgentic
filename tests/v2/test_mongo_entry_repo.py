"""Integration tests for ``MongoEntryRepository`` (Story 15.4).

Covers AC3-AC20, AC22, AC23. AC1/AC2 (importability + re-export) are validated
by module-level import checks below. AC21 (cross-backend parity) lives in
``test_entry_repo_parity.py``. AC24-AC28 are quality-gate ACs validated by
ruff, mypy, pytest, and CI themselves.

Every test uses the ``entries_collection`` fixture from ``conftest.py`` — a
fresh mongomock-backed collection per test, no shared state.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest

pytest.importorskip("pymongo")  # belt-and-braces: package already pulls pymongo in dev extras

from akgentic.catalog import MongoEntryRepository as PublicMongoEntryRepository  # noqa: E402
from akgentic.catalog.models.entry import Entry  # noqa: E402
from akgentic.catalog.models.queries import EntryQuery  # noqa: E402
from akgentic.catalog.repositories.base import EntryRepository  # noqa: E402
from akgentic.catalog.repositories.mongo import MongoEntryRepository  # noqa: E402

from .conftest import make_entry  # noqa: E402

if TYPE_CHECKING:
    import pymongo.collection

# AC3: structural-protocol check at module load time — mypy strict must accept.
_protocol_check: type[EntryRepository] = MongoEntryRepository


class TestPackageReExport:
    """AC2: the top-level package re-exports ``MongoEntryRepository`` when pymongo is installed."""

    def test_public_reexport_is_the_same_class(self) -> None:
        """``from akgentic.catalog import MongoEntryRepository`` resolves to the same class."""
        assert PublicMongoEntryRepository is MongoEntryRepository


class TestProtocolConformance:
    """AC3-AC4: repository satisfies ``EntryRepository`` structurally; no pymongo at import."""

    def test_instance_exposes_every_protocol_method(
        self,
        entries_collection: pymongo.collection.Collection,  # type: ignore[type-arg]
    ) -> None:
        """All seven protocol methods exist and are callable on a live instance."""
        repo = MongoEntryRepository(entries_collection)
        assert callable(repo.get)
        assert callable(repo.put)
        assert callable(repo.delete)
        assert callable(repo.list)
        assert callable(repo.list_by_namespace)
        assert callable(repo.get_by_kind)
        assert callable(repo.find_references)

    def test_importable_without_touching_pymongo_collection_api(self) -> None:
        """AC4: the module imports cleanly; top-level surface stays pymongo-free."""
        import akgentic.catalog.repositories.mongo as module

        # MongoEntryRepository is exported; no pymongo name is shadowed at module level.
        assert hasattr(module, "MongoEntryRepository")


class TestConstruction:
    """AC5: constructor signature and zero-I/O construction semantics."""

    def test_construct_does_not_touch_the_collection(
        self,
        entries_collection: pymongo.collection.Collection,  # type: ignore[type-arg]
    ) -> None:
        """``__init__`` performs no I/O — no documents, no indexes touched."""
        with patch.object(entries_collection, "create_index") as create_index_spy:
            repo = MongoEntryRepository(entries_collection)
            # No writes happened — collection empty, create_index never invoked.
            assert entries_collection.count_documents({}) == 0
            assert create_index_spy.call_count == 0
            # The stored collection is the passed object, not a coerced variant.
            assert repo._collection is entries_collection
            assert repo._indexes_created is False


class TestPutWrites:
    """AC6-AC9, AC19: document shape, put return value, upsert semantics, no re-dumping."""

    def test_put_writes_compound_id_document(
        self,
        entries_collection: pymongo.collection.Collection,  # type: ignore[type-arg]
    ) -> None:
        """AC6: put writes a compound-``_id`` doc with duplicated top-level ``namespace``+``id``."""
        repo = MongoEntryRepository(entries_collection)
        entry = make_entry(
            id="assistant",
            kind="agent",
            namespace="team-abc",
            model_type="akgentic.core.agent_card.AgentCard",
            description="Helpful assistant",
            payload={"role": "assistant", "description": "Helpful assistant", "skills": []},
        )
        repo.put(entry)

        doc = entries_collection.find_one(
            {"_id": {"namespace": "team-abc", "id": "assistant"}},
        )
        assert doc is not None
        assert doc["_id"] == {"namespace": "team-abc", "id": "assistant"}
        assert doc["namespace"] == "team-abc"
        assert doc["id"] == "assistant"
        assert doc["kind"] == "agent"
        assert doc["user_id"] == "anonymous"
        assert "parent_namespace" not in doc
        assert "parent_id" not in doc
        assert doc["model_type"] == "akgentic.core.agent_card.AgentCard"
        assert doc["description"] == "Helpful assistant"
        assert doc["payload"] == {
            "role": "assistant",
            "description": "Helpful assistant",
            "skills": [],
        }

    def test_put_returns_the_stored_entry(
        self,
        entries_collection: pymongo.collection.Collection,  # type: ignore[type-arg]
    ) -> None:
        """AC7: put returns an Entry equal to the input (identity is not required)."""
        repo = MongoEntryRepository(entries_collection)
        entry = make_entry(id="a", kind="tool", namespace="ns-1")

        returned = repo.put(entry)

        assert returned == entry

    def test_put_is_upsert_second_write_wins(
        self,
        entries_collection: pymongo.collection.Collection,  # type: ignore[type-arg]
    ) -> None:
        """AC8: put with the same (namespace, id) replaces the existing doc in place."""
        repo = MongoEntryRepository(entries_collection)
        first = make_entry(
            id="assistant",
            kind="agent",
            namespace="team-abc",
            description="first",
            payload={"v": 1},
        )
        second = make_entry(
            id="assistant",
            kind="agent",
            namespace="team-abc",
            description="second",
            payload={"v": 2},
        )

        repo.put(first)
        repo.put(second)

        assert entries_collection.count_documents({}) == 1
        doc = entries_collection.find_one({"_id": {"namespace": "team-abc", "id": "assistant"}})
        assert doc is not None
        assert doc["description"] == "second"
        assert doc["payload"] == {"v": 2}

    def test_put_kind_change_updates_same_document(
        self,
        entries_collection: pymongo.collection.Collection,  # type: ignore[type-arg]
    ) -> None:
        """AC9: kind change preserves single doc at the compound ``_id``."""
        repo = MongoEntryRepository(entries_collection)
        as_agent = make_entry(
            id="assistant", kind="agent", namespace="team-abc", payload={"role": "a"}
        )
        as_tool = make_entry(
            id="assistant", kind="tool", namespace="team-abc", payload={"role": "t"}
        )

        repo.put(as_agent)
        repo.put(as_tool)

        assert entries_collection.count_documents({}) == 1
        got = repo.get("team-abc", "assistant")
        assert got is not None
        assert got.kind == "tool"
        assert got.payload == {"role": "t"}

    def test_put_does_not_expand_ref_markers(
        self,
        entries_collection: pymongo.collection.Collection,  # type: ignore[type-arg]
    ) -> None:
        """AC19: repository never re-dumps payload; ``__ref__`` survives byte-for-byte."""
        repo = MongoEntryRepository(entries_collection)
        payload = {
            "config": {
                "model_cfg": {"__ref__": "id_gpt_41"},
                "name": "Helper",
            }
        }
        entry = make_entry(id="a", kind="agent", namespace="ns-1", payload=payload)

        repo.put(entry)

        doc = entries_collection.find_one({"_id": {"namespace": "ns-1", "id": "a"}})
        assert doc is not None
        assert doc["payload"] == payload


class TestLazyIndexes:
    """AC10, AC23: first put creates the secondary index, once and only once."""

    def test_first_put_creates_two_indexes(
        self,
        entries_collection: pymongo.collection.Collection,  # type: ignore[type-arg]
    ) -> None:
        """AC10: first put invokes create_index once with the expected spec."""
        from pymongo import ASCENDING

        repo = MongoEntryRepository(entries_collection)
        entry = make_entry(id="a", kind="tool", namespace="ns-1")

        with patch.object(entries_collection, "create_index") as spy:
            repo.put(entry)

        assert spy.call_count == 1
        specs = [call.args[0] for call in spy.call_args_list]
        assert [("namespace", ASCENDING), ("kind", ASCENDING)] in specs

    def test_second_put_does_not_recreate_indexes(
        self,
        entries_collection: pymongo.collection.Collection,  # type: ignore[type-arg]
    ) -> None:
        """AC10: the one-shot flag prevents re-invoking create_index on subsequent writes."""
        repo = MongoEntryRepository(entries_collection)
        repo.put(make_entry(id="a", kind="tool", namespace="ns-1"))

        with patch.object(entries_collection, "create_index") as spy:
            repo.put(make_entry(id="b", kind="tool", namespace="ns-1"))

        assert spy.call_count == 0

    def test_read_only_repo_never_calls_create_index(
        self,
        entries_collection: pymongo.collection.Collection,  # type: ignore[type-arg]
    ) -> None:
        """AC10: a repo that is only queried (no writes) does not create any index."""
        with patch.object(entries_collection, "create_index") as spy:
            repo = MongoEntryRepository(entries_collection)
            repo.get("ns-1", "a")
            repo.list(EntryQuery(namespace="ns-1"))
            repo.list_by_namespace("ns-1")
            repo.get_by_kind("ns-1", "tool")
            repo.find_references("ns-1", "id_gpt_41")

        assert spy.call_count == 0


class TestGet:
    """AC11: get hits, misses, and cross-namespace misses."""

    def test_get_returns_existing_entry(
        self,
        entries_collection: pymongo.collection.Collection,  # type: ignore[type-arg]
    ) -> None:
        repo = MongoEntryRepository(entries_collection)
        entry = make_entry(id="assistant", kind="agent", namespace="team-abc", payload={"k": "v"})
        repo.put(entry)

        got = repo.get("team-abc", "assistant")

        assert got == entry

    def test_get_returns_none_on_miss(
        self,
        entries_collection: pymongo.collection.Collection,  # type: ignore[type-arg]
    ) -> None:
        repo = MongoEntryRepository(entries_collection)
        repo.put(make_entry(id="assistant", kind="agent", namespace="team-abc"))

        assert repo.get("team-abc", "unknown") is None

    def test_get_respects_namespace_isolation(
        self,
        entries_collection: pymongo.collection.Collection,  # type: ignore[type-arg]
    ) -> None:
        repo = MongoEntryRepository(entries_collection)
        repo.put(make_entry(id="assistant", kind="agent", namespace="team-abc"))

        assert repo.get("other-namespace", "assistant") is None


class TestDelete:
    """AC12: delete removes the document and is idempotent on miss."""

    def test_delete_removes_document(
        self,
        entries_collection: pymongo.collection.Collection,  # type: ignore[type-arg]
    ) -> None:
        repo = MongoEntryRepository(entries_collection)
        repo.put(make_entry(id="assistant", kind="agent", namespace="team-abc"))

        repo.delete("team-abc", "assistant")

        assert entries_collection.count_documents({}) == 0
        assert repo.get("team-abc", "assistant") is None

    def test_delete_is_idempotent_on_miss(
        self,
        entries_collection: pymongo.collection.Collection,  # type: ignore[type-arg]
    ) -> None:
        repo = MongoEntryRepository(entries_collection)
        # No setup; delete must not raise.
        repo.delete("team-abc", "missing")


class TestList:
    """AC13: list applies every filter via a server-side Mongo filter."""

    def _seed(
        self,
        collection: pymongo.collection.Collection,  # type: ignore[type-arg]
    ) -> MongoEntryRepository:
        repo = MongoEntryRepository(collection)
        repo.put(
            make_entry(
                id="alice",
                kind="agent",
                namespace="ns-1",
                user_id="alice",
                description="first agent",
                payload={},
            )
        )
        repo.put(
            make_entry(
                id="bob",
                kind="agent",
                namespace="ns-1",
                user_id="anonymous",
                description="second agent",
                payload={},
            )
        )
        repo.put(
            make_entry(
                id="hammer",
                kind="tool",
                namespace="ns-1",
                user_id="anonymous",
                description="a tool for striking",
                payload={},
            )
        )
        repo.put(
            make_entry(
                id="carol",
                kind="agent",
                namespace="ns-2",
                user_id="carol",
                description="other namespace",
                payload={},
            )
        )
        return repo

    def test_list_filters_by_namespace(
        self,
        entries_collection: pymongo.collection.Collection,  # type: ignore[type-arg]
    ) -> None:
        repo = self._seed(entries_collection)
        got = repo.list(EntryQuery(namespace="ns-1"))
        assert {e.id for e in got} == {"alice", "bob", "hammer"}

    def test_list_filters_by_kind(
        self,
        entries_collection: pymongo.collection.Collection,  # type: ignore[type-arg]
    ) -> None:
        repo = self._seed(entries_collection)
        got = repo.list(EntryQuery(kind="tool"))
        assert [e.id for e in got] == ["hammer"]

    def test_list_filters_by_id_exact_match(
        self,
        entries_collection: pymongo.collection.Collection,  # type: ignore[type-arg]
    ) -> None:
        repo = self._seed(entries_collection)
        got = repo.list(EntryQuery(id="alice"))
        assert [e.namespace for e in got] == ["ns-1"]
        # Exact, not substring — "ali" must not match "alice".
        assert repo.list(EntryQuery(id="ali")) == []

    def test_list_filters_by_user_id(
        self,
        entries_collection: pymongo.collection.Collection,  # type: ignore[type-arg]
    ) -> None:
        repo = self._seed(entries_collection)
        got = repo.list(EntryQuery(user_id="alice"))
        assert [e.id for e in got] == ["alice"]

    def test_list_user_id_set_tri_state(
        self,
        entries_collection: pymongo.collection.Collection,  # type: ignore[type-arg]
    ) -> None:
        repo = self._seed(entries_collection)
        # None = no filter — all 4 entries.
        assert len(repo.list(EntryQuery(user_id_set=None))) == 4
        # True = user_id != "anonymous" — alice, carol.
        assert {e.id for e in repo.list(EntryQuery(user_id_set=True))} == {"alice", "carol"}
        # False = user_id == "anonymous" — bob, hammer.
        assert {e.id for e in repo.list(EntryQuery(user_id_set=False))} == {"bob", "hammer"}

    def test_list_description_contains_case_sensitive(
        self,
        entries_collection: pymongo.collection.Collection,  # type: ignore[type-arg]
    ) -> None:
        repo = self._seed(entries_collection)
        got = repo.list(EntryQuery(description_contains="agent"))
        # "first agent" + "second agent" — tool striking / other namespace excluded.
        assert {e.id for e in got} == {"alice", "bob"}
        # Case-sensitive — "AGENT" must not match.
        assert repo.list(EntryQuery(description_contains="AGENT")) == []

    def test_list_description_contains_defuses_regex_metacharacters(
        self,
        entries_collection: pymongo.collection.Collection,  # type: ignore[type-arg]
    ) -> None:
        """re.escape neutralises regex metacharacters so user strings match literally."""
        repo = MongoEntryRepository(entries_collection)
        repo.put(
            make_entry(
                id="x",
                kind="tool",
                namespace="ns",
                description="price is $99.",
                payload={},
            )
        )
        # "$" is a regex anchor; re.escape makes it literal.
        assert [e.id for e in repo.list(EntryQuery(description_contains="$99"))] == ["x"]
        # "." is a regex wildcard; re.escape makes it literal.
        assert [e.id for e in repo.list(EntryQuery(description_contains="99."))] == ["x"]

    def test_list_combines_filters_with_and(
        self,
        entries_collection: pymongo.collection.Collection,  # type: ignore[type-arg]
    ) -> None:
        repo = self._seed(entries_collection)
        got = repo.list(
            EntryQuery(namespace="ns-1", kind="agent", user_id_set=False),
        )
        # ns-1 AND agent AND user_id="anonymous" → only bob.
        assert [e.id for e in got] == ["bob"]

    def test_list_user_id_and_user_id_set_combine_with_and(
        self,
        entries_collection: pymongo.collection.Collection,  # type: ignore[type-arg]
    ) -> None:
        """user_id and user_id_set combine conjunctively — parity with YAML's _matches."""
        repo = self._seed(entries_collection)
        # user_id="alice" AND user_id_set=True → alice satisfies both (her user_id is non-None).
        got = repo.list(EntryQuery(user_id="alice", user_id_set=True))
        assert {e.id for e in got} == {"alice"}
        # user_id="alice" AND user_id_set=False → contradiction; zero entries.
        got = repo.list(EntryQuery(user_id="alice", user_id_set=False))
        assert got == []


class TestListByNamespace:
    """AC14: list_by_namespace returns every entry in one find call."""

    def test_returns_every_entry_in_namespace(
        self,
        entries_collection: pymongo.collection.Collection,  # type: ignore[type-arg]
    ) -> None:
        repo = MongoEntryRepository(entries_collection)
        repo.put(make_entry(id="t1", kind="team", namespace="team-abc"))
        repo.put(make_entry(id="a1", kind="agent", namespace="team-abc"))
        repo.put(make_entry(id="a2", kind="agent", namespace="team-abc"))
        repo.put(make_entry(id="tool1", kind="tool", namespace="team-abc"))
        repo.put(make_entry(id="p1", kind="prompt", namespace="team-abc"))
        # Cross-namespace noise that must not leak in.
        repo.put(make_entry(id="other", kind="agent", namespace="team-xyz"))

        got = repo.list_by_namespace("team-abc")

        assert {e.id for e in got} == {"t1", "a1", "a2", "tool1", "p1"}

    def test_missing_namespace_returns_empty_list(
        self,
        entries_collection: pymongo.collection.Collection,  # type: ignore[type-arg]
    ) -> None:
        repo = MongoEntryRepository(entries_collection)
        assert repo.list_by_namespace("nonexistent") == []

    def test_issues_exactly_one_find_call(
        self,
        entries_collection: pymongo.collection.Collection,  # type: ignore[type-arg]
    ) -> None:
        """AC14: one find call, no per-kind fan-out."""
        repo = MongoEntryRepository(entries_collection)
        repo.put(make_entry(id="a", kind="tool", namespace="ns-1"))

        real_find = entries_collection.find
        with patch.object(entries_collection, "find", wraps=real_find) as spy:
            repo.list_by_namespace("ns-1")
        assert spy.call_count == 1


class TestGetByKind:
    """AC15: get_by_kind returns the singleton or None; deterministic on duplicates."""

    def test_returns_singleton_entry(
        self,
        entries_collection: pymongo.collection.Collection,  # type: ignore[type-arg]
    ) -> None:
        repo = MongoEntryRepository(entries_collection)
        team = make_entry(id="team", kind="team", namespace="team-abc")
        repo.put(team)
        repo.put(make_entry(id="a1", kind="agent", namespace="team-abc"))

        got = repo.get_by_kind("team-abc", "team")

        assert got == team

    def test_returns_none_when_no_match(
        self,
        entries_collection: pymongo.collection.Collection,  # type: ignore[type-arg]
    ) -> None:
        repo = MongoEntryRepository(entries_collection)
        repo.put(make_entry(id="a1", kind="agent", namespace="team-abc"))

        assert repo.get_by_kind("team-abc", "team") is None

    def test_returns_alphabetically_first_on_duplicates(
        self,
        entries_collection: pymongo.collection.Collection,  # type: ignore[type-arg]
    ) -> None:
        """Two docs sharing (namespace, kind) — return lowest ``_id.id``, no raise."""
        repo = MongoEntryRepository(entries_collection)
        # Insert b first to prove order is by id, not by insert order.
        repo.put(make_entry(id="b", kind="agent", namespace="team-abc"))
        repo.put(make_entry(id="a", kind="agent", namespace="team-abc"))

        got = repo.get_by_kind("team-abc", "agent")

        assert got is not None
        assert got.id == "a"


class TestFindReferences:
    """AC16: find_references walks payloads in memory with the shared helper."""

    def test_finds_ref_at_nested_dict_depth(
        self,
        entries_collection: pymongo.collection.Collection,  # type: ignore[type-arg]
    ) -> None:
        repo = MongoEntryRepository(entries_collection)
        repo.put(
            make_entry(
                id="agent-a",
                kind="agent",
                namespace="team-abc",
                payload={"config": {"model_cfg": {"__ref__": "id_gpt_41"}}},
            )
        )
        repo.put(
            make_entry(
                id="agent-b",
                kind="agent",
                namespace="team-abc",
                payload={"config": {"tools": [{"__ref__": "id_gpt_41"}]}},
            )
        )
        repo.put(
            make_entry(
                id="agent-c",
                kind="agent",
                namespace="team-abc",
                payload={"config": {"model_cfg": {"__ref__": "id_other"}}},
            )
        )

        got = repo.find_references("team-abc", "id_gpt_41")

        assert {e.id for e in got} == {"agent-a", "agent-b"}

    def test_finds_ref_inside_list_element(
        self,
        entries_collection: pymongo.collection.Collection,  # type: ignore[type-arg]
    ) -> None:
        repo = MongoEntryRepository(entries_collection)
        repo.put(
            make_entry(
                id="agent",
                kind="agent",
                namespace="ns-1",
                payload={
                    "config": {
                        "prompt": {"params": {"X": {"__ref__": "id_tool_a"}}},
                    }
                },
            )
        )

        got = repo.find_references("ns-1", "id_tool_a")

        assert [e.id for e in got] == ["agent"]

    def test_standalone_string_ref_key_is_not_matched(
        self,
        entries_collection: pymongo.collection.Collection,  # type: ignore[type-arg]
    ) -> None:
        """Only dict keys named ``__ref__`` count — a plain string value does not."""
        repo = MongoEntryRepository(entries_collection)
        repo.put(
            make_entry(
                id="noise",
                kind="agent",
                namespace="ns-1",
                payload={"text": "__ref__"},
            )
        )

        assert repo.find_references("ns-1", "__ref__") == []

    def test_respects_namespace_isolation(
        self,
        entries_collection: pymongo.collection.Collection,  # type: ignore[type-arg]
    ) -> None:
        repo = MongoEntryRepository(entries_collection)
        repo.put(
            make_entry(
                id="a",
                kind="agent",
                namespace="ns-a",
                payload={"__ref__": "target"},
            )
        )
        repo.put(
            make_entry(
                id="b",
                kind="agent",
                namespace="ns-b",
                payload={"__ref__": "target"},
            )
        )

        got_a = repo.find_references("ns-a", "target")
        got_b = repo.find_references("ns-b", "target")

        assert [e.id for e in got_a] == ["a"]
        assert [e.id for e in got_b] == ["b"]

    def test_uses_shared_helper_from_yaml_module(self) -> None:
        """Import-path assertion: the walker comes from ``repositories.yaml``."""
        from akgentic.catalog.repositories import mongo
        from akgentic.catalog.repositories.yaml import _payload_has_ref

        assert mongo._payload_has_ref is _payload_has_ref


class TestNamespaceIsolation:
    """AC17: same id across two namespaces coexists; cross-namespace bleed never happens."""

    def test_same_id_in_two_namespaces_coexists(
        self,
        entries_collection: pymongo.collection.Collection,  # type: ignore[type-arg]
    ) -> None:
        repo = MongoEntryRepository(entries_collection)
        a = make_entry(id="shared", kind="agent", namespace="ns-a", payload={"which": "a"})
        b = make_entry(id="shared", kind="agent", namespace="ns-b", payload={"which": "b"})

        repo.put(a)
        repo.put(b)

        assert entries_collection.count_documents({}) == 2
        assert repo.get("ns-a", "shared") == a
        assert repo.get("ns-b", "shared") == b
        assert [e.id for e in repo.list_by_namespace("ns-a")] == ["shared"]
        assert [e.id for e in repo.list_by_namespace("ns-b")] == ["shared"]


class TestRoundTrip:
    """AC18, AC20: ``Entry.model_validate`` round-trip preserves every field and nested refs."""

    def test_round_trip_reconstructs_entry_exactly(
        self,
        entries_collection: pymongo.collection.Collection,  # type: ignore[type-arg]
    ) -> None:
        repo = MongoEntryRepository(entries_collection)
        entry = make_entry(
            id="assistant",
            kind="agent",
            namespace="team-abc",
            user_id="geoff",
            model_type="akgentic.core.agent_card.AgentCard",
            description="Helpful",
            payload={"role": "assistant", "skills": ["a", "b"]},
        )
        repo.put(entry)

        got = repo.get("team-abc", "assistant")

        assert got == entry
        assert isinstance(got, Entry)
        assert got.user_id == "geoff"
        assert got.payload == {"role": "assistant", "skills": ["a", "b"]}

    def test_nested_ref_markers_round_trip_at_arbitrary_depth(
        self,
        entries_collection: pymongo.collection.Collection,  # type: ignore[type-arg]
    ) -> None:
        """AC20: ``__ref__`` and ``__type__`` survive nested dicts and list elements."""
        repo = MongoEntryRepository(entries_collection)
        payload = {
            "config": {
                "tools": [
                    {"__ref__": "id_tool_a"},
                    {"__ref__": "id_tool_b", "__type__": "akgentic.tool.ToolCard"},
                ]
            }
        }
        entry = make_entry(id="agent", kind="agent", namespace="ns-1", payload=payload)
        repo.put(entry)

        got = repo.get("ns-1", "agent")

        assert got is not None
        assert got.payload == payload


class TestMongoCatalogConfigUnifiedCollection:
    """Story 19.2 AC #5 option (a): ``catalog_entries_collection`` field."""

    def test_catalog_entries_collection_defaults_to_catalog_entries(self) -> None:
        """The field defaults to ``"catalog_entries"`` when not provided."""
        from akgentic.catalog.repositories.mongo import MongoCatalogConfig

        config = MongoCatalogConfig(
            connection_string="mongodb://localhost:27017",
            database="catalog",
        )
        assert config.catalog_entries_collection == "catalog_entries"

    def test_catalog_entries_collection_accepts_override(self) -> None:
        """A caller can override the collection name at construction."""
        from akgentic.catalog.repositories.mongo import MongoCatalogConfig

        config = MongoCatalogConfig(
            connection_string="mongodb://localhost:27017",
            database="catalog",
            catalog_entries_collection="custom_entries",
        )
        assert config.catalog_entries_collection == "custom_entries"


class TestMetaEntryRoundTrip:
    """Story 17.2 — Mongo repo round-trips a ``kind="meta"`` entry."""

    def test_meta_entry_round_trips(
        self,
        entries_collection: pymongo.collection.Collection,  # type: ignore[type-arg]
    ) -> None:
        repo = MongoEntryRepository(entries_collection)
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
        # All three readers return the meta entry.
        assert repo.get("tenant-42", "_meta") == meta
        rows = repo.list_by_namespace("tenant-42")
        assert any(e.id == "_meta" and e.kind == "meta" for e in rows)
        assert repo.get_by_kind("tenant-42", "meta") == meta

    def test_meta_entry_public_field_round_trips_as_bson_bool(
        self,
        entries_collection: pymongo.collection.Collection,  # type: ignore[type-arg]
    ) -> None:
        # Story 18.2 AC7 — ``public=True`` survives the byte-identical
        # write/read cycle as a BSON boolean (not a string).
        repo = MongoEntryRepository(entries_collection)
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
        # Inspect the BSON document directly — the payload.public field is
        # stored as a Python bool (mapped to BSON boolean by pymongo).
        doc = entries_collection.find_one({"_id": {"namespace": "tenant-pub", "id": "_meta"}})
        assert doc is not None
        assert doc["payload"]["public"] is True
        assert isinstance(doc["payload"]["public"], bool)
        # Read-back via the repository surfaces the value as a typed bool.
        loaded = repo.get("tenant-pub", "_meta")
        assert loaded is not None
        assert isinstance(loaded.payload, dict)
        assert loaded.payload["public"] is True


class TestUserIdAnonymousPersistedShape:
    """Story 18.1 / AC5 — entries with the default ``user_id`` persist as a
    BSON string ``"anonymous"`` (not BSON null) in the Mongo document.
    """

    def test_default_user_id_writes_anonymous_to_mongo_document(
        self,
        entries_collection: pymongo.collection.Collection,  # type: ignore[type-arg]
    ) -> None:
        repo = MongoEntryRepository(entries_collection)
        repo.put(make_entry(id="e1", kind="tool", namespace="ns-anon"))
        doc = entries_collection.find_one({"_id": {"namespace": "ns-anon", "id": "e1"}})
        assert doc is not None
        assert doc["user_id"] == "anonymous"
        assert isinstance(doc["user_id"], str)
