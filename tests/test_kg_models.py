"""Tests for Knowledge Graph data models.

Covers: UUID auto-generation, defaults, Pydantic validation,
serialization round-trip for all models (Task 1.7).
"""

from __future__ import annotations

import uuid

import pytest

from akgentic.tool.knowledge_graph.models import (
    Entity,
    EntityCreate,
    EntityUpdate,
    GetGraphQuery,
    KnowledgeGraph,
    KnowledgeGraphState,
    ManageGraph,
    PathStep,
    Relation,
    RelationCreate,
    RelationDelete,
    SearchQuery,
    SearchResult,
)

# ---------------------------------------------------------------------------
# Entity model (AC-2, Task 1.1)
# ---------------------------------------------------------------------------


class TestEntity:
    """Tests for Entity model."""

    def test_auto_generated_uuid(self) -> None:
        """Entity.id is a UUID auto-generated on creation."""
        e = Entity(name="Alice", entity_type="Person", description="A person")
        assert isinstance(e.id, uuid.UUID)

    def test_two_entities_have_different_uuids(self) -> None:
        e1 = Entity(name="Alice", entity_type="Person", description="A person")
        e2 = Entity(name="Bob", entity_type="Person", description="Another person")
        assert e1.id != e2.id

    def test_observations_default_empty(self) -> None:
        e = Entity(name="Alice", entity_type="Person", description="A person")
        assert e.observations == []

    def test_observations_set_explicitly(self) -> None:
        e = Entity(
            name="Alice",
            entity_type="Person",
            description="A person",
            observations=["friendly", "tall"],
        )
        assert e.observations == ["friendly", "tall"]

    def test_required_fields_enforced(self) -> None:
        with pytest.raises(Exception):  # noqa: B017
            Entity()  # type: ignore[call-arg]

    def test_all_fields_type_checked(self) -> None:
        e = Entity(name="Alice", entity_type="Person", description="A person")
        assert isinstance(e.name, str)
        assert isinstance(e.entity_type, str)
        assert isinstance(e.description, str)
        assert isinstance(e.observations, list)

    def test_serialization_round_trip(self) -> None:
        e = Entity(
            name="Alice",
            entity_type="Person",
            description="A person",
            observations=["friendly"],
        )
        data = e.model_dump()
        restored = Entity.model_validate(data)
        assert restored.name == e.name
        assert restored.entity_type == e.entity_type
        assert restored.description == e.description
        assert restored.observations == e.observations
        # UUID survives round-trip (as string in dump, parsed back)
        assert str(restored.id) == str(e.id)

    def test_is_root_defaults_false(self) -> None:
        e = Entity(name="Alice", entity_type="Person", description="A person")
        assert e.is_root is False

    def test_is_root_set_true(self) -> None:
        e = Entity(name="Alice", entity_type="Person", description="A person", is_root=True)
        assert e.is_root is True

    def test_is_root_survives_serialization(self) -> None:
        e = Entity(name="R", entity_type="T", description="d", is_root=True)
        restored = Entity.model_validate(e.model_dump())
        assert restored.is_root is True


# ---------------------------------------------------------------------------
# Relation model (AC-3, Task 1.2)
# ---------------------------------------------------------------------------


class TestRelation:
    """Tests for Relation model."""

    def test_auto_generated_uuid(self) -> None:
        r = Relation(from_entity="A", to_entity="B", relation_type="knows")
        assert isinstance(r.id, uuid.UUID)

    def test_description_defaults_empty(self) -> None:
        r = Relation(from_entity="A", to_entity="B", relation_type="knows")
        assert r.description == ""

    def test_description_set_explicitly(self) -> None:
        r = Relation(
            from_entity="A", to_entity="B", relation_type="knows", description="since 2020"
        )
        assert r.description == "since 2020"

    def test_required_fields_enforced(self) -> None:
        with pytest.raises(Exception):  # noqa: B017
            Relation()  # type: ignore[call-arg]

    def test_serialization_round_trip(self) -> None:
        r = Relation(from_entity="A", to_entity="B", relation_type="knows", description="well")
        data = r.model_dump()
        restored = Relation.model_validate(data)
        assert restored.from_entity == r.from_entity
        assert restored.to_entity == r.to_entity
        assert restored.relation_type == r.relation_type
        assert restored.description == r.description
        assert str(restored.id) == str(r.id)


# ---------------------------------------------------------------------------
# CRUD request models (Task 1.3)
# ---------------------------------------------------------------------------


class TestEntityCreate:
    def test_defaults(self) -> None:
        ec = EntityCreate(name="X", entity_type="Concept", description="desc")
        assert ec.observations == []

    def test_with_observations(self) -> None:
        ec = EntityCreate(name="X", entity_type="Concept", description="desc", observations=["a"])
        assert ec.observations == ["a"]

    def test_is_root_defaults_false(self) -> None:
        ec = EntityCreate(name="X", entity_type="Concept", description="desc")
        assert ec.is_root is False

    def test_is_root_set_true(self) -> None:
        ec = EntityCreate(name="X", entity_type="Concept", description="desc", is_root=True)
        assert ec.is_root is True


