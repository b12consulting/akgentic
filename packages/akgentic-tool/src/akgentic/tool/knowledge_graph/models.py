"""Data models for the Knowledge Graph subsystem.

Defines entity, relation, CRUD request models, query/search models,
and state containers for the KnowledgeGraphActor. All models extend
SerializableBaseModel for proper actor message serialization.

Source: V2 redesign of akgentic-framework/libs/tools/tools/knowledge_graph.py
"""

from __future__ import annotations

import uuid
from typing import Literal

from pydantic import Field

from akgentic.core.agent_state import BaseState
from akgentic.core.utils.serializer import SerializableBaseModel

# ---------------------------------------------------------------------------
# Core domain models
# ---------------------------------------------------------------------------


class Entity(SerializableBaseModel):
    """An entity node in the knowledge graph.

    Attributes:
        id: Auto-generated UUID for stable vector index referencing.
        name: Human-facing unique key (deduplication key).
        entity_type: Classification label (e.g. "Person", "Concept").
        description: Free-text or Markdown description.
        observations: Incremental observations attached to the entity.
    """

    id: uuid.UUID = Field(default_factory=uuid.uuid4, description="Stable unique identifier.")
    name: str = Field(..., description="Human-facing unique name (deduplication key).")
    entity_type: str = Field(..., description="Classification label for the entity.")
    description: str = Field(..., description="Free-text or Markdown description.")
    observations: list[str] = Field(
        default_factory=list, description="Incremental observations attached to the entity."
    )
    is_root: bool = Field(
        default=False,
        description="Marks entry-point/anchor entities for system prompt and roots_only queries.",
    )


class Relation(SerializableBaseModel):
    """A directed edge between two entities in the knowledge graph.

    Attributes:
        id: Auto-generated UUID for stable vector index referencing.
        from_entity: Source entity name.
        to_entity: Target entity name.
        relation_type: Label describing the relationship.
        description: Optional free-text description (enables embedding in Epic 2).
    """

    id: uuid.UUID = Field(default_factory=uuid.uuid4, description="Stable unique identifier.")
    from_entity: str = Field(..., description="Source entity name.")
    to_entity: str = Field(..., description="Target entity name.")
    relation_type: str = Field(..., description="Label describing the relationship.")
    description: str = Field(default="", description="Optional description of the relation.")


# ---------------------------------------------------------------------------
# CRUD request models
# ---------------------------------------------------------------------------


class EntityCreate(SerializableBaseModel):
    """Request to create a new entity.

    Mirrors Entity fields minus ``id`` (auto-generated on creation).
    """

    name: str = Field(..., description="Unique entity name.")
    entity_type: str = Field(..., description="Classification label.")
    description: str = Field(..., description="Free-text description.")
    observations: list[str] = Field(default_factory=list, description="Initial observations.")
    is_root: bool = Field(default=False, description="Mark as root/entry-point entity.")


class EntityUpdate(SerializableBaseModel):
    """Partial update for an existing entity.

    Only ``name`` is required (lookup key). All other fields are optional;
    only non-None values are applied.
    """

    name: str = Field(..., description="Entity name to update (lookup key).")
    description: str | None = Field(default=None, description="New description.")
    entity_type: str | None = Field(default=None, description="New entity type.")
    add_observations: list[str] | None = Field(default=None, description="Observations to append.")
    remove_observations: list[str] | None = Field(
        default=None, description="Observations to remove."
    )
    is_root: bool | None = Field(default=None, description="Toggle root status.")


class RelationCreate(SerializableBaseModel):
    """Request to create a new relation."""

    from_entity: str = Field(..., description="Source entity name.")
    to_entity: str = Field(..., description="Target entity name.")
    relation_type: str = Field(..., description="Relation label.")
    description: str = Field(default="", description="Optional relation description.")


class RelationDelete(SerializableBaseModel):
    """Request to delete a relation by its unique triple."""

    from_entity: str = Field(..., description="Source entity name.")
    to_entity: str = Field(..., description="Target entity name.")
    relation_type: str = Field(..., description="Relation label.")


# ---------------------------------------------------------------------------
# Batch mutation model
# ---------------------------------------------------------------------------


