"""Tests for ``ToolStateEvent`` envelope and ``KnowledgeGraphStateEvent`` payload.

Covers Story 17.1 (ADR-024): envelope + payload round-trip serialization,
``__model__`` dispatch of the payload, and validation of required fields.
"""

from __future__ import annotations

import uuid

import pytest
from pydantic import ValidationError

from akgentic.tool import KnowledgeGraphStateEvent, ToolStateEvent
from akgentic.tool.knowledge_graph.models import Entity, Relation


def _sample_entity(name: str = "Alice") -> Entity:
    return Entity(name=name, entity_type="Person", description="desc")


def _sample_relation(relation_type: str = "KNOWS") -> Relation:
    return Relation(from_entity="Alice", to_entity="Bob", relation_type=relation_type)


# ---------------------------------------------------------------------------
# KnowledgeGraphStateEvent payload round-trip tests (AC #7)
# ---------------------------------------------------------------------------


class TestKnowledgeGraphStateEvent:
    """Round-trip tests for the KG delta payload (AC #3, #7)."""

    def test_defaults_empty(self) -> None:
        ev = KnowledgeGraphStateEvent()
        assert ev.entities_added == []
        assert ev.entities_modified == []
        assert ev.entities_removed == []
        assert ev.relations_added == []
        assert ev.relations_modified == []
        assert ev.relations_removed == []

    def test_roundtrip_all_populated(self) -> None:
        e1 = _sample_entity("Alice")
        e2 = _sample_entity("Bob")
        r1 = _sample_relation("KNOWS")
        r2 = _sample_relation("LIKES")
        removed_entity = uuid.uuid4()
        removed_relation = uuid.uuid4()

        ev = KnowledgeGraphStateEvent(
            entities_added=[e1],
            entities_modified=[e2],
            entities_removed=[removed_entity],
            relations_added=[r1],
            relations_modified=[r2],
            relations_removed=[removed_relation],
        )
        restored = KnowledgeGraphStateEvent.model_validate(ev.model_dump())

        assert len(restored.entities_added) == 1
        assert restored.entities_added[0].name == "Alice"
        assert isinstance(restored.entities_added[0], Entity)
        assert restored.entities_modified[0].name == "Bob"
        assert restored.entities_removed == [removed_entity]
        assert restored.relations_added[0].relation_type == "KNOWS"
        assert isinstance(restored.relations_added[0], Relation)
        assert restored.relations_modified[0].relation_type == "LIKES"
        assert restored.relations_removed == [removed_relation]

    def test_roundtrip_only_entities_added(self) -> None:
        ev = KnowledgeGraphStateEvent(entities_added=[_sample_entity("Only")])
        restored = KnowledgeGraphStateEvent.model_validate(ev.model_dump())
        assert len(restored.entities_added) == 1
        assert restored.entities_modified == []
        assert restored.entities_removed == []
        assert restored.relations_added == []
        assert restored.relations_modified == []
        assert restored.relations_removed == []

    def test_roundtrip_only_relations_removed(self) -> None:
        rid = uuid.uuid4()
        ev = KnowledgeGraphStateEvent(relations_removed=[rid])
        restored = KnowledgeGraphStateEvent.model_validate(ev.model_dump())
        assert restored.relations_removed == [rid]
        assert restored.entities_added == []
        assert restored.entities_modified == []
        assert restored.entities_removed == []
        assert restored.relations_added == []
        assert restored.relations_modified == []

    def test_roundtrip_only_entities_modified(self) -> None:
        ev = KnowledgeGraphStateEvent(entities_modified=[_sample_entity("Mod")])
        restored = KnowledgeGraphStateEvent.model_validate(ev.model_dump())
        assert len(restored.entities_modified) == 1
        assert restored.entities_added == []
        assert restored.entities_removed == []
        assert restored.relations_added == []
        assert restored.relations_modified == []
        assert restored.relations_removed == []


# ---------------------------------------------------------------------------
# ToolStateEvent envelope tests (AC #1, #7)
# ---------------------------------------------------------------------------


class TestToolStateEvent:
    """Round-trip tests for the envelope around a KG payload (AC #1, #7)."""

    def test_envelope_roundtrip_preserves_all_fields(self) -> None:
        team_id = uuid.uuid4()
        payload = KnowledgeGraphStateEvent(entities_added=[_sample_entity("Alice")])
        ev = ToolStateEvent(
            tool_id="#KnowledgeGraphTool",
            seq=1,
            payload=payload,
            team_id=team_id,
        )

        restored = ToolStateEvent.model_validate(ev.model_dump())

        assert restored.tool_id == "#KnowledgeGraphTool"
        assert restored.seq == 1
        assert restored.team_id == team_id
        assert restored.id == ev.id
        assert restored.timestamp == ev.timestamp
        # Payload must be reconstructed as a real model, not a dict (AC #7)
        assert isinstance(restored.payload, KnowledgeGraphStateEvent)
        assert len(restored.payload.entities_added) == 1
        assert restored.payload.entities_added[0].name == "Alice"

    def test_envelope_roundtrip_with_none_team_id(self) -> None:
        payload = KnowledgeGraphStateEvent()
        ev = ToolStateEvent(tool_id="#KG", seq=2, payload=payload)
        restored = ToolStateEvent.model_validate(ev.model_dump())
        assert restored.team_id is None
        assert restored.seq == 2
        assert isinstance(restored.payload, KnowledgeGraphStateEvent)

    def test_envelope_requires_payload(self) -> None:
        with pytest.raises(ValidationError):
            ToolStateEvent(tool_id="#KG", seq=1)  # type: ignore[call-arg]

    def test_envelope_requires_tool_id_and_seq(self) -> None:
        with pytest.raises(ValidationError):
            ToolStateEvent(payload=KnowledgeGraphStateEvent())  # type: ignore[call-arg]

    def test_envelope_payload_has_model_marker(self) -> None:
        """The payload dict in the dumped envelope carries ``__model__`` (AC #7, dispatch)."""
        ev = ToolStateEvent(
            tool_id="#KG",
            seq=1,
            payload=KnowledgeGraphStateEvent(entities_added=[_sample_entity()]),
        )
        dumped = ev.model_dump()
        assert isinstance(dumped["payload"], dict)
        assert "__model__" in dumped["payload"]
        assert dumped["payload"]["__model__"].endswith("KnowledgeGraphStateEvent")
