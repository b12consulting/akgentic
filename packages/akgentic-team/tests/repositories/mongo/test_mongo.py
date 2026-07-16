"""Mongo-specific tests for ``MongoEventStore``.

Behavioural Protocol coverage (round-trip, upsert, list, sequencing,
max sequence, cascading delete, polymorphic round-trips) lives in the
shared ``tests/repositories/test_event_store_contract.py`` and runs
once per backend. This module retains only Mongo-specific invariants:

* Protocol structural-typing check.
* Corrupted-document resilience (mongo's analogue of YAML's corrupted-
  file resilience and the postgres payload-authority test).
* Import guards — ``MongoEventStore`` is only available with the
  ``[mongo]`` extra installed.
"""

from __future__ import annotations

import os
import uuid
from typing import TYPE_CHECKING, Any
from unittest.mock import patch

from akgentic.team.repositories.mongo import MongoEventStore

if TYPE_CHECKING:
    from akgentic.team.ports import EventStore

from akgentic.team.models import TeamStatus
from tests.models.conftest import (
    SampleAgentState,
    make_agent_state_snapshot,
    make_persisted_event,
    make_process,
)


class TestMongoEventStoreMongoSpecific:
    """Mongo-only invariants — see contract suite for behavioural coverage."""

    # --- Protocol compliance ------------------------------------------------

    def test_satisfies_event_store_protocol(self, mongo_store: MongoEventStore) -> None:
        """``MongoEventStore`` satisfies ``EventStore`` Protocol structurally."""
        store: EventStore = mongo_store
        assert store is not None

    # --- Corrupted-document resilience --------------------------------------

    def test_load_team_returns_none_for_corrupted_document(
        self, mongo_store: MongoEventStore, mongo_db: object
    ) -> None:
        """Corrupted team document returns None instead of raising."""
        from mongomock import Database as MockDB

        db: MockDB = mongo_db  # type: ignore[assignment]
        db["teams"].insert_one({"team_id": "bad-uuid", "status": "INVALID", "_bogus": True})
        result = mongo_store.load_team(uuid.UUID("00000000-0000-0000-0000-000000000000"))
        # No document matches, so None
        assert result is None

        # Insert a document with a valid team_id but corrupted fields
        team_id = uuid.uuid4()
        db["teams"].insert_one({"team_id": str(team_id), "not_a_valid_field": 123})
        result = mongo_store.load_team(team_id)
        assert result is None

    def test_load_events_skips_corrupted_documents(
        self, mongo_store: MongoEventStore, mongo_db: object
    ) -> None:
        """Corrupted event documents are skipped; valid ones still loaded."""
        from mongomock import Database as MockDB

        db: MockDB = mongo_db  # type: ignore[assignment]
        team_id = uuid.uuid4()

        # Insert a valid event
        valid_event = make_persisted_event(team_id=team_id, sequence=1)
        mongo_store.save_event(valid_event)

        # Insert a corrupted event document directly
        db["events"].insert_one({"team_id": str(team_id), "sequence": 2, "corrupted": True})

        loaded = mongo_store.load_events(team_id)
        assert len(loaded) == 1
        assert loaded[0].sequence == 1

    def test_load_agent_states_skips_corrupted_documents(
        self, mongo_store: MongoEventStore, mongo_db: object
    ) -> None:
        """Corrupted agent state documents are skipped; valid ones still loaded."""
        from mongomock import Database as MockDB

        db: MockDB = mongo_db  # type: ignore[assignment]
        team_id = uuid.uuid4()

        # Save a valid snapshot
        snap = make_agent_state_snapshot(team_id=team_id, agent_id="good-agent")
        mongo_store.save_agent_state(snap)

        # Insert a corrupted agent state document directly
        db["agent_states"].insert_one(
            {"team_id": str(team_id), "agent_id": "bad-agent", "corrupted": True}
        )

        loaded = mongo_store.load_agent_states(team_id)
        assert len(loaded) == 1
        assert loaded[0].agent_id == "good-agent"

    # --- user_id index presence ---------------------------------------------

    def test_teams_user_id_index_created_on_init(
        self, mongo_store: MongoEventStore, mongo_db: Any
    ) -> None:
        """``MongoEventStore.__init__`` creates ``teams_user_id_idx`` on ``teams``.

        Asserts the index is present with a single-field ascending key spec
        on ``user_id``. Backs the ``find({"user_id": ...})`` push-down in
        ``list_teams`` (ADR-16 §5).
        """
        info = mongo_db["teams"].index_information()
        assert "teams_user_id_idx" in info
        assert info["teams_user_id_idx"]["key"] == [("user_id", 1)]

    def test_teams_user_id_index_creation_is_idempotent(
        self, mongo_store: MongoEventStore, mongo_db: Any
    ) -> None:
        """Re-running the constructor against the same database does not raise.

        PyMongo's ``create_index`` returns silently when an index with the
        same key spec already exists, so redeploys against a long-lived
        MongoDB are safe (ADR-16 §5). After the second construction the
        ``teams_user_id_idx`` entry is still present exactly once with the
        same key spec.
        """
        # First construction already happened via the ``mongo_store`` fixture.
        MongoEventStore(mongo_db)  # second construction — must not raise

        info = mongo_db["teams"].index_information()
        assert info["teams_user_id_idx"]["key"] == [("user_id", 1)]
        assert list(info.keys()).count("teams_user_id_idx") == 1

    # --- event.id index presence --------------------------------------------

    def test_events_team_event_id_index_created_on_init(
        self, mongo_store: MongoEventStore, mongo_db: Any
    ) -> None:
        """``__init__`` creates ``events_team_event_id_idx`` on ``events``.

        Compound ascending key on ``team_id`` + the nested ``event.id``,
        backing the anchor lookup in ``load_events(after_event_id=...)``
        (ADR-21 §5). Deliberately not unique: a unique index would turn a
        read-path ambiguity into a write-path ``DuplicateKeyError`` on
        ``save_event``.
        """
        info = mongo_db["events"].index_information()
        assert "events_team_event_id_idx" in info
        assert info["events_team_event_id_idx"]["key"] == [("team_id", 1), ("event.id", 1)]
        assert not info["events_team_event_id_idx"].get("unique")

    def test_events_team_event_id_index_creation_is_idempotent(
        self, mongo_store: MongoEventStore, mongo_db: Any
    ) -> None:
        """Re-running the constructor against the same database does not raise.

        ``create_index`` returns silently when an index with the same key
        spec already exists, so redeploys against a long-lived MongoDB are
        safe. After the second construction the entry is still present
        exactly once with the same key spec.
        """
        # First construction already happened via the ``mongo_store`` fixture.
        MongoEventStore(mongo_db)  # second construction — must not raise

        info = mongo_db["events"].index_information()
        assert info["events_team_event_id_idx"]["key"] == [("team_id", 1), ("event.id", 1)]
        assert list(info.keys()).count("events_team_event_id_idx") == 1

    def test_existing_events_team_sequence_index_still_created(
        self, mongo_store: MongoEventStore, mongo_db: Any
    ) -> None:
        """The pre-existing ``(team_id, sequence)`` index is untouched.

        It already backs the ``{"sequence": {"$gt": n}}`` range filter and
        needs no change for the cursor push-down.
        """
        specs = [entry["key"] for entry in mongo_db["events"].index_information().values()]
        assert [("team_id", 1), ("sequence", 1)] in specs

    # --- Collection name configuration --------------------------------------

    def test_collection_names_default_when_env_unset(
        self, mongo_db: Any, monkeypatch: Any
    ) -> None:
        """With the override env vars unset, the default collection names are used.

        Persists one document of each kind and asserts it lands in the
        hardcoded ``teams`` / ``events`` / ``agent_states`` collections.
        """
        for env in (
            "MONGO_TEAMS_COLLECTION",
            "MONGO_EVENTS_COLLECTION",
            "MONGO_AGENT_STATES_COLLECTION",
        ):
            monkeypatch.delenv(env, raising=False)

        store = MongoEventStore(mongo_db)
        team_id = uuid.uuid4()
        store.save_team(make_process(team_id=team_id))
        store.save_event(make_persisted_event(team_id=team_id, sequence=1))
        store.save_agent_state(make_agent_state_snapshot(team_id=team_id, agent_id="a1"))

        assert mongo_db["teams"].count_documents({}) == 1
        assert mongo_db["events"].count_documents({}) == 1
        assert mongo_db["agent_states"].count_documents({}) == 1

    def test_collection_names_overridden_by_env(
        self, mongo_db: Any
    ) -> None:
        """The three env vars rename the collections; defaults stay empty.

        Constructs a store with custom collection names set via environment
        variables, persists one document of each kind, and asserts they land
        in the custom collections and NOT in the default ones.
        """
        overrides = {
            "MONGO_TEAMS_COLLECTION": "t_custom",
            "MONGO_EVENTS_COLLECTION": "e_custom",
            "MONGO_AGENT_STATES_COLLECTION": "as_custom",
        }
        with patch.dict(os.environ, overrides):
            store = MongoEventStore(mongo_db)

        team_id = uuid.uuid4()
        store.save_team(make_process(team_id=team_id))
        store.save_event(make_persisted_event(team_id=team_id, sequence=1))
        store.save_agent_state(make_agent_state_snapshot(team_id=team_id, agent_id="a1"))

        # Documents land in the custom collections...
        assert mongo_db["t_custom"].count_documents({}) == 1
        assert mongo_db["e_custom"].count_documents({}) == 1
        assert mongo_db["as_custom"].count_documents({}) == 1
        # ...and not in the defaults.
        assert mongo_db["teams"].count_documents({}) == 0
        assert mongo_db["events"].count_documents({}) == 0
        assert mongo_db["agent_states"].count_documents({}) == 0

    # --- Import guards ------------------------------------------------------

    def test_import_succeeds_when_pymongo_installed(self) -> None:
        """``MongoEventStore`` is importable when ``pymongo`` is available."""
        from akgentic.team.repositories.mongo import MongoEventStore as Imported

        assert Imported is MongoEventStore

    def test_import_guard_when_pymongo_missing(self) -> None:
        """Conditional import fails gracefully when ``pymongo`` is unavailable."""
        import importlib
        import sys

        import akgentic.team.repositories as repos_module

        # Save and remove cached mongo and pymongo modules
        mongo_module_key = "akgentic.team.repositories.mongo"
        saved: dict[str, object] = {}
        for key in list(sys.modules):
            if key.startswith(mongo_module_key):
                saved[key] = sys.modules.pop(key)
        pymongo_saved: dict[str, object] = {}
        for key in list(sys.modules):
            if key.startswith("pymongo"):
                pymongo_saved[key] = sys.modules.pop(key)

        try:
            # Simulate pymongo being unavailable
            with patch.dict(
                sys.modules,
                {"pymongo": None, "pymongo.database": None, "pymongo.collection": None},
            ):
                # Verify repositories __init__ removes MongoEventStore from __all__
                importlib.reload(repos_module)
                assert "MongoEventStore" not in repos_module.__all__

                # Verify direct import of mongo module raises helpful ImportError
                try:
                    importlib.import_module(mongo_module_key)
                    msg = "Expected ImportError was not raised"
                    raise AssertionError(msg)
                except ImportError as exc:
                    assert "pymongo is required" in str(exc)
                    assert "akgentic-team[mongo]" in str(exc)
        finally:
            # Restore all saved modules
            sys.modules.update(saved)  # type: ignore[arg-type]
            sys.modules.update(pymongo_saved)  # type: ignore[arg-type]
            importlib.reload(repos_module)