class TestEntityUpdate:
    def test_all_optional_fields_default_none(self) -> None:
        eu = EntityUpdate(name="X")
        assert eu.description is None
        assert eu.entity_type is None
        assert eu.add_observations is None
        assert eu.remove_observations is None
        assert eu.is_root is None

    def test_partial_fields(self) -> None:
        eu = EntityUpdate(name="X", description="new desc")
        assert eu.description == "new desc"
        assert eu.entity_type is None

    def test_is_root_none_is_noop(self) -> None:
        eu = EntityUpdate(name="X")
        assert eu.is_root is None

    def test_is_root_set_true(self) -> None:
        eu = EntityUpdate(name="X", is_root=True)
        assert eu.is_root is True

    def test_is_root_set_false(self) -> None:
        eu = EntityUpdate(name="X", is_root=False)
        assert eu.is_root is False


class TestRelationCreate:
    def test_defaults(self) -> None:
        rc = RelationCreate(from_entity="A", to_entity="B", relation_type="knows")
        assert rc.description == ""

    def test_with_description(self) -> None:
        rc = RelationCreate(
            from_entity="A", to_entity="B", relation_type="knows", description="well"
        )
        assert rc.description == "well"


class TestRelationDelete:
    def test_fields(self) -> None:
        rd = RelationDelete(from_entity="A", to_entity="B", relation_type="knows")
        assert rd.from_entity == "A"
        assert rd.to_entity == "B"
        assert rd.relation_type == "knows"


# ---------------------------------------------------------------------------
# ManageGraph batch model (Task 1.4)
# ---------------------------------------------------------------------------


class TestManageGraph:
    def test_all_defaults_empty(self) -> None:
        mg = ManageGraph()
        assert mg.create_entities == []
        assert mg.update_entities == []
        assert mg.delete_entities == []
        assert mg.create_relations == []
        assert mg.delete_relations == []

    def test_with_data(self) -> None:
        mg = ManageGraph(
            create_entities=[EntityCreate(name="A", entity_type="T", description="d")],
            delete_entities=["B"],
        )
        assert len(mg.create_entities) == 1
        assert mg.delete_entities == ["B"]


# ---------------------------------------------------------------------------
# KnowledgeGraph container (Task 1.5)
# ---------------------------------------------------------------------------


class TestKnowledgeGraph:
    def test_defaults_empty(self) -> None:
        kg = KnowledgeGraph()
        assert kg.entities == []
        assert kg.relations == []

    def test_with_data(self) -> None:
        e = Entity(name="A", entity_type="T", description="d")
        r = Relation(from_entity="A", to_entity="B", relation_type="knows")
        kg = KnowledgeGraph(entities=[e], relations=[r])
        assert len(kg.entities) == 1
        assert len(kg.relations) == 1


# ---------------------------------------------------------------------------
# KnowledgeGraphState (Task 1.6)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# PathStep model (Story 3.1, Task 1.1)
# ---------------------------------------------------------------------------


class TestPathStep:
    """AC-1: PathStep model for directed traversal waypoints."""

    def test_construction(self) -> None:
        ps = PathStep(relation_type="HAS_COMPONENT", to_entity="ComponentA")
        assert ps.relation_type == "HAS_COMPONENT"
        assert ps.to_entity == "ComponentA"

    def test_required_fields_enforced(self) -> None:
        with pytest.raises(Exception):  # noqa: B017
            PathStep()  # type: ignore[call-arg]

    def test_serialization_round_trip(self) -> None:
        ps = PathStep(relation_type="DEPENDS_ON", to_entity="ServiceB")
        restored = PathStep.model_validate(ps.model_dump())
        assert restored.relation_type == ps.relation_type
        assert restored.to_entity == ps.to_entity

    def test_list_of_path_steps(self) -> None:
        steps = [
            PathStep(relation_type="HAS_COMPONENT", to_entity="ComponentA"),
            PathStep(relation_type="DEPENDS_ON", to_entity="ServiceB"),
        ]
        assert len(steps) == 2


class TestGetGraphQueryWithPath:
    """AC-1: GetGraphQuery path field (Story 3.1, Task 1.2)."""

    def test_path_defaults_empty(self) -> None:
        q = GetGraphQuery()
        assert q.path == []

    def test_path_with_steps(self) -> None:
        q = GetGraphQuery(
            entity_names=["Device"],
            path=[PathStep(relation_type="HAS_COMPONENT", to_entity="ComponentA")],
            depth=2,
        )
        assert len(q.path) == 1
        assert q.path[0].relation_type == "HAS_COMPONENT"
        assert q.path[0].to_entity == "ComponentA"
        assert q.depth == 2

    def test_path_serialization_round_trip(self) -> None:
        q = GetGraphQuery(
            entity_names=["A"],
            path=[PathStep(relation_type="R", to_entity="B")],
        )
        restored = GetGraphQuery.model_validate(q.model_dump())
        assert len(restored.path) == 1
        assert restored.path[0].relation_type == "R"
        assert restored.path[0].to_entity == "B"


