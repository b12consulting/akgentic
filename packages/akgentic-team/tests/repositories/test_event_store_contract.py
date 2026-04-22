"""Shared parametrized contract suite for ``EventStore`` implementations.

Every behavioural test in this module runs once per backend (yaml,
mongo, postgres) via the ``event_store`` fixture defined in
``conftest.py``. The intent (ADR-15 §9) is that a Protocol drift cannot
land in one backend without breaking the others — this module is the
"non-negotiable first gate" for ``EventStore`` conformance.

Backend-specific assertions (collection names, index specs, exception
TYPES on duplicate keys, on-disk file layout, schema-drift /
payload-authority invariants) stay in the per-backend modules under
``tests/repositories/{yaml,mongo,postgres}/``.
"""

from __future__ import annotations

import uuid

import pytest
from akgentic.core.messages.message import UserMessage

from akgentic.team.models import TeamStatus
from akgentic.team.ports import EventStore
from tests.models.conftest import (
    SampleAgentState,
    make_agent_state_snapshot,
    make_persisted_event,
    make_process,
)


class TestEventStoreContract:
    """Behavioural contract every ``EventStore`` backend must satisfy.

    Each test takes the parametrized ``event_store`` fixture and runs
    once per backend. Test IDs end in ``[yaml]`` / ``[mongo]`` /
    ``[postgres]`` so a per-backend regression is immediately obvious in
    pytest output.
    """

    # --- save_team / load_team round-trip ---------------------------------

    def test_save_and_load_team_round_trip(self, event_store: EventStore) -> None:
        """``save_team(p)`` then ``load_team(p.team_id)`` returns deep-equal Process."""
        process = make_process()
        event_store.save_team(process)
        loaded = event_store.load_team(process.team_id)

        assert loaded is not None
        assert loaded.team_id == process.team_id
        assert loaded.status == process.status
        assert loaded.team_card.name == process.team_card.name
        assert loaded.created_at == process.created_at

    def test_load_team_returns_none_when_missing(self, event_store: EventStore) -> None:
        """Protocol contract: missing team yields ``None``."""
        assert event_store.load_team(uuid.uuid4()) is None

    def test_save_team_is_upsert(self, event_store: EventStore) -> None:
        """``save_team`` is upsert-by-team_id; second insert replaces payload."""
        process = make_process(status=TeamStatus.RUNNING)
        event_store.save_team(process)

        updated = make_process(team_id=process.team_id, status=TeamStatus.STOPPED)
        event_store.save_team(updated)

        loaded = event_store.load_team(process.team_id)
        assert loaded is not None
        assert loaded.status == TeamStatus.STOPPED

    # --- list_teams -------------------------------------------------------

    def test_list_teams_returns_all(self, event_store: EventStore) -> None:
        """``list_teams`` returns every persisted process (order-independent)."""
        p1 = make_process()
        p2 = make_process()
        p3 = make_process()
        event_store.save_team(p1)
        event_store.save_team(p2)
        event_store.save_team(p3)

        result = event_store.list_teams()
        assert len(result) == 3
        assert {p.team_id for p in result} == {p1.team_id, p2.team_id, p3.team_id}

    # --- save_event / load_events ordering --------------------------------

    def test_save_and_load_events_in_sequence_order(self, event_store: EventStore) -> None:
        """Events inserted out of order are returned ascending by sequence."""
        team_id = uuid.uuid4()
        for seq in (3, 1, 2, 5, 4):
            event_store.save_event(make_persisted_event(team_id=team_id, sequence=seq))

        loaded = event_store.load_events(team_id)
        assert [e.sequence for e in loaded] == [1, 2, 3, 4, 5]

    def test_load_events_returns_empty_list_when_missing(self, event_store: EventStore) -> None:
        """Protocol contract: no events for a team yields ``[]``."""
        assert event_store.load_events(uuid.uuid4()) == []

    # --- get_max_sequence -------------------------------------------------

    def test_get_max_sequence_returns_zero_when_empty(self, event_store: EventStore) -> None:
        """Protocol contract: no events yields max sequence ``0``."""
        assert event_store.get_max_sequence(uuid.uuid4()) == 0

    def test_get_max_sequence_returns_largest_after_inserts(self, event_store: EventStore) -> None:
        """``get_max_sequence`` returns the largest sequence ever written."""
        team_id = uuid.uuid4()
        for seq in (1, 2, 7, 3):
            event_store.save_event(make_persisted_event(team_id=team_id, sequence=seq))
        assert event_store.get_max_sequence(team_id) == 7

    # --- save_agent_state / load_agent_states -----------------------------

    def test_save_and_load_agent_state_round_trip(self, event_store: EventStore) -> None:
        """``save_agent_state(s)`` puts ``s`` into ``load_agent_states(team_id)``."""
        snap = make_agent_state_snapshot(agent_id="round-trip-agent")
        event_store.save_agent_state(snap)

        loaded = event_store.load_agent_states(snap.team_id)
        assert len(loaded) == 1
        assert loaded[0].agent_id == "round-trip-agent"
        assert isinstance(loaded[0].state, SampleAgentState)

    def test_save_agent_state_is_upsert(self, event_store: EventStore) -> None:
        """``save_agent_state`` is upsert on ``(team_id, agent_id)``."""
        team_id = uuid.uuid4()
        first = make_agent_state_snapshot(
            team_id=team_id,
            agent_id="agent-a",
            state=SampleAgentState(task_count=1),
        )
        event_store.save_agent_state(first)

        second = make_agent_state_snapshot(
            team_id=team_id,
            agent_id="agent-a",
            state=SampleAgentState(task_count=99),
        )
        event_store.save_agent_state(second)

        loaded = event_store.load_agent_states(team_id)
        assert len(loaded) == 1
        assert isinstance(loaded[0].state, SampleAgentState)
        assert loaded[0].state.task_count == 99

    def test_load_agent_states_returns_empty_list_when_missing(
        self, event_store: EventStore
    ) -> None:
        """Protocol contract: no agent states for a team yields ``[]``."""
        assert event_store.load_agent_states(uuid.uuid4()) == []

    # --- delete_team ------------------------------------------------------

    def test_delete_team_cascades_across_three_kinds(self, event_store: EventStore) -> None:
        """``delete_team`` removes the team's process, events, and agent states."""
        process = make_process()
        for seq in range(1, 6):
            event_store.save_event(make_persisted_event(team_id=process.team_id, sequence=seq))
        for agent_id in ("a1", "a2", "a3"):
            event_store.save_agent_state(
                make_agent_state_snapshot(team_id=process.team_id, agent_id=agent_id)
            )
        event_store.save_team(process)

        event_store.delete_team(process.team_id)

        assert event_store.load_team(process.team_id) is None
        assert event_store.load_events(process.team_id) == []
        assert event_store.load_agent_states(process.team_id) == []

    def test_delete_team_isolates_other_teams(self, event_store: EventStore) -> None:
        """``delete_team`` only purges the requested team_id; others survive."""
        team_a = make_process()
        team_b = make_process()
        event_store.save_team(team_a)
        event_store.save_team(team_b)

        for seq in range(1, 4):
            event_store.save_event(make_persisted_event(team_id=team_a.team_id, sequence=seq))
            event_store.save_event(make_persisted_event(team_id=team_b.team_id, sequence=seq))
        event_store.save_agent_state(
            make_agent_state_snapshot(team_id=team_a.team_id, agent_id="a1")
        )
        event_store.save_agent_state(
            make_agent_state_snapshot(team_id=team_b.team_id, agent_id="b1")
        )

        event_store.delete_team(team_a.team_id)

        assert event_store.load_team(team_b.team_id) is not None
        assert len(event_store.load_events(team_b.team_id)) == 3
        assert len(event_store.load_agent_states(team_b.team_id)) == 1

    def test_delete_team_is_idempotent(self, event_store: EventStore) -> None:
        """Deleting a non-existent team is a no-op (no exception)."""
        ghost_id = uuid.uuid4()
        event_store.delete_team(ghost_id)
        event_store.delete_team(ghost_id)  # second call also a no-op

    # --- Polymorphic round-trip (Message / BaseState) ---------------------

    def test_polymorphic_message_round_trip_through_event(self, event_store: EventStore) -> None:
        """A polymorphic ``Message`` subtype survives the persist/hydrate cycle."""
        team_id = uuid.uuid4()
        msg = UserMessage(content="hello from polymorphic test")
        event_store.save_event(make_persisted_event(team_id=team_id, sequence=1, event=msg))

        loaded = event_store.load_events(team_id)
        assert len(loaded) == 1
        assert isinstance(loaded[0].event, UserMessage)
        assert loaded[0].event.content == "hello from polymorphic test"

    def test_polymorphic_basestate_round_trip_through_agent_state(
        self, event_store: EventStore
    ) -> None:
        """A polymorphic ``BaseState`` subtype survives the persist/hydrate cycle."""
        team_id = uuid.uuid4()
        state = SampleAgentState(task_count=42)
        event_store.save_agent_state(
            make_agent_state_snapshot(team_id=team_id, agent_id="poly", state=state)
        )

        loaded = event_store.load_agent_states(team_id)
        assert len(loaded) == 1
        assert isinstance(loaded[0].state, SampleAgentState)
        assert loaded[0].state.task_count == 42

    # --- Validation failure on corrupted payload --------------------------

    def test_validation_failure_propagates_pydantic_error(
        self,
        event_store: EventStore,
        request: pytest.FixtureRequest,
    ) -> None:
        """A corrupted stored payload triggers Pydantic validation handling.

        Implementation note (per AC #5, last bullet): all three current
        backends DELIBERATELY swallow ``pydantic.ValidationError`` on
        corrupted payloads (logging + ``None`` / ``[]`` semantics) — see
        the per-backend ``test_load_*_corrupted_*`` tests for the
        bespoke coverage. None expose a clean seam to assert raw
        propagation without leaking internals into the contract suite.

        Per the AC's escape hatch ("If a backend cannot expose a seam
        without leaking implementation, **skip on that backend** with a
        clear message — do NOT loosen the assertion"), this test skips
        on every backend until a future EventStore implementation
        propagates ``ValidationError`` natively. The skip preserves the
        contract slot so the moment such a backend lands, the test slot
        is already there to be flipped on.
        """
        backend = request.node.callspec.params["event_store"]
        pytest.skip(
            f"{backend!r} swallows ValidationError by design (resilient "
            "load semantics); per-backend corrupted-payload coverage "
            "lives in tests/repositories/{yaml,mongo,postgres}/."
        )