class ManageGraph(SerializableBaseModel):
    """Batch mutation request processed by ``KnowledgeGraphActor.update_graph``.

    Operation order: create_entities → create_relations → update_entities
    → delete_relations → delete_entities.
    """

    create_entities: list[EntityCreate] = Field(
        default_factory=list, description="Entities to add."
    )
    update_entities: list[EntityUpdate] = Field(
        default_factory=list, description="Partial entity updates."
    )
    delete_entities: list[str] = Field(
        default_factory=list, description="Entity names to remove (cascade deletes relations)."
    )
    create_relations: list[RelationCreate] = Field(
        default_factory=list, description="Relations to add."
    )
    delete_relations: list[RelationDelete] = Field(
        default_factory=list, description="Relations to remove by triple."
    )


# ---------------------------------------------------------------------------
# Container & state models
# ---------------------------------------------------------------------------


class KnowledgeGraph(SerializableBaseModel):
    """In-memory container for the full knowledge graph."""

    entities: list[Entity] = Field(default_factory=list, description="All entities.")
    relations: list[Relation] = Field(default_factory=list, description="All relations.")


class KnowledgeGraphState(BaseState):
    """Actor state wrapping a ``KnowledgeGraph`` instance.

    Inherits observer pattern from ``BaseState`` for automatic
    ``StateChangedMessage`` propagation to the orchestrator.
    """

    knowledge_graph: KnowledgeGraph = Field(default_factory=KnowledgeGraph)


# ---------------------------------------------------------------------------
# Query & search models (Story 1.2)
# ---------------------------------------------------------------------------


class PathStep(SerializableBaseModel):
    """One directed hop in a path traversal query.

    Used in ``GetGraphQuery.path`` to specify directed graph navigation
    waypoints. Each step identifies the relation type to follow and the
    target entity name to navigate to.
    """

    relation_type: str = Field(..., description="Relation type to follow.")
    to_entity: str = Field(..., description="Target entity name to navigate to.")


class GetGraphQuery(SerializableBaseModel):
    """Parameters for subgraph retrieval via ``KnowledgeGraphActor.get_graph``.

    When ``entity_names`` is empty and ``roots_only`` is False the full
    graph is returned.  ``depth`` controls BFS expansion hops from root
    entities.  ``relation_types`` limits which edges are traversed.

    When ``roots_only`` is True, only entities with ``is_root=True`` are
    used as BFS seeds (``entity_names`` is ignored).  ``depth`` defaults
    to None which means: 0 for roots_only mode (no expansion), 1 for
    entity_names mode (1-hop expansion).  Explicit depth always wins.

    When ``path`` is non-empty, directed traversal is performed: the actor
    navigates from ``entity_names[0]`` through each ``PathStep`` waypoint,
    then applies BFS with ``depth`` hops from the terminal entity.  Path
    traversal takes precedence over ``roots_only`` and BFS modes.
    """

    entity_names: list[str] = Field(
        default_factory=list,
        description="Root entity names to start subgraph from (empty = full graph).",
    )
    relation_types: list[str] = Field(
        default_factory=list,
        description="Only traverse these relation types during BFS (empty = all).",
    )
    depth: int | None = Field(
        default=None,
        ge=0,
        description="Max BFS hops. None = context default (0 for roots_only, 1 for entity_names).",
    )
    roots_only: bool = Field(
        default=False,
        description="Return only is_root=True entities + inter-root relations.",
    )
    path: list[PathStep] = Field(
        default_factory=list,
        description=(
            "Directed traversal waypoints. When non-empty, used instead of BFS from entity_names."
        ),
    )


class GraphView(SerializableBaseModel):
    """Read-only snapshot of a (sub)graph returned by ``get_graph``.

    Attributes:
        entities: Entities in the view.
        relations: Relations connecting entities in the view.
    """

    entities: list[Entity] = Field(default_factory=list, description="Entities in the view.")
    relations: list[Relation] = Field(default_factory=list, description="Relations in the view.")


class SearchQuery(SerializableBaseModel):
    """Parameters for ``KnowledgeGraphActor.search``.

    Epic 3 expansion options (all default False for zero behavior change):
    ``include_neighbors`` adds 1-hop neighbors of entity hits to the result.
    ``include_edges`` adds all relations connected to entity hits.
    ``find_paths`` computes BFS shortest paths between the top 5 entity hits
    (max 10 pairs).
    """

    query: str = Field(..., description="Search query string.")
    top_k: int | None = Field(
        default=None,
        description="Max hits. Overrides ToolCard default (10) when set.",
    )
    mode: Literal["hybrid", "vector", "keyword"] = Field(
        default="hybrid",
        description="hybrid (default) = keyword + vector, "
        "keyword = substring only, vector = semantic only.",
    )
    include_neighbors: bool = Field(
        default=False, description="Add 1-hop neighbors of entity hits."
    )
    include_edges: bool = Field(
        default=False, description="Add relations connected to entity hits."
    )
    find_paths: bool = Field(
        default=False,
        description="BFS shortest paths between top 5 entity hits (max 10 pairs).",
    )
    score_threshold: float | None = Field(
        default=None,
        description="Min cosine similarity. Overrides ToolCard default (0.3) when set.",
    )