# ---------------------------------------------------------------------------
# SearchQuery Epic 3 expansion fields (Story 3.1, Task 2.1)
# ---------------------------------------------------------------------------


class TestSearchQueryEpic3Fields:
    """AC-2, AC-3, AC-4: SearchQuery expansion fields default to False."""

    def test_include_neighbors_defaults_false(self) -> None:
        sq = SearchQuery(query="test")
        assert sq.include_neighbors is False

    def test_include_edges_defaults_false(self) -> None:
        sq = SearchQuery(query="test")
        assert sq.include_edges is False

    def test_find_paths_defaults_false(self) -> None:
        sq = SearchQuery(query="test")
        assert sq.find_paths is False

    def test_expansion_fields_can_be_set(self) -> None:
        sq = SearchQuery(query="test", include_neighbors=True, include_edges=True, find_paths=True)
        assert sq.include_neighbors is True
        assert sq.include_edges is True
        assert sq.find_paths is True

    def test_serialization_round_trip_with_expansion_fields(self) -> None:
        sq = SearchQuery(query="x", include_neighbors=True, find_paths=True)
        restored = SearchQuery.model_validate(sq.model_dump())
        assert restored.include_neighbors is True
        assert restored.find_paths is True
        assert restored.include_edges is False


# ---------------------------------------------------------------------------
# SearchResult Epic 3 expansion fields (Story 3.1, Task 2.2)
# ---------------------------------------------------------------------------


class TestSearchResultEpic3Fields:
    """AC-2, AC-3, AC-4: SearchResult expansion fields default to empty."""

    def test_neighbors_defaults_empty(self) -> None:
        sr = SearchResult()
        assert sr.neighbors == []

    def test_connected_relations_defaults_empty(self) -> None:
        sr = SearchResult()
        assert sr.connected_relations == []

    def test_paths_defaults_empty(self) -> None:
        sr = SearchResult()
        assert sr.paths == []

    def test_neighbors_with_entities(self) -> None:
        e = Entity(name="N", entity_type="T", description="d")
        sr = SearchResult(neighbors=[e])
        assert len(sr.neighbors) == 1
        assert sr.neighbors[0].name == "N"

    def test_connected_relations_with_data(self) -> None:
        r = Relation(from_entity="A", to_entity="B", relation_type="R")
        sr = SearchResult(connected_relations=[r])
        assert len(sr.connected_relations) == 1

    def test_paths_with_entity_relation_sequence(self) -> None:
        e1 = Entity(name="A", entity_type="T", description="d")
        r = Relation(from_entity="A", to_entity="B", relation_type="R")
        e2 = Entity(name="B", entity_type="T", description="d")
        sr = SearchResult(paths=[[e1, r, e2]])
        assert len(sr.paths) == 1
        assert len(sr.paths[0]) == 3

    def test_serialization_round_trip_with_expansion_fields(self) -> None:
        from akgentic.tool.knowledge_graph.models import SearchHit
        hit = SearchHit(ref_type="entity", ref_id="x", score=1.0)
        e = Entity(name="N", entity_type="T", description="d")
        sr = SearchResult(hits=[hit], neighbors=[e])
        restored = SearchResult.model_validate(sr.model_dump())
        assert len(restored.hits) == 1
        assert len(restored.neighbors) == 1
        assert restored.neighbors[0].name == "N"

    def test_paths_serialization_round_trip(self) -> None:
        """Serialization round-trip for paths: list[list[Entity | Relation]]."""
        e1 = Entity(name="A", entity_type="T", description="d1")
        r = Relation(from_entity="A", to_entity="B", relation_type="LINKS")
        e2 = Entity(name="B", entity_type="T", description="d2")
        sr = SearchResult(paths=[[e1, r, e2]])
        dumped = sr.model_dump()
        restored = SearchResult.model_validate(dumped)
        assert len(restored.paths) == 1
        path = restored.paths[0]
        assert len(path) == 3
        assert isinstance(path[0], Entity)
        assert isinstance(path[1], Relation)
        assert isinstance(path[2], Entity)
        assert path[0].name == "A"
        assert path[1].relation_type == "LINKS"
        assert path[2].name == "B"


class TestKnowledgeGraphState:
    def test_default_knowledge_graph(self) -> None:
        state = KnowledgeGraphState()
        assert isinstance(state.knowledge_graph, KnowledgeGraph)
        assert state.knowledge_graph.entities == []

    def test_inherits_base_state(self) -> None:
        from akgentic.core.agent_state import BaseState

        state = KnowledgeGraphState()
        assert isinstance(state, BaseState)

    def test_notify_state_change_without_observer(self) -> None:
        """notify_state_change should be a no-op without observer."""
        state = KnowledgeGraphState()
        state.notify_state_change()  # Should not raise
