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
import uuid
from typing import TYPE_CHECKING, Any
from unittest.mock import patch

import pytest
from pymongo.errors import OperationFailure

from akgentic.team.repositories.mongo import MongoEventStore

if TYPE_CHECKING:
    from akgentic.team.ports import EventStore

from tests.models.conftest import (
    make_agent_state_snapshot,
    make_persisted_event,
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