class SearchHit(SerializableBaseModel):
    """A single search result entry.

    Attributes:
        ref_type: Whether the hit is an entity or relation.
        ref_id: UUID string of the matched entity/relation.
        score: Match score (1.0 for keyword binary match).
        entity: Populated when ref_type is "entity".
        relation: Populated when ref_type is "relation".
    """

    ref_type: Literal["entity", "relation"] = Field(
        ..., description="Type of the referenced object."
    )
    ref_id: str = Field(..., description="UUID string of the matched object.")
    score: float = Field(..., description="Match score.")
    entity: Entity | None = Field(default=None, description="Matched entity (if ref_type=entity).")
    relation: Relation | None = Field(
        default=None, description="Matched relation (if ref_type=relation)."
    )


class SearchResult(SerializableBaseModel):
    """Container for search results.

    Epic 3 expansion fields (all default to empty for zero behavior change):
    ``neighbors`` contains 1-hop neighbors of found entities (when
    ``include_neighbors=True`` in the query).  ``connected_relations``
    contains all relations connected to found entities (when
    ``include_edges=True``).  ``paths`` contains BFS shortest-path sequences
    between top entity hits (when ``find_paths=True``), each as an alternating
    ``[Entity, Relation, Entity, ...]`` list.
    """

    hits: list[SearchHit] = Field(default_factory=list, description="Ranked search hits.")
    neighbors: list[Entity] = Field(
        default_factory=list,
        description="1-hop neighbors of found entities (when include_neighbors=True).",
    )
    connected_relations: list[Relation] = Field(
        default_factory=list,
        description="All relations connected to found entities (when include_edges=True).",
    )
    paths: list[list[Entity | Relation]] = Field(
        default_factory=list,
        description="Shortest BFS paths between top entity hits (when find_paths=True).",
    )


# ---------------------------------------------------------------------------
# State delta models (Story 17.1, ADR-024)
# ---------------------------------------------------------------------------


class KnowledgeGraphStateEvent(SerializableBaseModel):
    """Delta payload describing changes to the knowledge graph (Story 17.1, ADR-024).

    Carried inside a ``ToolStateEvent`` envelope and broadcast on the orchestrator
    event stream by the ``KnowledgeGraphActor`` (emission lands in Story 17.2).
    All collections default to empty so that a single mutation category can be
    reported without populating the others.

    Attributes:
        entities_added: Entities newly created in this delta.
        entities_modified: Entities whose fields were updated.
        entities_removed: UUIDs of entities removed in this delta.
        relations_added: Relations newly created in this delta.
        relations_modified: Relations whose fields were updated.
        relations_removed: UUIDs of relations removed in this delta.
    """

    entities_added: list[Entity] = Field(
        default_factory=list, description="Entities newly created in this delta."
    )
    entities_modified: list[Entity] = Field(
        default_factory=list, description="Entities whose fields were updated."
    )
    entities_removed: list[uuid.UUID] = Field(
        default_factory=list, description="UUIDs of entities removed in this delta."
    )
    relations_added: list[Relation] = Field(
        default_factory=list, description="Relations newly created in this delta."
    )
    relations_modified: list[Relation] = Field(
        default_factory=list, description="Relations whose fields were updated."
    )
    relations_removed: list[uuid.UUID] = Field(
        default_factory=list, description="UUIDs of relations removed in this delta."
    )


# ---------------------------------------------------------------------------
# Resolve the forward-referenced ``ToolStatePayload`` alias on ``ToolStateEvent``
# now that ``KnowledgeGraphStateEvent`` is defined. ``event.py`` cannot import
# this module directly without creating a cycle, so the rebuild lives here
# (Story 17.1, ADR-024).
# ---------------------------------------------------------------------------
from akgentic.tool.event import ToolStateEvent as _ToolStateEvent  # noqa: E402

_ToolStateEvent.model_rebuild(
    _types_namespace={"KnowledgeGraphStateEvent": KnowledgeGraphStateEvent}
)
