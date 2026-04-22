"""Tests for ToolStateEvent emission from KnowledgeGraphActor (Story 17.2, ADR-024).

These tests exercise the actor's emission contract — the single-event-per-batch
rule, the monotonic ``seq`` counter, the empty / partial-failure rules, and the
envelope shape. The orchestrator is not required: each test installs a
``MagicMock`` on ``actor.notify_event`` to capture emitted events.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from akgentic.tool.errors import RetriableError
from akgentic.tool.event import ToolStateEvent
from akgentic.tool.knowledge_graph.kg_actor import (
    KG_ACTOR_NAME,
    KnowledgeGraphActor,
)
from akgentic.tool.knowledge_graph.models import (
    EntityCreate,
    EntityUpdate,
    KnowledgeGraphStateEvent,
    ManageGraph,
    RelationCreate,
    RelationDelete,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _actor() -> KnowledgeGraphActor:
    """Create and initialize a KnowledgeGraphActor for testing (same as test_kg_actor)."""
    actor = KnowledgeGraphActor()
    actor.on_start()
    return actor


def _capture(actor: KnowledgeGraphActor) -> list[Any]:
    """Replace ``actor.notify_event`` with a capture-list-backed MagicMock.

    Returns:
        The list that will be populated with each emitted event, in order.
    """
    captured: list[Any] = []
    actor.notify_event = MagicMock(side_effect=lambda evt: captured.append(evt))  # type: ignore[method-assign]
    return captured


class _StateNotifyObserver:
    """Minimal observer that counts ``notify_state_change`` invocations."""

    def __init__(self) -> None:
        self.calls: int = 0

    def notify_state_change(self, state: object) -> None:
        self.calls += 1


# ---------------------------------------------------------------------------
# Scenario (a) — create-entities-only batch
# ---------------------------------------------------------------------------


def test_create_entities_emits_single_event_with_added() -> None:
    actor = _actor()
    captured = _capture(actor)

    actor.update_graph(
        ManageGraph(
            create_entities=[
                EntityCreate(name="Alice", entity_type="Person", description="Engineer")
            ]
        )
    )

    assert len(captured) == 1
    event = captured[0]
    assert isinstance(event, ToolStateEvent)
    assert event.tool_id == KG_ACTOR_NAME
    assert event.seq == 1
    assert isinstance(event.payload, KnowledgeGraphStateEvent)
    assert event.payload.entities_added == actor.get_graph().entities
    assert event.payload.entities_modified == []
    assert event.payload.entities_removed == []
    assert event.payload.relations_added == []
    assert event.payload.relations_modified == []
    assert event.payload.relations_removed == []


# ---------------------------------------------------------------------------
# Scenario (b) — create-relations batch, seq increments
# ---------------------------------------------------------------------------


def test_create_relations_emits_added_and_seq_increments() -> None:
    actor = _actor()
    captured = _capture(actor)

    actor.update_graph(
        ManageGraph(
            create_entities=[
                EntityCreate(name="Alice", entity_type="Person", description="Engineer"),
                EntityCreate(name="Bob", entity_type="Person", description="Designer"),
            ]
        )
    )
    assert len(captured) == 1
    assert captured[0].seq == 1

    actor.update_graph(
        ManageGraph(
            create_relations=[
                RelationCreate(from_entity="Alice", to_entity="Bob", relation_type="knows")
            ]
        )
    )
    assert len(captured) == 2
    second = captured[1]
    assert second.seq == 2
    assert second.payload.entities_added == []
    assert len(second.payload.relations_added) == 1
    assert second.payload.relations_added[0].from_entity == "Alice"


# ---------------------------------------------------------------------------
# Scenario (c) — update with description change emits modified snapshot
# ---------------------------------------------------------------------------


def test_update_entity_description_emits_modified_with_post_state() -> None:
    actor = _actor()
    captured = _capture(actor)

    actor.update_graph(
        ManageGraph(
            create_entities=[
                EntityCreate(name="Alice", entity_type="Person", description="Engineer")
            ]
        )
    )
    actor.update_graph(
        ManageGraph(update_entities=[EntityUpdate(name="Alice", description="Architect")])
    )

    assert len(captured) == 2
    update_event = captured[1]
    assert update_event.seq == 2
    assert update_event.payload.entities_added == []
    assert len(update_event.payload.entities_modified) == 1
    assert update_event.payload.entities_modified[0].description == "Architect"


# ---------------------------------------------------------------------------
# Scenario (d) — no-op update emits nothing
# ---------------------------------------------------------------------------


def test_update_entity_no_op_emits_nothing() -> None:
    actor = _actor()
    captured = _capture(actor)

    actor.update_graph(
        ManageGraph(
            create_entities=[
                EntityCreate(name="Alice", entity_type="Person", description="Engineer")
            ]
        )
    )

    observer = _StateNotifyObserver()
    actor.state.observer(observer)  # type: ignore[arg-type]
    baseline = observer.calls

    # Description matches existing value -> no-op
    actor.update_graph(
        ManageGraph(update_entities=[EntityUpdate(name="Alice", description="Engineer")])
    )

    assert len(captured) == 1  # only the seed event
    assert actor._state_event_seq == 1
    # notify_state_change still invoked on the second update_graph call
    assert observer.calls > baseline


# ---------------------------------------------------------------------------
# Scenario (e) — delete entity with no incident relations
# ---------------------------------------------------------------------------


def test_delete_entity_no_relations_emits_entities_removed() -> None:
    actor = _actor()
    captured = _capture(actor)

    actor.update_graph(
        ManageGraph(
            create_entities=[
                EntityCreate(name="Alice", entity_type="Person", description="Engineer")
            ]
        )
    )
    alice_id = actor.get_graph().entities[0].id

    actor.update_graph(ManageGraph(delete_entities=["Alice"]))

    assert len(captured) == 2
    delete_event = captured[1]
    assert delete_event.seq == 2
    assert delete_event.payload.entities_removed == [alice_id]
    assert delete_event.payload.relations_removed == []


# ---------------------------------------------------------------------------
# Scenario (f) — delete entity with cascade
# ---------------------------------------------------------------------------


def test_delete_entity_cascades_single_event() -> None:
    actor = _actor()
    captured = _capture(actor)

    actor.update_graph(
        ManageGraph(
            create_entities=[
                EntityCreate(name="Alice", entity_type="Person", description="Engineer"),
                EntityCreate(name="Bob", entity_type="Person", description="Designer"),
            ],
            create_relations=[
                RelationCreate(from_entity="Alice", to_entity="Bob", relation_type="knows")
            ],
        )
    )

    graph = actor.get_graph()
    alice_id = next(e.id for e in graph.entities if e.name == "Alice")
    rel_id = graph.relations[0].id

    actor.update_graph(ManageGraph(delete_entities=["Alice"]))

    assert len(captured) == 2
    cascade_event = captured[1]
    assert cascade_event.payload.entities_removed == [alice_id]
    assert cascade_event.payload.relations_removed == [rel_id]


# ---------------------------------------------------------------------------
# Scenario (g) — duplicate batch emits nothing but still notifies state
# ---------------------------------------------------------------------------


def test_duplicate_batch_emits_nothing_but_notifies_state_change() -> None:
    actor = _actor()
    captured = _capture(actor)

    actor.update_graph(
        ManageGraph(
            create_entities=[
                EntityCreate(name="Alice", entity_type="Person", description="Engineer")
            ]
        )
    )
    assert len(captured) == 1

    observer = _StateNotifyObserver()
    actor.state.observer(observer)  # type: ignore[arg-type]
    baseline = observer.calls

    with pytest.raises(RetriableError):
        actor.update_graph(
            ManageGraph(
                create_entities=[
                    EntityCreate(name="Alice", entity_type="Person", description="Engineer")
                ]
            )
        )

    assert len(captured) == 1  # no new event
    assert actor._state_event_seq == 1
    assert observer.calls > baseline


# ---------------------------------------------------------------------------
# Scenario (h) — partial failure emits before raising
# ---------------------------------------------------------------------------


def test_partial_failure_emits_before_raise() -> None:
    actor = _actor()
    captured = _capture(actor)

    actor.update_graph(
        ManageGraph(
            create_entities=[
                EntityCreate(name="Alice", entity_type="Person", description="Engineer")
            ]
        )
    )
    assert len(captured) == 1

    with pytest.raises(RetriableError):
        actor.update_graph(
            ManageGraph(
                create_entities=[
                    EntityCreate(name="Bob", entity_type="Person", description="Designer"),
                    EntityCreate(name="Alice", entity_type="Person", description="Duplicate Alice"),
                ]
            )
        )

    # Event for the Bob success was emitted before the raise.
    assert len(captured) == 2
    partial_event = captured[1]
    assert partial_event.seq == 2
    assert len(partial_event.payload.entities_added) == 1
    assert partial_event.payload.entities_added[0].name == "Bob"


# ---------------------------------------------------------------------------
# Scenario (i) — seq monotonic
# ---------------------------------------------------------------------------


def test_seq_is_monotonic_across_batches() -> None:
    actor = _actor()
    captured = _capture(actor)

    for name in ["A", "B", "C"]:
        actor.update_graph(
            ManageGraph(
                create_entities=[EntityCreate(name=name, entity_type="Person", description="x")]
            )
        )

    assert [e.seq for e in captured] == [1, 2, 3]


# ---------------------------------------------------------------------------
# Scenario (j) — mixed batch single event, no duplicate cascaded ids
# ---------------------------------------------------------------------------


def test_mixed_batch_single_event_with_all_sections() -> None:
    actor = _actor()
    captured = _capture(actor)

    # Seed: Alice, Bob, Carol; two relations — Alice→Bob (knows), Alice→Carol (knows).
    actor.update_graph(
        ManageGraph(
            create_entities=[
                EntityCreate(name="Alice", entity_type="Person", description="Engineer"),
                EntityCreate(name="Bob", entity_type="Person", description="Designer"),
                EntityCreate(name="Carol", entity_type="Person", description="PM"),
            ],
            create_relations=[
                RelationCreate(from_entity="Alice", to_entity="Bob", relation_type="knows"),
                RelationCreate(from_entity="Alice", to_entity="Carol", relation_type="knows"),
            ],
        )
    )
    assert len(captured) == 1

    # Mixed batch: create new entity Dave, create new relation Bob→Carol,
    # update Carol's description, and explicitly delete Alice→Bob (knows).
    actor.update_graph(
        ManageGraph(
            create_entities=[EntityCreate(name="Dave", entity_type="Person", description="QA")],
            create_relations=[
                RelationCreate(from_entity="Bob", to_entity="Carol", relation_type="works_with")
            ],
            update_entities=[EntityUpdate(name="Carol", description="Senior PM")],
            delete_relations=[
                RelationDelete(from_entity="Alice", to_entity="Bob", relation_type="knows")
            ],
        )
    )

    assert len(captured) == 2
    mixed_event = captured[1]
    payload = mixed_event.payload
    assert len(payload.entities_added) == 1
    assert payload.entities_added[0].name == "Dave"
    assert len(payload.relations_added) == 1
    assert payload.relations_added[0].from_entity == "Bob"
    assert len(payload.entities_modified) == 1
    assert payload.entities_modified[0].name == "Carol"
    assert payload.entities_modified[0].description == "Senior PM"
    assert len(payload.relations_removed) == 1  # the Alice→Bob relation

    # Follow-up: delete an entity whose incident relation was already
    # removed in the earlier batch — assert no duplicate relation id
    # appears in relations_removed (dedup across explicit + cascade).
    # Alice→Carol still exists; delete Alice, cascading only Alice→Carol.
    carol_rel_id = next(
        r.id
        for r in actor.get_graph().relations
        if r.from_entity == "Alice" and r.to_entity == "Carol"
    )
    actor.update_graph(ManageGraph(delete_entities=["Alice"]))

    assert len(captured) == 3
    cascade_event = captured[2]
    # Only the cascade id should appear, exactly once.
    assert cascade_event.payload.relations_removed == [carol_rel_id]
    assert len(cascade_event.payload.relations_removed) == len(
        set(cascade_event.payload.relations_removed)
    )


# ---------------------------------------------------------------------------
# Scenario (k) — on_start emits no event
# ---------------------------------------------------------------------------


def test_on_start_emits_no_event() -> None:
    actor = KnowledgeGraphActor()
    notify = MagicMock()
    actor.notify_event = notify  # type: ignore[method-assign]
    actor.on_start()
    assert notify.call_count == 0


# ---------------------------------------------------------------------------
# Scenario (l) — envelope shape
# ---------------------------------------------------------------------------


def test_event_envelope_shape() -> None:
    actor = _actor()
    captured = _capture(actor)

    actor.update_graph(
        ManageGraph(
            create_entities=[
                EntityCreate(name="Alice", entity_type="Person", description="Engineer")
            ]
        )
    )

    event = captured[0]
    assert isinstance(event, ToolStateEvent)
    assert isinstance(event.payload, KnowledgeGraphStateEvent)
    assert event.tool_id == KG_ACTOR_NAME
    assert event.seq >= 1
    assert hasattr(event, "team_id")
