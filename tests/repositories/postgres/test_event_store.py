"""Postgres-specific tests for ``NagraEventStore``.

Behavioural Protocol coverage (round-trip, upsert, list, sequencing,
max sequence, cascading delete, polymorphic round-trips) lives in the
shared ``tests/repositories/test_event_store_contract.py`` and runs
once per backend. This module retains only Postgres-specific
invariants:

* ``test_satisfies_event_store_protocol`` — structural typing check.
* ``test_payload_is_authoritative_over_promoted_columns`` — schema-drift
  / payload-authority invariant from Story 17.2.
* ``test_duplicate_sequence_raises_unique_violation`` — the §8 native-
  exception propagation contract for the composite primary key.

Constructor / source-purity / import-gate tests live in adjacent files
(``test_ci_env_wiring.py``, ``test_init_db.py``, ``test_import_gate.py``).
"""

from __future__ import annotations

import json
import uuid
from typing import TYPE_CHECKING

import psycopg
import pytest
from nagra import Transaction  # type: ignore[import-untyped]

from akgentic.team.repositories.postgres import NagraEventStore

if TYPE_CHECKING:
    from akgentic.team.ports import EventStore

from tests.models.conftest import make_persisted_event


class TestNagraEventStorePostgresSpecific:
    """Postgres-only invariants — see contract suite for behavioural coverage."""

    # --- Protocol compliance -------------------------------------------------

    def test_satisfies_event_store_protocol(self, postgres_clean_tables: str) -> None:
        """``NagraEventStore`` satisfies ``EventStore`` Protocol structurally."""
        store: EventStore = NagraEventStore(postgres_clean_tables)
        assert store is not None

    # --- schema-drift / payload-authority invariant -------------------------

    def test_payload_is_authoritative_over_promoted_columns(
        self, postgres_clean_tables: str
    ) -> None:
        """Hydrated model fields come from JSONB ``data`` only.

        Plants a row whose promoted ``team_id`` / ``sequence`` columns
        DISAGREE with the embedded payload, then asserts the hydrated
        ``PersistedEvent`` carries the payload values — proving the
        promoted columns are query keys only, never read back into the
        model. Bypasses ``save_event`` on purpose to plant the drift.
        """
        store = NagraEventStore(postgres_clean_tables)
        routing_team_id = uuid.uuid4()
        payload_team_id = uuid.uuid4()

        canonical = make_persisted_event(team_id=payload_team_id, sequence=42)
        payload_dict = canonical.model_dump()
        payload_dict["team_id"] = str(payload_team_id)
        payload_dict["sequence"] = 42

        with Transaction(postgres_clean_tables) as trn:
            trn.execute(
                "INSERT INTO event_entries (team_id, sequence, data) VALUES (%s, %s, %s)",
                (str(routing_team_id), 1, json.dumps(payload_dict)),
            )

        loaded = store.load_events(routing_team_id)
        assert len(loaded) == 1
        assert loaded[0].team_id == payload_team_id
        assert loaded[0].sequence == 42

    # --- duplicate (team_id, sequence) propagation contract -----------------

    def test_duplicate_sequence_raises_unique_violation(self, postgres_clean_tables: str) -> None:
        """Composite-PK violation propagates as ``psycopg.errors.UniqueViolation``.

        Pins the exception TYPE — not a substring of the message — so a
        regression that swallows or rewraps the native exception is
        caught immediately. Mirrors the §8 contract: backends must
        propagate the underlying driver's native uniqueness error so
        callers can rely on the type for compensation logic.
        """
        store = NagraEventStore(postgres_clean_tables)
        team_id = uuid.uuid4()
        store.save_event(make_persisted_event(team_id=team_id, sequence=1))
        with pytest.raises(psycopg.errors.UniqueViolation):
            store.save_event(make_persisted_event(team_id=team_id, sequence=1))