class TestMongoEventStoreShardKeySafeUpsert:
    """Shard-key-safe upsert shape for ``save_team`` / ``save_agent_state``.

    These tests assert that the writers use ``update_one`` with
    ``$set``/``$setOnInsert`` (never ``replace_one``), keep the shard key out of
    the mutating ``$set`` body, and behave identically to the old replace-style
    upsert on round-trip and update paths.

    LIMITATION: the backing double is ``mongomock``, which models neither Cosmos
    sharding nor partition-key extraction. A green run here proves the new shape
    is *behaviourally equivalent* to the old one — it does **not** and **cannot**
    validate that the new shape resolves the Cosmos substatus ``1001``
    (``wrong-pk-value``) failure. That is an operational gate (the Validation
    Plan in the tech-spec), run against a real throwaway sharded Cosmos
    collection, not part of this CI suite.
    """

    # --- save_team upsert shape ---------------------------------------------

    def test_save_team_uses_setoninsert_not_replace(
        self, mongo_store: MongoEventStore
    ) -> None:
        """``save_team`` calls ``update_one`` with the shard key only in $setOnInsert.

        ``team_id`` must be absent from ``$set`` and present only under
        ``$setOnInsert``; ``replace_one`` must never be called (AC1, AC5).
        """
        process = make_process()
        team_id_str = str(process.team_id)

        with (
            patch.object(
                mongo_store._teams, "update_one", wraps=mongo_store._teams.update_one
            ) as spy_update,
            patch.object(mongo_store._teams, "replace_one") as spy_replace,
        ):
            mongo_store.save_team(process)

        spy_replace.assert_not_called()
        spy_update.assert_called_once()
        assert spy_update.call_args.kwargs["upsert"] is True
        filter_arg, update_arg = spy_update.call_args.args[0], spy_update.call_args.args[1]
        assert filter_arg == {"team_id": team_id_str}
        assert "team_id" not in update_arg["$set"]
        assert update_arg["$setOnInsert"] == {"team_id": team_id_str}
        # $set must carry the full payload minus the shard key — a regression
        # that emitted an empty or garbage $set would otherwise slip through.
        expected_set = process.model_dump()
        expected_set.pop("_id", None)
        expected_set.pop("team_id", None)
        assert update_arg["$set"] == expected_set
        assert "status" in update_arg["$set"]

    def test_save_team_update_path_preserves_team_id(
        self, mongo_store: MongoEventStore, mongo_db: Any
    ) -> None:
        """Re-saving the same ``team_id`` with a changed status updates in place (AC4).

        The raw ``teams`` document keeps its original ``team_id`` string and
        reflects the new status; the typed load returns the updated status.
        """
        process = make_process(status=TeamStatus.RUNNING)
        mongo_store.save_team(process)

        updated = make_process(team_id=process.team_id, status=TeamStatus.STOPPED)
        mongo_store.save_team(updated)

        raw = mongo_db["teams"].find_one({"team_id": str(process.team_id)})
        assert raw is not None
        assert raw["team_id"] == str(process.team_id)

        loaded = mongo_store.load_team(process.team_id)
        assert loaded is not None
        assert loaded.status == TeamStatus.STOPPED

    def test_save_team_insert_then_update_single_document(
        self, mongo_store: MongoEventStore, mongo_db: Any
    ) -> None:
        """An insert + update cycle leaves exactly one ``teams`` document (AC4)."""
        process = make_process(status=TeamStatus.RUNNING)
        mongo_store.save_team(process)
        mongo_store.save_team(
            make_process(team_id=process.team_id, status=TeamStatus.STOPPED)
        )

        assert mongo_db["teams"].count_documents({"team_id": str(process.team_id)}) == 1

    def test_save_team_set_merges_does_not_remove_absent_fields(
        self, mongo_store: MongoEventStore, mongo_db: Any
    ) -> None:
        """``$set`` merges rather than replaces (the one documented divergence).

        A top-level field present in the stored document but absent from the new
        write survives the re-save — ``replace_one`` would have dropped it. This
        pins the behavioural difference the tech-spec flags ("Known semantic
        difference"). Low-risk for the additive ``Process`` schema because
        ``model_dump`` always emits every current field; a stale field can only
        survive if a field is removed from the model while old docs exist.
        """
        process = make_process(status=TeamStatus.RUNNING)
        mongo_store.save_team(process)

        # Inject a stale top-level field directly, as if an older schema wrote it.
        mongo_db["teams"].update_one(
            {"team_id": str(process.team_id)},
            {"$set": {"legacy_field": "stale"}},
        )

        # Re-save via the writer; replace_one would have dropped legacy_field.
        mongo_store.save_team(
            make_process(team_id=process.team_id, status=TeamStatus.STOPPED)
        )

        raw = mongo_db["teams"].find_one({"team_id": str(process.team_id)})
        assert raw is not None
        assert raw["legacy_field"] == "stale"  # merged, not replaced
        # ...and the in-schema payload still updated.
        loaded = mongo_store.load_team(process.team_id)
        assert loaded is not None
        assert loaded.status == TeamStatus.STOPPED

    # --- save_agent_state upsert shape --------------------------------------

    def test_save_agent_state_uses_setoninsert_not_replace(
        self, mongo_store: MongoEventStore
    ) -> None:
        """``save_agent_state`` keeps both identity keys out of $set (AC2, AC5).

        ``team_id`` and ``agent_id`` must be absent from ``$set`` and present
        only under ``$setOnInsert``; ``replace_one`` must never be called.
        """
        snapshot = make_agent_state_snapshot(agent_id="a1")
        team_id_str = str(snapshot.team_id)

        with (
            patch.object(
                mongo_store._agent_states,
                "update_one",
                wraps=mongo_store._agent_states.update_one,
            ) as spy_update,
            patch.object(mongo_store._agent_states, "replace_one") as spy_replace,
        ):
            mongo_store.save_agent_state(snapshot)

        spy_replace.assert_not_called()
        spy_update.assert_called_once()
        assert spy_update.call_args.kwargs["upsert"] is True
        filter_arg, update_arg = spy_update.call_args.args[0], spy_update.call_args.args[1]
        assert filter_arg == {"team_id": team_id_str, "agent_id": "a1"}
        assert "team_id" not in update_arg["$set"]
        assert "agent_id" not in update_arg["$set"]
        assert update_arg["$setOnInsert"] == {"team_id": team_id_str, "agent_id": "a1"}
        # $set must carry the full payload minus both identity keys.
        expected_set = snapshot.model_dump()
        expected_set.pop("_id", None)
        expected_set.pop("team_id", None)
        expected_set.pop("agent_id", None)
        assert update_arg["$set"] == expected_set
        assert "state" in update_arg["$set"]

    def test_save_agent_state_update_path_preserves_keys(
        self, mongo_store: MongoEventStore, mongo_db: Any
    ) -> None:
        """Re-saving the same ``(team_id, agent_id)`` updates the body in place (AC6).

        Exactly one ``agent_states`` document survives, both identity key strings
        are unchanged, and the mutated body (``state.task_count``) is reflected.
        """
        team_id = uuid.uuid4()
        snapshot = make_agent_state_snapshot(
            team_id=team_id, agent_id="a1", state=SampleAgentState(task_count=5)
        )
        mongo_store.save_agent_state(snapshot)

        updated = make_agent_state_snapshot(
            team_id=team_id, agent_id="a1", state=SampleAgentState(task_count=9)
        )
        mongo_store.save_agent_state(updated)

        assert (
            mongo_db["agent_states"].count_documents(
                {"team_id": str(team_id), "agent_id": "a1"}
            )
            == 1
        )
        raw = mongo_db["agent_states"].find_one(
            {"team_id": str(team_id), "agent_id": "a1"}
        )
        assert raw is not None
        assert raw["team_id"] == str(team_id)
        assert raw["agent_id"] == "a1"
        assert raw["state"]["task_count"] == 9
