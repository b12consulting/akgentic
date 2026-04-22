"""Tests for Knowledge Graph query and search models and operations.

Covers Story 1.2: GetGraphQuery, GraphView, SearchQuery, SearchHit,
SearchResult models; get_graph() subgraph retrieval; keyword search.

Pattern: Instantiate KnowledgeGraphActor() directly, call on_start(),
populate graph via update_graph(), then test get_graph() and search().
"""

from __future__ import annotations

import pytest

from akgentic.tool.knowledge_graph.kg_actor import KnowledgeGraphActor
from akgentic.tool.knowledge_graph.models import (
    Entity,
    EntityCreate,
    GetGraphQuery,
    GraphView,
    ManageGraph,
    PathStep,
    Relation,
    RelationCreate,
    SearchHit,
    SearchQuery,
    SearchResult,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _actor() -> KnowledgeGraphActor:
    """Create and initialize a KnowledgeGraphActor for testing."""
    actor = KnowledgeGraphActor()
    actor.on_start()
    return actor


def _seed_graph(actor: KnowledgeGraphActor) -> None:
    """Populate a test graph with ~5 entities and ~6 relations of varied types.

    Graph topology:
        Product --DEPENDS_ON--> Library
        Product --DEPENDS_ON--> Framework
        Library --USES--> Algorithm
        Framework --USES--> Algorithm
        Algorithm --DESCRIBED_IN--> Paper
        Product --MAINTAINED_BY--> Team
    """
    actor.update_graph(
        ManageGraph(
            create_entities=[
                EntityCreate(
                    name="Product",
                    entity_type="Software",
                    description="Signal processing toolkit",
                ),
                EntityCreate(
                    name="Library",
                    entity_type="Software",
                    description="Math utility library",
                ),
                EntityCreate(
                    name="Framework",
                    entity_type="Software",
                    description="Web framework for APIs",
                ),
                EntityCreate(
                    name="Algorithm",
                    entity_type="Concept",
                    description="FFT signal processing algorithm",
                ),
                EntityCreate(
                    name="Paper",
                    entity_type="Document",
                    description="Research paper on signal processing",
                ),
                EntityCreate(
                    name="Team",
                    entity_type="Organization",
                    description="Core engineering team",
                ),
            ],
            create_relations=[
                RelationCreate(
                    from_entity="Product",
                    to_entity="Library",
                    relation_type="DEPENDS_ON",
                    description="runtime dependency",
                ),
                RelationCreate(
                    from_entity="Product",
                    to_entity="Framework",
                    relation_type="DEPENDS_ON",
                    description="API layer dependency",
                ),
                RelationCreate(
                    from_entity="Library",
                    to_entity="Algorithm",
                    relation_type="USES",
                    description="implements algorithm",
                ),
                RelationCreate(
                    from_entity="Framework",
                    to_entity="Algorithm",
                    relation_type="USES",
                    description="uses for processing",
                ),
                RelationCreate(
                    from_entity="Algorithm",
                    to_entity="Paper",
                    relation_type="DESCRIBED_IN",
                    description="original publication",
                ),
                RelationCreate(
                    from_entity="Product",
                    to_entity="Team",
                    relation_type="MAINTAINED_BY",
                    description="owned by team",
                ),
            ],
        )
    )


# ===========================================================================
# Task 1 — Model tests (subtasks 1.1–1.6)
# ===========================================================================


class TestGetGraphQuery:
    """Subtask 1.1: GetGraphQuery model."""

    def test_defaults(self) -> None:
        q = GetGraphQuery()
        assert q.entity_names == []
        assert q.relation_types == []
        assert q.depth is None
        assert q.roots_only is False

    def test_explicit_values(self) -> None:
        q = GetGraphQuery(entity_names=["A"], relation_types=["R"], depth=3)
        assert q.entity_names == ["A"]
        assert q.relation_types == ["R"]
        assert q.depth == 3

    def test_serialization_round_trip(self) -> None:
        q = GetGraphQuery(entity_names=["X"], depth=2)
        restored = GetGraphQuery.model_validate(q.model_dump())
        assert restored.entity_names == q.entity_names
        assert restored.depth == q.depth


class TestGraphView:
    """Subtask 1.2: GraphView model."""

    def test_defaults_empty(self) -> None:
        gv = GraphView()
        assert gv.entities == []
        assert gv.relations == []

    def test_with_data(self) -> None:
        e = Entity(name="A", entity_type="T", description="d")
        r = Relation(from_entity="A", to_entity="B", relation_type="R")
        gv = GraphView(entities=[e], relations=[r])
        assert len(gv.entities) == 1
        assert len(gv.relations) == 1

    def test_serialization_round_trip(self) -> None:
        e = Entity(name="A", entity_type="T", description="d")
        gv = GraphView(entities=[e])
        restored = GraphView.model_validate(gv.model_dump())
        assert restored.entities[0].name == "A"


class TestSearchQuery:
    """Subtask 1.3: SearchQuery model."""

    def test_defaults(self) -> None:
        sq = SearchQuery(query="test")
        assert sq.query == "test"
        assert sq.top_k is None
        assert sq.mode == "hybrid"

    def test_keyword_mode(self) -> None:
        sq = SearchQuery(query="test", mode="keyword")
        assert sq.mode == "keyword"

    def test_vector_mode(self) -> None:
        sq = SearchQuery(query="test", mode="vector")
        assert sq.mode == "vector"

    def test_invalid_mode_rejected(self) -> None:
        with pytest.raises(Exception):  # noqa: B017
            SearchQuery(query="test", mode="invalid")  # type: ignore[arg-type]

    def test_query_required(self) -> None:
        with pytest.raises(Exception):  # noqa: B017
            SearchQuery()  # type: ignore[call-arg]

    def test_serialization_round_trip(self) -> None:
        sq = SearchQuery(query="hello", top_k=5, mode="keyword")
        restored = SearchQuery.model_validate(sq.model_dump())
        assert restored.query == "hello"
        assert restored.top_k == 5
        assert restored.mode == "keyword"


class TestSearchHit:
    """Subtask 1.4: SearchHit model."""

    def test_entity_hit(self) -> None:
        e = Entity(name="A", entity_type="T", description="d")
        hit = SearchHit(ref_type="entity", ref_id=str(e.id), score=1.0, entity=e)
        assert hit.ref_type == "entity"
        assert hit.entity is not None
        assert hit.relation is None

    def test_relation_hit(self) -> None:
        r = Relation(from_entity="A", to_entity="B", relation_type="R")
        hit = SearchHit(ref_type="relation", ref_id=str(r.id), score=0.5, relation=r)
        assert hit.ref_type == "relation"
        assert hit.relation is not None
        assert hit.entity is None

    def test_defaults_none(self) -> None:
        hit = SearchHit(ref_type="entity", ref_id="abc", score=1.0)
        assert hit.entity is None
        assert hit.relation is None

    def test_invalid_ref_type_rejected(self) -> None:
        with pytest.raises(Exception):  # noqa: B017
            SearchHit(ref_type="unknown", ref_id="x", score=1.0)  # type: ignore[arg-type]

    def test_serialization_round_trip(self) -> None:
        hit = SearchHit(ref_type="entity", ref_id="123", score=0.9)
        restored = SearchHit.model_validate(hit.model_dump())
        assert restored.ref_type == "entity"
        assert restored.score == 0.9


class TestSearchResult:
    """Subtask 1.5: SearchResult model."""

    def test_defaults_empty(self) -> None:
        sr = SearchResult()
        assert sr.hits == []

    def test_with_hits(self) -> None:
        hit = SearchHit(ref_type="entity", ref_id="x", score=1.0)
        sr = SearchResult(hits=[hit])
        assert len(sr.hits) == 1

    def test_serialization_round_trip(self) -> None:
        hit = SearchHit(ref_type="entity", ref_id="x", score=1.0)
        sr = SearchResult(hits=[hit])
        restored = SearchResult.model_validate(sr.model_dump())
        assert len(restored.hits) == 1
        assert restored.hits[0].ref_id == "x"


# ===========================================================================
# Task 2 — get_graph() subgraph tests (subtasks 2.1–2.6)
# ===========================================================================


class TestGetGraphFullGraph:
    """Subtask 2.1: Full graph return when entity_names is empty."""

    def test_empty_query_returns_full_graph(self) -> None:
        actor = _actor()
        _seed_graph(actor)
        result = actor.get_graph(GetGraphQuery())
        assert isinstance(result, GraphView)
        assert len(result.entities) == 6
        assert len(result.relations) == 6

    def test_empty_graph_returns_empty_view(self) -> None:
        actor = _actor()
        result = actor.get_graph(GetGraphQuery())
        assert result.entities == []
        assert result.relations == []


class TestGetGraphSubgraph:
    """Subtasks 2.2–2.4: BFS subgraph filtering."""

    def test_single_entity_depth_1(self) -> None:
        """Product at depth=1 reaches Library, Framework, Team (direct neighbors)."""
        actor = _actor()
        _seed_graph(actor)
        result = actor.get_graph(GetGraphQuery(entity_names=["Product"], depth=1))
        entity_names = {e.name for e in result.entities}
        assert "Product" in entity_names
        assert "Library" in entity_names
        assert "Framework" in entity_names
        assert "Team" in entity_names
        # Algorithm is 2 hops away via DEPENDS_ON→USES, should NOT be here
        assert "Algorithm" not in entity_names

    def test_multi_hop_depth_2(self) -> None:
        """Product at depth=2 reaches Algorithm (2 hops) but not Paper (3 hops)."""
        actor = _actor()
        _seed_graph(actor)
        result = actor.get_graph(GetGraphQuery(entity_names=["Product"], depth=2))
        entity_names = {e.name for e in result.entities}
        assert "Product" in entity_names
        assert "Algorithm" in entity_names
        # Paper is 3 hops away (Product→Library→Algorithm→Paper)
        assert "Paper" not in entity_names

    def test_depth_3_reaches_paper(self) -> None:
        """Product at depth=3 reaches Paper (3 hops)."""
        actor = _actor()
        _seed_graph(actor)
        result = actor.get_graph(GetGraphQuery(entity_names=["Product"], depth=3))
        entity_names = {e.name for e in result.entities}
        assert "Paper" in entity_names
        assert len(entity_names) == 6  # All entities reachable

    def test_relation_types_filtering(self) -> None:
        """Only DEPENDS_ON edges traversed: Product→Library, Product→Framework."""
        actor = _actor()
        _seed_graph(actor)
        result = actor.get_graph(
            GetGraphQuery(
                entity_names=["Product"],
                relation_types=["DEPENDS_ON"],
                depth=3,
            )
        )
        entity_names = {e.name for e in result.entities}
        assert "Product" in entity_names
        assert "Library" in entity_names
        assert "Framework" in entity_names
        # USES and other edge types are NOT traversed
        assert "Algorithm" not in entity_names
        assert "Paper" not in entity_names

    def test_depth_plus_relation_types_combined(self) -> None:
        """DEPENDS_ON at depth=1: Product + Library + Framework only."""
        actor = _actor()
        _seed_graph(actor)
        result = actor.get_graph(
            GetGraphQuery(
                entity_names=["Product"],
                relation_types=["DEPENDS_ON"],
                depth=1,
            )
        )
        entity_names = {e.name for e in result.entities}
        assert entity_names == {"Product", "Library", "Framework"}

    def test_entity_not_found_returns_empty(self) -> None:
        """Requesting a non-existent entity returns empty GraphView."""
        actor = _actor()
        _seed_graph(actor)
        result = actor.get_graph(GetGraphQuery(entity_names=["NonExistent"]))
        assert result.entities == []
        assert result.relations == []

    def test_relations_only_between_visited_entities(self) -> None:
        """Returned relations connect only entities within the discovered set."""
        actor = _actor()
        _seed_graph(actor)
        result = actor.get_graph(GetGraphQuery(entity_names=["Product"], depth=1))
        entity_names = {e.name for e in result.entities}
        for rel in result.relations:
            assert rel.from_entity in entity_names
            assert rel.to_entity in entity_names

    def test_multiple_root_entities(self) -> None:
        """Starting from Algorithm and Team at depth=0 returns just those two."""
        actor = _actor()
        _seed_graph(actor)
        result = actor.get_graph(
            GetGraphQuery(entity_names=["Algorithm", "Team"], depth=0)
        )
        entity_names = {e.name for e in result.entities}
        assert entity_names == {"Algorithm", "Team"}

    def test_depth_0_returns_only_root(self) -> None:
        """Depth=0 returns only the root entity, no expansion."""
        actor = _actor()
        _seed_graph(actor)
        result = actor.get_graph(
            GetGraphQuery(entity_names=["Product"], depth=0)
        )
        entity_names = {e.name for e in result.entities}
        assert entity_names == {"Product"}


class TestGetGraphBackwardCompat:
    """Subtask 2.5: Backward compatibility — default GetGraphQuery returns full graph."""

    def test_default_query_matches_full_graph(self) -> None:
        actor = _actor()
        _seed_graph(actor)
        result = actor.get_graph(GetGraphQuery())
        assert len(result.entities) == 6
        assert len(result.relations) == 6


# ===========================================================================
# Task 3 — search() keyword tests (subtasks 3.1–3.4)
# ===========================================================================


class TestKeywordSearchEntity:
    """Subtask 3.1: Keyword search matching entity fields."""

    def test_match_entity_name(self) -> None:
        actor = _actor()
        _seed_graph(actor)
        result = actor.search(SearchQuery(query="Product", mode="keyword"))
        entity_hits = [h for h in result.hits if h.ref_type == "entity"]
        assert any(h.entity and h.entity.name == "Product" for h in entity_hits)

    def test_match_entity_description(self) -> None:
        actor = _actor()
        _seed_graph(actor)
        result = actor.search(SearchQuery(query="signal processing", mode="keyword"))
        entity_hits = [h for h in result.hits if h.ref_type == "entity"]
        matched_names = {h.entity.name for h in entity_hits if h.entity}
        assert "Product" in matched_names  # "Signal processing toolkit"
        assert "Algorithm" in matched_names  # "FFT signal processing algorithm"

    def test_match_entity_type(self) -> None:
        actor = _actor()
        _seed_graph(actor)
        result = actor.search(SearchQuery(query="Software", mode="keyword"))
        entity_hits = [h for h in result.hits if h.ref_type == "entity"]
        matched_names = {h.entity.name for h in entity_hits if h.entity}
        assert "Product" in matched_names
        assert "Library" in matched_names
        assert "Framework" in matched_names


class TestKeywordSearchRelation:
    """Subtask 3.1 continued: Keyword search matching relation fields."""

    def test_match_relation_type(self) -> None:
        actor = _actor()
        _seed_graph(actor)
        result = actor.search(SearchQuery(query="DEPENDS_ON", mode="keyword"))
        rel_hits = [h for h in result.hits if h.ref_type == "relation"]
        assert len(rel_hits) == 2  # Product→Library, Product→Framework

    def test_match_relation_description(self) -> None:
        actor = _actor()
        _seed_graph(actor)
        result = actor.search(SearchQuery(query="runtime dependency", mode="keyword"))
        rel_hits = [h for h in result.hits if h.ref_type == "relation"]
        assert len(rel_hits) >= 1

    def test_match_relation_from_entity(self) -> None:
        actor = _actor()
        _seed_graph(actor)
        result = actor.search(SearchQuery(query="Product", mode="keyword"))
        rel_hits = [h for h in result.hits if h.ref_type == "relation"]
        # Product appears as from_entity in several relations
        assert len(rel_hits) >= 1


class TestKeywordSearchBehavior:
    """Subtasks 3.1–3.4: Case insensitivity, empty results, top_k, mode routing."""

    def test_case_insensitive(self) -> None:
        actor = _actor()
        _seed_graph(actor)
        result_lower = actor.search(SearchQuery(query="product", mode="keyword"))
        result_upper = actor.search(SearchQuery(query="PRODUCT", mode="keyword"))
        result_mixed = actor.search(SearchQuery(query="Product", mode="keyword"))
        assert len(result_lower.hits) == len(result_upper.hits) == len(result_mixed.hits)
        assert len(result_lower.hits) > 0

    def test_no_match_returns_empty(self) -> None:
        actor = _actor()
        _seed_graph(actor)
        result = actor.search(SearchQuery(query="zzz_nonexistent_zzz", mode="keyword"))
        assert result.hits == []

    def test_top_k_limit_applied(self) -> None:
        actor = _actor()
        _seed_graph(actor)
        result = actor.search(SearchQuery(query="Product", mode="keyword", top_k=2))
        assert len(result.hits) <= 2

    def test_search_empty_graph_returns_empty(self) -> None:
        actor = _actor()
        result = actor.search(SearchQuery(query="anything", mode="keyword"))
        assert result.hits == []

    def test_hit_score_is_1(self) -> None:
        """Keyword matches produce binary score=1.0."""
        actor = _actor()
        _seed_graph(actor)
        result = actor.search(SearchQuery(query="Product", mode="keyword"))
        for hit in result.hits:
            assert hit.score == 1.0


class TestSearchModeRouting:
    """Subtask 3.2: Mode routing — vector returns empty, hybrid falls back to keyword."""

    def test_vector_mode_returns_empty(self) -> None:
        actor = _actor()
        _seed_graph(actor)
        result = actor.search(SearchQuery(query="Product", mode="vector"))
        assert result.hits == []

    def test_hybrid_mode_falls_back_to_keyword(self) -> None:
        actor = _actor()
        _seed_graph(actor)
        result_hybrid = actor.search(SearchQuery(query="Product", mode="hybrid"))
        result_keyword = actor.search(SearchQuery(query="Product", mode="keyword"))
        # hybrid == keyword-only for now
        assert len(result_hybrid.hits) == len(result_keyword.hits)
        assert len(result_hybrid.hits) > 0

    def test_vector_mode_no_external_calls(self) -> None:
        """AC-5/AC-6: vector mode returns empty without any external API calls."""
        actor = _actor()
        result = actor.search(SearchQuery(query="test", mode="vector"))
        assert isinstance(result, SearchResult)
        assert result.hits == []


# ===========================================================================
# Task 4 — roots_only query tests (Story 1.4, subtask 3.4)
# ===========================================================================


def _seed_graph_with_roots(actor: KnowledgeGraphActor) -> None:
    """Populate a test graph with root entities for roots_only tests.

    Root entities: Product, AuthService
    Non-root: Library, Algorithm, Team
    Relations:
        Product --DEPENDS_ON--> Library
        Product --CONNECTS_TO--> AuthService  (inter-root)
        Library --USES--> Algorithm
        AuthService --MAINTAINED_BY--> Team
    """
    actor.update_graph(
        ManageGraph(
            create_entities=[
                EntityCreate(
                    name="Product",
                    entity_type="Software",
                    description="Main product",
                    is_root=True,
                ),
                EntityCreate(
                    name="AuthService",
                    entity_type="Service",
                    description="Authentication service",
                    is_root=True,
                ),
                EntityCreate(
                    name="Library",
                    entity_type="Software",
                    description="Math utility library",
                ),
                EntityCreate(
                    name="Algorithm",
                    entity_type="Concept",
                    description="FFT algorithm",
                ),
                EntityCreate(
                    name="Team",
                    entity_type="Organization",
                    description="Engineering team",
                ),
            ],
            create_relations=[
                RelationCreate(
                    from_entity="Product",
                    to_entity="Library",
                    relation_type="DEPENDS_ON",
                ),
                RelationCreate(
                    from_entity="Product",
                    to_entity="AuthService",
                    relation_type="CONNECTS_TO",
                ),
                RelationCreate(
                    from_entity="Library",
                    to_entity="Algorithm",
                    relation_type="USES",
                ),
                RelationCreate(
                    from_entity="AuthService",
                    to_entity="Team",
                    relation_type="MAINTAINED_BY",
                ),
            ],
        )
    )


class TestRootsOnly:
    """Story 1.4, AC-4: roots_only=True returns root entities + inter-root relations."""

    def test_roots_only_returns_root_entities(self) -> None:
        actor = _actor()
        _seed_graph_with_roots(actor)
        result = actor.get_graph(GetGraphQuery(roots_only=True))
        entity_names = {e.name for e in result.entities}
        assert entity_names == {"Product", "AuthService"}

    def test_roots_only_returns_inter_root_relations(self) -> None:
        actor = _actor()
        _seed_graph_with_roots(actor)
        result = actor.get_graph(GetGraphQuery(roots_only=True))
        # Only Product→AuthService (inter-root) should be included
        assert len(result.relations) == 1
        assert result.relations[0].from_entity == "Product"
        assert result.relations[0].to_entity == "AuthService"

    def test_roots_only_depth_1_expands_neighbors(self) -> None:
        """AC-5: roots_only + depth=1 expands from roots to direct neighbors."""
        actor = _actor()
        _seed_graph_with_roots(actor)
        result = actor.get_graph(GetGraphQuery(roots_only=True, depth=1))
        entity_names = {e.name for e in result.entities}
        # Product→Library, Product→AuthService, AuthService→Team
        assert "Product" in entity_names
        assert "AuthService" in entity_names
        assert "Library" in entity_names
        assert "Team" in entity_names
        # Algorithm is 2 hops from roots
        assert "Algorithm" not in entity_names

    def test_roots_only_ignores_entity_names(self) -> None:
        """AC-6: entity_names ignored when roots_only=True."""
        actor = _actor()
        _seed_graph_with_roots(actor)
        result = actor.get_graph(
            GetGraphQuery(roots_only=True, entity_names=["Library"])
        )
        entity_names = {e.name for e in result.entities}
        # Should return root entities, not Library
        assert "Product" in entity_names
        assert "AuthService" in entity_names
        # Library is NOT returned because entity_names is ignored
        assert "Library" not in entity_names

    def test_roots_only_empty_graph(self) -> None:
        actor = _actor()
        result = actor.get_graph(GetGraphQuery(roots_only=True))
        assert result.entities == []
        assert result.relations == []

    def test_roots_only_no_root_entities(self) -> None:
        """Graph with entities but none marked is_root → empty result."""
        actor = _actor()
        _seed_graph(actor)  # Uses original seed without is_root
        result = actor.get_graph(GetGraphQuery(roots_only=True))
        assert result.entities == []
        assert result.relations == []

    def test_roots_only_with_relation_types_filter(self) -> None:
        """roots_only + relation_types limits BFS edge traversal."""
        actor = _actor()
        _seed_graph_with_roots(actor)
        result = actor.get_graph(
            GetGraphQuery(roots_only=True, depth=1, relation_types=["DEPENDS_ON"])
        )
        entity_names = {e.name for e in result.entities}
        # Only DEPENDS_ON edges traversed: Product→Library
        assert "Product" in entity_names
        assert "AuthService" in entity_names  # Root entity itself
        assert "Library" in entity_names
        # Team not reached (MAINTAINED_BY not in filter)
        assert "Team" not in entity_names

    def test_roots_only_explicit_depth_0(self) -> None:
        """AC-4: roots_only + explicit depth=0 returns only roots + inter-root relations."""
        actor = _actor()
        _seed_graph_with_roots(actor)
        result = actor.get_graph(GetGraphQuery(roots_only=True, depth=0))
        entity_names = {e.name for e in result.entities}
        assert entity_names == {"Product", "AuthService"}
        # Only inter-root relation
        assert len(result.relations) == 1
        assert result.relations[0].relation_type == "CONNECTS_TO"

    def test_roots_only_depth_2_full_expansion(self) -> None:
        """roots_only + depth=2 reaches 2-hop neighbors from roots."""
        actor = _actor()
        _seed_graph_with_roots(actor)
        result = actor.get_graph(GetGraphQuery(roots_only=True, depth=2))
        entity_names = {e.name for e in result.entities}
        # All entities reachable within 2 hops from roots
        assert entity_names == {"Product", "AuthService", "Library", "Algorithm", "Team"}

    def test_negative_depth_rejected(self) -> None:
        """L-1: Negative depth values are rejected by validation."""
        import pytest
        with pytest.raises(Exception):  # noqa: B017
            GetGraphQuery(depth=-1)


# ===========================================================================
# Task 5 — Path traversal tests (Story 3.1, AC-1)
# ===========================================================================


def _seed_path_graph(actor: KnowledgeGraphActor) -> None:
    """Populate a linear + branching test graph for path traversal tests.

    Graph topology:
        Device --HAS_COMPONENT--> ComponentA
        ComponentA --DEPENDS_ON--> ServiceB
        ServiceB --DEPENDS_ON--> DatabaseC
        Device --ALSO_HAS--> ComponentX  (side branch)

    5 entities, 4 relations.
    """
    actor.update_graph(
        ManageGraph(
            create_entities=[
                EntityCreate(name="Device", entity_type="Hardware", description="Main device"),
                EntityCreate(name="ComponentA", entity_type="Component", description="Primary component"),
                EntityCreate(name="ServiceB", entity_type="Service", description="Backend service"),
                EntityCreate(name="DatabaseC", entity_type="Database", description="Persistent store"),
                EntityCreate(name="ComponentX", entity_type="Component", description="Secondary component"),
            ],
            create_relations=[
                RelationCreate(from_entity="Device", to_entity="ComponentA", relation_type="HAS_COMPONENT"),
                RelationCreate(from_entity="ComponentA", to_entity="ServiceB", relation_type="DEPENDS_ON"),
                RelationCreate(from_entity="ServiceB", to_entity="DatabaseC", relation_type="DEPENDS_ON"),
                RelationCreate(from_entity="Device", to_entity="ComponentX", relation_type="ALSO_HAS"),
            ],
        )
    )


class TestDirectedPathTraversal:
    """AC-1: Directed path traversal via PathStep waypoints."""

    def test_single_waypoint_depth_2(self) -> None:
        """Device → HAS_COMPONENT → ComponentA, then depth=2 expands from ComponentA."""
        actor = _actor()
        _seed_path_graph(actor)
        result = actor.get_graph(
            GetGraphQuery(
                entity_names=["Device"],
                path=[PathStep(relation_type="HAS_COMPONENT", to_entity="ComponentA")],
                depth=2,
            )
        )
        entity_names = {e.name for e in result.entities}
        # Device and ComponentA collected during waypoint walk
        assert "Device" in entity_names
        assert "ComponentA" in entity_names
        # Depth=2 from ComponentA: ServiceB (1 hop), DatabaseC (2 hops)
        assert "ServiceB" in entity_names
        assert "DatabaseC" in entity_names
        # ComponentX is a sibling of ComponentA (off Device), NOT from ComponentA
        assert "ComponentX" not in entity_names

    def test_missing_waypoint_returns_empty(self) -> None:
        """Path traversal with wrong relation type returns empty GraphView."""
        actor = _actor()
        _seed_path_graph(actor)
        result = actor.get_graph(
            GetGraphQuery(
                entity_names=["Device"],
                path=[PathStep(relation_type="NONEXISTENT_REL", to_entity="ComponentA")],
                depth=1,
            )
        )
        assert result.entities == []
        assert result.relations == []

    def test_missing_target_entity_returns_empty(self) -> None:
        """Path traversal targeting a non-existent entity returns empty."""
        actor = _actor()
        _seed_path_graph(actor)
        result = actor.get_graph(
            GetGraphQuery(
                entity_names=["Device"],
                path=[PathStep(relation_type="HAS_COMPONENT", to_entity="NoSuchEntity")],
                depth=1,
            )
        )
        assert result.entities == []
        assert result.relations == []

    def test_empty_entity_names_returns_empty(self) -> None:
        """Path traversal with empty entity_names returns empty GraphView."""
        actor = _actor()
        _seed_path_graph(actor)
        result = actor.get_graph(
            GetGraphQuery(
                entity_names=[],
                path=[PathStep(relation_type="HAS_COMPONENT", to_entity="ComponentA")],
                depth=1,
            )
        )
        assert result.entities == []
        assert result.relations == []

    def test_depth_0_returns_only_terminal_entity(self) -> None:
        """Path traversal with depth=0 returns only the terminal entity (no BFS expansion)."""
        actor = _actor()
        _seed_path_graph(actor)
        result = actor.get_graph(
            GetGraphQuery(
                entity_names=["Device"],
                path=[PathStep(relation_type="HAS_COMPONENT", to_entity="ComponentA")],
                depth=0,
            )
        )
        entity_names = {e.name for e in result.entities}
        # Terminal is ComponentA; with depth=0 no expansion, but path entities are collected
        assert "ComponentA" in entity_names
        assert "ServiceB" not in entity_names

    def test_relation_types_filter_applies_during_expansion(self) -> None:
        """relation_types filter applies during BFS expansion phase, not waypoint phase."""
        actor = _actor()
        _seed_path_graph(actor)
        result = actor.get_graph(
            GetGraphQuery(
                entity_names=["Device"],
                path=[PathStep(relation_type="HAS_COMPONENT", to_entity="ComponentA")],
                depth=2,
                relation_types=["DEPENDS_ON"],  # filter during expansion
            )
        )
        entity_names = {e.name for e in result.entities}
        # ComponentA → ServiceB (DEPENDS_ON) → DatabaseC (DEPENDS_ON)
        assert "ServiceB" in entity_names
        assert "DatabaseC" in entity_names

    def test_multi_step_path_traversal(self) -> None:
        """AC-1: Multi-step path (2 waypoints) navigates correctly through the graph."""
        actor = _actor()
        _seed_path_graph(actor)
        # Device → HAS_COMPONENT → ComponentA → DEPENDS_ON → ServiceB, then depth=1
        result = actor.get_graph(
            GetGraphQuery(
                entity_names=["Device"],
                path=[
                    PathStep(relation_type="HAS_COMPONENT", to_entity="ComponentA"),
                    PathStep(relation_type="DEPENDS_ON", to_entity="ServiceB"),
                ],
                depth=1,
            )
        )
        entity_names = {e.name for e in result.entities}
        # All waypoint entities present
        assert "Device" in entity_names
        assert "ComponentA" in entity_names
        assert "ServiceB" in entity_names
        # depth=1 from ServiceB includes DatabaseC
        assert "DatabaseC" in entity_names
        # ComponentX is off Device (sibling branch), not reachable from ServiceB
        assert "ComponentX" not in entity_names
