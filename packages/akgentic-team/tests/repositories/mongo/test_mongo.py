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

import logging
import os
import uuid
from typing import TYPE_CHECKING, Any
from unittest.mock import patch

import pytest
from pymongo.errors import OperationFailure

from akgentic.team.repositories.mongo import MongoEventStore, ensure_indexes

if TYPE_CHECKING:
    from akgentic.team.ports import EventStore

from akgentic.team.models import TeamStatus
from tests.models.conftest import (
    SampleAgentState,
    make_agent_state_snapshot,
    make_persisted_event,
    make_process,
)


def _assert_ungated_indexes_present(db: Any) -> None:
    """Assert the three non-teams indexes survive a teams-collection opt-out.

    ``auto_create_indexes`` / ``MONGO_TEAM_AUTO_INDEX`` gate the teams
    collection only. The ``events`` and ``agent_states`` indexes have been
    built on every boot since those backends shipped, so they already exist in
    production and the call there is a cheap no-op; gating them would be an
    unrequested behaviour change. Called from every opt-out test so a future
    refactor cannot widen the gate unnoticed.
    """
    events_info = db["events"].index_information()
    assert [("team_id", 1), ("sequence", 1)] in [e["key"] for e in events_info.values()]
    assert "events_event_id_idx" in events_info

    agent_info = db["agent_states"].index_information()
    compound = [
        e for e in agent_info.values() if e["key"] == [("team_id", 1), ("agent_id", 1)]
    ]
    assert compound, f"agent_states (team_id, agent_id) index missing: {agent_info}"
    assert compound[0].get("unique") is True


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

    # --- list_teams push-down -----------------------------------------------

    def test_list_teams_pushes_status_into_the_find_filter(
        self, mongo_store: MongoEventStore
    ) -> None:
        """``status`` reaches MongoDB inside the ``find`` filter, not Python.

        An in-memory filter returns the same list, so the assertion has to
        bite on the shape of the call: exactly one ``find``, carrying
        ``{"status": "running"}``. ``wraps=`` keeps the real query running
        so the result is pinned by the same test.
        """
        mongo_store.save_team(make_process(status=TeamStatus.RUNNING))
        mongo_store.save_team(make_process(status=TeamStatus.STOPPED))

        with patch.object(
            mongo_store._teams, "find", wraps=mongo_store._teams.find
        ) as spy_find:
            result = mongo_store.list_teams(status=TeamStatus.RUNNING)

        spy_find.assert_called_once()
        assert spy_find.call_args.args[0] == {"status": "running"}
        assert len(result) == 1
        assert result[0].status == TeamStatus.RUNNING

    def test_list_teams_pushes_user_id_and_status_into_one_find_filter(
        self, mongo_store: MongoEventStore
    ) -> None:
        """Both filters travel down in a single ``find`` call, ANDed.

        Never two ``find`` calls intersected afterwards, and never a
        ``find`` plus a second pass in Python.
        """
        mine_running = make_process(user_id="u1", status=TeamStatus.RUNNING)
        mongo_store.save_team(mine_running)
        mongo_store.save_team(make_process(user_id="u1", status=TeamStatus.STOPPED))
        mongo_store.save_team(make_process(user_id="u2", status=TeamStatus.RUNNING))

        with patch.object(
            mongo_store._teams, "find", wraps=mongo_store._teams.find
        ) as spy_find:
            result = mongo_store.list_teams(user_id="u1", status=TeamStatus.RUNNING)

        spy_find.assert_called_once()
        assert spy_find.call_args.args[0] == {"user_id": "u1", "status": "running"}
        assert [p.team_id for p in result] == [mine_running.team_id]

    def test_list_teams_without_filters_issues_an_empty_find_filter(
        self, mongo_store: MongoEventStore
    ) -> None:
        """A ``None`` parameter contributes no key — the filter stays ``{}``.

        Both the no-argument call and the explicit ``user_id=None,
        status=None`` call must reach MongoDB unfiltered and return every
        lifecycle state, ``DELETED`` included.
        """
        mongo_store.save_team(make_process(status=TeamStatus.RUNNING))
        mongo_store.save_team(make_process(status=TeamStatus.DELETED))

        with patch.object(
            mongo_store._teams, "find", wraps=mongo_store._teams.find
        ) as spy_find:
            no_argument = mongo_store.list_teams()

        spy_find.assert_called_once()
        assert spy_find.call_args.args[0] == {}

        with patch.object(
            mongo_store._teams, "find", wraps=mongo_store._teams.find
        ) as spy_find:
            explicit_none = mongo_store.list_teams(user_id=None, status=None)

        spy_find.assert_called_once()
        assert spy_find.call_args.args[0] == {}
        assert len(no_argument) == 2
        assert {p.team_id for p in explicit_none} == {p.team_id for p in no_argument}

    def test_list_teams_positional_user_id_still_reaches_the_find_filter(
        self, mongo_store: MongoEventStore
    ) -> None:
        """``list_teams("u1")`` keeps working and still pushes ``user_id`` down.

        Guards the parameter order against the rewrite: a keyword-only
        regression would be caught at the Mongo layer too, not only in the
        shared contract suite.
        """
        mine = make_process(user_id="u1", status=TeamStatus.RUNNING)
        mongo_store.save_team(mine)
        mongo_store.save_team(make_process(user_id="u2", status=TeamStatus.RUNNING))

        with patch.object(
            mongo_store._teams, "find", wraps=mongo_store._teams.find
        ) as spy_find:
            result = mongo_store.list_teams("u1")

        spy_find.assert_called_once()
        assert spy_find.call_args.args[0] == {"user_id": "u1"}
        assert [p.team_id for p in result] == [mine.team_id]

    def test_list_teams_does_not_filter_status_in_python(
        self, mongo_store: MongoEventStore
    ) -> None:
        """No post-hydration ``status`` comparison survives in the method body.

        ``find`` is stubbed to hand back BOTH documents whatever filter the
        store asks for. Any surviving Python-side comparison would drop the
        stopped team while leaving every result-level assertion elsewhere
        green — which is exactly what this test exists to catch.
        """
        running = make_process(status=TeamStatus.RUNNING)
        stopped = make_process(status=TeamStatus.STOPPED)
        mongo_store.save_team(running)
        mongo_store.save_team(stopped)

        all_docs = list(mongo_store._teams.find({}))
        with patch.object(mongo_store._teams, "find", return_value=iter(all_docs)):
            result = mongo_store.list_teams(status=TeamStatus.RUNNING)

        assert {p.team_id for p in result} == {running.team_id, stopped.team_id}

    def test_list_teams_skips_corrupted_documents(
        self, mongo_store: MongoEventStore, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A corrupted team document is skipped, logged, and does not raise.

        Seeded beside a healthy team so a ``list_teams`` broken to return
        nothing cannot pass: the healthy team must still come back.
        """
        healthy = make_process(status=TeamStatus.RUNNING)
        mongo_store.save_team(healthy)
        mongo_store._teams.insert_one(
            {"team_id": str(uuid.uuid4()), "not_a_valid_field": 123}
        )

        with caplog.at_level(logging.WARNING):
            result = mongo_store.list_teams()

        assert {p.team_id for p in result} == {healthy.team_id}
        assert "Skipping corrupted team document" in caplog.text

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

    def test_events_event_id_index_created_on_init(
        self, mongo_store: MongoEventStore, mongo_db: Any
    ) -> None:
        """``__init__`` creates ``events_event_id_idx`` on ``events``.

        Single-field ascending key on the nested ``event.id``, backing the
        anchor lookup in ``load_events(after_event_id=...)``. Deliberately
        NOT compound with ``team_id``: Cosmos for MongoDB rejects compound
        indexes on nested paths unless the account enables
        ``EnableUniqueCompoundNestedDocs``, and ``event.id`` is a uuid4 —
        already maximally selective for this equality lookup. Deliberately
        not unique: a unique index would turn a read-path ambiguity into a
        write-path ``DuplicateKeyError`` on ``save_event``.
        """
        info = mongo_db["events"].index_information()
        assert "events_event_id_idx" in info
        assert info["events_event_id_idx"]["key"] == [("event.id", 1)]
        assert not info["events_event_id_idx"].get("unique")

    def test_events_event_id_index_creation_is_idempotent(
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
        assert info["events_event_id_idx"]["key"] == [("event.id", 1)]
        assert list(info.keys()).count("events_event_id_idx") == 1

    def test_index_rejection_is_logged_and_does_not_block_construction(
        self, mongo_db: Any, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A backend that rejects the event.id spec must not stop the process.

        Cosmos for MongoDB rejects compound-on-nested by default and may
        reject other specs depending on account capabilities. The index is a
        performance optimization, not a correctness requirement — so
        construction logs and continues rather than raising, which would
        refuse to start the server on every replica.
        """
        real_create_index = type(mongo_db["events"]).create_index

        def _reject_event_id(self: Any, keys: Any, **kwargs: Any) -> Any:
            if keys == "event.id":
                raise OperationFailure("index not supported on this account")
            return real_create_index(self, keys, **kwargs)

        with patch.object(type(mongo_db["events"]), "create_index", _reject_event_id):
            with caplog.at_level(logging.WARNING):
                MongoEventStore(mongo_db)  # must NOT raise

        assert "events_event_id_idx" in caplog.text
        assert "collection scan" in caplog.text

    def test_existing_events_team_sequence_index_still_created(
        self, mongo_store: MongoEventStore, mongo_db: Any
    ) -> None:
        """The pre-existing ``(team_id, sequence)`` index is untouched.

        It already backs the ``{"sequence": {"$gt": n}}`` range filter and
        needs no change for the cursor push-down.
        """
        specs = [entry["key"] for entry in mongo_db["events"].index_information().values()]
        assert [("team_id", 1), ("sequence", 1)] in specs

    # --- teams index provisioning -------------------------------------------

    def test_ensure_indexes_creates_both_teams_indexes(self, mongo_db: Any) -> None:
        """``ensure_indexes`` provisions ``teams_user_id_idx`` and ``teams_status_idx``.

        Both are single-field ascending — the specs backing ``list_teams``'
        ``user_id`` and ``status`` push-downs, and the exact name and spec the
        enterprise orphan reconciler probes for.
        """
        ensure_indexes(mongo_db)

        info = mongo_db["teams"].index_information()
        assert info["teams_user_id_idx"]["key"] == [("user_id", 1)]
        assert info["teams_status_idx"]["key"] == [("status", 1)]

    def test_ensure_indexes_targets_the_env_overridden_teams_collection(
        self, mongo_db: Any
    ) -> None:
        """``MONGO_TEAMS_COLLECTION`` moves both indexes to the renamed collection.

        The same resolution the constructor uses, so a deployment sharing one
        database under a custom collection name is indexed identically.
        """
        with patch.dict(os.environ, {"MONGO_TEAMS_COLLECTION": "t_custom"}):
            ensure_indexes(mongo_db)

        info = mongo_db["t_custom"].index_information()
        assert info["teams_user_id_idx"]["key"] == [("user_id", 1)]
        assert info["teams_status_idx"]["key"] == [("status", 1)]
        assert "teams_status_idx" not in mongo_db["teams"].index_information()

    def test_ensure_indexes_is_idempotent(self, mongo_db: Any) -> None:
        """A second call does not raise; each index stays present exactly once.

        ``create_index`` returns silently for an identical name and key spec,
        which is what makes the routine safe from a constructor, an init
        container and a migration job alike.
        """
        ensure_indexes(mongo_db)
        ensure_indexes(mongo_db)  # second call — must NOT raise

        info = mongo_db["teams"].index_information()
        assert info["teams_user_id_idx"]["key"] == [("user_id", 1)]
        assert info["teams_status_idx"]["key"] == [("status", 1)]
        assert list(info.keys()).count("teams_user_id_idx") == 1
        assert list(info.keys()).count("teams_status_idx") == 1

    def test_ensure_indexes_guards_each_index_independently(
        self, mongo_db: Any, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A rejected spec is logged at WARNING and skipped, never raised.

        Both names must appear: one ``try`` wrapping the whole loop would
        abandon ``teams_status_idx`` — the index the reconciler waits on — the
        moment ``teams_user_id_idx`` was refused.
        """
        real_create_index = type(mongo_db["teams"]).create_index

        def _reject_teams(self: Any, keys: Any, **kwargs: Any) -> Any:
            if kwargs.get("name") in {"teams_user_id_idx", "teams_status_idx"}:
                raise OperationFailure("index not supported on this account")
            return real_create_index(self, keys, **kwargs)

        with patch.object(type(mongo_db["teams"]), "create_index", _reject_teams):
            with caplog.at_level(logging.WARNING):
                assert ensure_indexes(mongo_db) is None

        assert "teams_user_id_idx" in caplog.text
        assert "teams_status_idx" in caplog.text
        assert "teams_status_idx" not in mongo_db["teams"].index_information()

    def test_teams_index_rejection_does_not_block_construction(
        self, mongo_db: Any, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A backend that rejects the teams specs must not stop the process.

        The wrapper refuses only the two teams indexes and delegates every
        other key: a patch that rejected everything would take construction
        down on the ungated ``(team_id, sequence)`` index and prove nothing
        about the teams guard. ``mongomock`` shares one ``Collection`` class
        across collections, so the patch is seen by all of them.
        """
        real_create_index = type(mongo_db["teams"]).create_index

        def _reject_teams(self: Any, keys: Any, **kwargs: Any) -> Any:
            if kwargs.get("name") in {"teams_user_id_idx", "teams_status_idx"}:
                raise OperationFailure("index not supported on this account")
            return real_create_index(self, keys, **kwargs)

        with patch.object(type(mongo_db["teams"]), "create_index", _reject_teams):
            with caplog.at_level(logging.WARNING):
                MongoEventStore(mongo_db)  # must NOT raise

        assert "teams_user_id_idx" in caplog.text
        assert "teams_status_idx" in caplog.text
        assert "collection scan" in caplog.text

    # --- teams index opt-out -------------------------------------------------

    def test_teams_indexes_created_by_default(
        self, mongo_db: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """No argument and no env var: construction creates both teams indexes.

        Parity with how ``teams_user_id_idx`` behaved before the opt-out
        existed — small deployments get the indexes for free.
        """
        monkeypatch.delenv("MONGO_TEAM_AUTO_INDEX", raising=False)

        MongoEventStore(mongo_db)

        info = mongo_db["teams"].index_information()
        assert info["teams_user_id_idx"]["key"] == [("user_id", 1)]
        assert info["teams_status_idx"]["key"] == [("status", 1)]

    def test_auto_create_indexes_false_creates_neither_teams_index(
        self, mongo_db: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """``auto_create_indexes=False`` suppresses both teams indexes.

        Including ``teams_user_id_idx``, which was unconditional before this
        story: one provisioning path, two indexes — which is why the opt-out is
        paired with ``python -m akgentic.team.scripts.init_mongo``.
        """
        monkeypatch.delenv("MONGO_TEAM_AUTO_INDEX", raising=False)

        MongoEventStore(mongo_db, auto_create_indexes=False)

        info = mongo_db["teams"].index_information()
        assert "teams_user_id_idx" not in info
        assert "teams_status_idx" not in info
        _assert_ungated_indexes_present(mongo_db)

    @pytest.mark.parametrize("raw", ["0", "false", "no", "FALSE", "No"])
    def test_env_disabling_value_creates_neither_teams_index(
        self, mongo_db: Any, monkeypatch: pytest.MonkeyPatch, raw: str
    ) -> None:
        """``MONGO_TEAM_AUTO_INDEX`` in {0, false, no} disables, case-insensitively.

        This is the production safety valve: an enterprise teams collection of
        ~100 GB cannot absorb a foreground index build at boot.
        """
        monkeypatch.setenv("MONGO_TEAM_AUTO_INDEX", raw)

        MongoEventStore(mongo_db)

        info = mongo_db["teams"].index_information()
        assert "teams_user_id_idx" not in info
        assert "teams_status_idx" not in info
        _assert_ungated_indexes_present(mongo_db)

    @pytest.mark.parametrize("raw", ["", "1", "true", "yes", "maybe"])
    def test_env_non_disabling_value_leaves_the_default_on(
        self, mongo_db: Any, monkeypatch: pytest.MonkeyPatch, raw: str
    ) -> None:
        """Any other value — empty included — leaves the boot-time build on.

        The variable is an explicit opt-out, not a tri-state: a typo must fail
        towards the historical behaviour rather than silently dropping indexes.
        """
        monkeypatch.setenv("MONGO_TEAM_AUTO_INDEX", raw)

        MongoEventStore(mongo_db)

        info = mongo_db["teams"].index_information()
        assert info["teams_user_id_idx"]["key"] == [("user_id", 1)]
        assert info["teams_status_idx"]["key"] == [("status", 1)]

    def test_explicit_argument_beats_the_disabling_environment(
        self, mongo_db: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """``auto_create_indexes=True`` overrides ``MONGO_TEAM_AUTO_INDEX=0``.

        A caller that knows its collection is small stays in control of its own
        store even under a deployment-wide opt-out.
        """
        monkeypatch.setenv("MONGO_TEAM_AUTO_INDEX", "0")

        MongoEventStore(mongo_db, auto_create_indexes=True)

        info = mongo_db["teams"].index_information()
        assert info["teams_user_id_idx"]["key"] == [("user_id", 1)]
        assert info["teams_status_idx"]["key"] == [("status", 1)]

    def test_auto_create_indexes_is_keyword_only(
        self, mongo_db: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The flag cannot be passed positionally.

        Keyword-only so no positional caller can drift into it — a second
        positional argument is a ``TypeError``, not a silent opt-out.
        """
        monkeypatch.delenv("MONGO_TEAM_AUTO_INDEX", raising=False)

        with pytest.raises(TypeError):
            MongoEventStore(mongo_db, False)  # type: ignore[misc]

    def test_list_teams_status_filter_works_without_the_status_index(
        self, mongo_db: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The status filter is correct against a collection with no status index.

        Correctness must never depend on the index being present — a store that
        failed closed while an operator provisioned out of band would turn a
        performance opt-out into an outage.
        """
        monkeypatch.delenv("MONGO_TEAM_AUTO_INDEX", raising=False)
        store = MongoEventStore(mongo_db, auto_create_indexes=False)
        running = make_process(status=TeamStatus.RUNNING)
        store.save_team(running)
        store.save_team(make_process(status=TeamStatus.STOPPED))

        assert "teams_status_idx" not in mongo_db["teams"].index_information()
        assert {p.team_id for p in store.list_teams(status=TeamStatus.RUNNING)} == {
            running.team_id
        }

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
