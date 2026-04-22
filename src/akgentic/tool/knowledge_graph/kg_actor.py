"""KnowledgeGraph actor with batch CRUD operations.

Mirrors the PlanActor pattern: Akgent subclass with typed state,
error-collection semantics, and ``BaseState.notify_state_change()``
for orchestrator integration.

Source: V2 redesign of akgentic-framework KnowledgeGraphManager.
"""

from __future__ import annotations

import logging
import uuid
from collections import deque

from pydantic import Field

from akgentic.core.agent import Akgent
from akgentic.core.agent_config import BaseConfig
from akgentic.core.orchestrator import Orchestrator
from akgentic.tool.errors import RetriableError
from akgentic.tool.event import ToolStateEvent
from akgentic.tool.knowledge_graph.models import (
    Entity,
    EntityCreate,
    EntityUpdate,
    GetGraphQuery,
    GraphView,
    KnowledgeGraphState,
    KnowledgeGraphStateEvent,
    ManageGraph,
    PathStep,
    Relation,
    RelationCreate,
    RelationDelete,
    SearchHit,
    SearchQuery,
    SearchResult,
)
from akgentic.tool.vector import VectorEntry
from akgentic.tool.vector_store.actor import VS_ACTOR_NAME, VectorStoreActor
from akgentic.tool.vector_store.protocol import CollectionConfig
from akgentic.tool.vector_store.protocol import SearchResult as VsSearchResult

logger = logging.getLogger(__name__)

KG_ACTOR_NAME: str = "#KnowledgeGraphTool"
"""Singleton actor name registered with the orchestrator."""

KG_ACTOR_ROLE: str = "ToolActor"
"""Actor role constant for ToolCard integration."""

KG_COLLECTION: str = "knowledge_graph"
"""Collection name used in VectorStoreActor."""


class KnowledgeGraphConfig(BaseConfig):
    """Configuration for ``KnowledgeGraphActor``.

    Carries the actor-level binding to a ``VectorStoreActor`` so the
    knowledge graph can resolve its vector store by name (or operate in
    degraded mode without one). The actor itself is created by
    ``VectorStoreTool``; this config only points at it.
    """

    vector_store: bool | str = Field(
        default=True,
        description=(
            "Binding to a VectorStoreActor: True=default #VectorStore, "
            "str=named instance, False=degraded mode (no vector search)."
        ),
    )
    collection: CollectionConfig = Field(
        default_factory=CollectionConfig,
        description=(
            "Vector collection configuration forwarded to "
            "VectorStoreActor.create_collection."
        ),
    )
    search_top_k: int = Field(
        default=10,
        description="Default maximum number of search hits to return.",
    )
    search_score_threshold: float = Field(
        default=0.3,
        description=(
            "Default minimum cosine similarity score for vector/hybrid search. "
            "Hits below this threshold are filtered out."
        ),
    )


class KnowledgeGraphActor(Akgent[KnowledgeGraphConfig, KnowledgeGraphState]):
    """Centralized, thread-safe knowledge graph with batch CRUD.

    All mutations flow through ``update_graph(ManageGraph)`` which
    executes operations in a deterministic order, collects all errors,
    notifies the orchestrator via ``state.notify_state_change()``, and
    raises a single ``RetriableError`` when any errors occurred.

    Operation order:
        create_entities → create_relations → update_entities
        → delete_relations → delete_entities
    """

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def on_start(self) -> None:  # noqa: ANN201
        """Initialize state, attach observer, and acquire VectorStoreActor proxy."""
        self.state = KnowledgeGraphState()
        self.state.observer(self)
        # Coerce BaseConfig → KnowledgeGraphConfig when the actor was started with
        # a plain BaseConfig (e.g., test harnesses that construct the actor
        # directly). This preserves backward compatibility while giving the actor
        # typed access to the vector_store field introduced in story 10-9.
        if not isinstance(self.config, KnowledgeGraphConfig):
            self.config = KnowledgeGraphConfig(
                name=self.config.name,
                role=self.config.role,
            )
        self._vs_proxy: VectorStoreActor | None = None
        self._acquire_vs_proxy()
        self._state_event_seq: int = 0

    def _acquire_vs_proxy(self) -> None:
        """Look up the VectorStoreActor proxy and create the KG collection.

        The VectorStoreActor is owned by ``VectorStoreTool``; this actor
        only resolves it by name. Behaviour:

        - ``config.vector_store is False`` → stay in degraded mode (no
          lookup, ``_vs_proxy`` remains ``None``).
        - ``self.orchestrator is None`` (test harness) → log WARNING and
          return — existing behaviour preserved.
        - Otherwise look up the target actor name (``config.vector_store``
          when a ``str``, else ``VS_ACTOR_NAME``) via
          ``orch_proxy.get_team_member``. Raise ``RuntimeError`` when it
          is missing — a missing VectorStoreTool is a **configuration**
          error, not a runtime degradation.
        - A transient backend error during ``create_collection`` drops
          back to degraded mode with a WARNING (matches existing
          behaviour for embedding failures).
        """
        if self.config.vector_store is False:
            return  # degraded mode by design

        if self.orchestrator is None:
            logger.warning(
                "[%s] No orchestrator; operating in degraded mode",
                self.config.name,
            )
            return

        vs_name = (
            self.config.vector_store
            if isinstance(self.config.vector_store, str)
            else VS_ACTOR_NAME
        )
        orch_proxy = self.proxy_ask(self.orchestrator, Orchestrator)
        vs_addr = orch_proxy.get_team_member(vs_name)
        if vs_addr is None:
            raise RuntimeError(
                f"{self.config.name} requires VectorStoreActor '{vs_name}' "
                f"but it was not found. Ensure VectorStoreTool is in the team config."
            )
        self._vs_proxy = self.proxy_ask(vs_addr, VectorStoreActor)
        try:
            self._vs_proxy.create_collection(KG_COLLECTION, self.config.collection)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "[%s] create_collection on VectorStoreActor failed: %s — degraded mode",
                self.config.name,
                exc,
            )
            self._vs_proxy = None

    # ------------------------------------------------------------------
    # Embedding helpers (via VectorStoreActor proxy)
    # ------------------------------------------------------------------

    def _embed_entity(self, entity: Entity) -> None:
        """Embed an entity and store the result via VectorStoreActor proxy.

        Silently logs a WARNING and returns if the proxy is unavailable
        or if the embedding call fails.
        """
        if self._vs_proxy is None:
            return
        try:
            text = f"{entity.name}: {entity.description}"
            vectors = self._vs_proxy.embed([text])
            if not vectors:
                return
            self._vs_proxy.add(
                KG_COLLECTION,
                [
                    VectorEntry(
                        ref_type="entity",
                        ref_id=str(entity.id),
                        text=text,
                        vector=vectors[0],
                    )
                ],
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "[%s] Failed to embed entity '%s': %s", self.config.name, entity.name, exc
            )

    def _embed_relation(self, relation: Relation) -> None:
        """Embed a relation if it has a non-empty description.

        Relations without descriptions are not embedded (no meaningful text).
        Silently logs WARNING on embedding failure.
        """
        if not relation.description:
            return
        if self._vs_proxy is None:
            return
        try:
            text = (
                f"{relation.from_entity} {relation.relation_type} "
                f"{relation.to_entity}: {relation.description}"
            )
            vectors = self._vs_proxy.embed([text])
            if not vectors:
                return
            self._vs_proxy.add(
                KG_COLLECTION,
                [
                    VectorEntry(
                        ref_type="relation",
                        ref_id=str(relation.id),
                        text=text,
                        vector=vectors[0],
                    )
                ],
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "[%s] Failed to embed relation '%s->%s': %s",
                self.config.name,
                relation.from_entity,
                relation.to_entity,
                exc,
            )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @property
    def _graph(self) -> KnowledgeGraphState:
        """Shortcut to the knowledge graph container inside state."""
        return self.state

    def _entity_map(self) -> dict[str, Entity]:
        """Build a name → Entity lookup from current graph."""
        return {e.name: e for e in self.state.knowledge_graph.entities}

    # ------------------------------------------------------------------
    # CRUD: Create
    # ------------------------------------------------------------------

    def _create_entities(self, entities: list[EntityCreate]) -> tuple[list[Entity], list[str]]:
        """Add new entities, skipping duplicates by name.

        Returns:
            Tuple of (applied entities, error strings for duplicate names).
        """
        errors: list[str] = []
        applied: list[Entity] = []
        existing_names = {e.name for e in self.state.knowledge_graph.entities}

        for ec in entities:
            if ec.name in existing_names:
                errors.append(f"Entity '{ec.name}' already exists")
                continue
            entity = Entity(
                id=uuid.uuid4(),
                name=ec.name,
                entity_type=ec.entity_type,
                description=ec.description,
                observations=list(ec.observations),
                is_root=ec.is_root,
            )
            self.state.knowledge_graph.entities.append(entity)
            existing_names.add(ec.name)
            self._embed_entity(entity)
            applied.append(entity)
        return applied, errors

    def _create_relations(
        self, relations: list[RelationCreate]
    ) -> tuple[list[Relation], list[str]]:
        """Add new relations, validating entity references and deduplicating.

        Returns:
            Tuple of (applied relations, error strings for missing refs).
            Duplicate triples are silently skipped and NOT included in applied.
        """
        errors: list[str] = []
        applied: list[Relation] = []
        existing_names = {e.name for e in self.state.knowledge_graph.entities}
        existing_triples = {
            (r.from_entity, r.to_entity, r.relation_type)
            for r in self.state.knowledge_graph.relations
        }

        for rc in relations:
            if rc.from_entity not in existing_names:
                errors.append(f"Relation references non-existent from_entity '{rc.from_entity}'")
                continue
            if rc.to_entity not in existing_names:
                errors.append(f"Relation references non-existent to_entity '{rc.to_entity}'")
                continue

            triple = (rc.from_entity, rc.to_entity, rc.relation_type)
            if triple in existing_triples:
                # Silently skip duplicate relations (same as V1)
                continue

            relation = Relation(
                id=uuid.uuid4(),
                from_entity=rc.from_entity,
                to_entity=rc.to_entity,
                relation_type=rc.relation_type,
                description=rc.description,
            )
            self.state.knowledge_graph.relations.append(relation)
            existing_triples.add(triple)
            self._embed_relation(relation)
            applied.append(relation)
        return applied, errors

    # ------------------------------------------------------------------
    # CRUD: Update
    # ------------------------------------------------------------------

    def _update_entities(self, updates: list[EntityUpdate]) -> tuple[list[Entity], list[str]]:
        """Apply partial updates to existing entities.

        Only non-None fields are applied. ``add_observations`` appends,
        ``remove_observations`` removes from the existing list. An entity
        appears in the applied list only if at least one mutation
        effectively changed it.

        Returns:
            Tuple of (post-update snapshots of changed entities, errors).
        """
        errors: list[str] = []
        applied: list[Entity] = []
        entity_map = self._entity_map()

        for eu in updates:
            entity = entity_map.get(eu.name)
            if entity is None:
                errors.append(f"Entity '{eu.name}' not found for update")
                continue

            changed: bool = False

            if eu.description is not None and eu.description != entity.description:
                if self._vs_proxy is not None:
                    self._vs_proxy.remove(KG_COLLECTION, [str(entity.id)])
                entity.description = eu.description
                self._embed_entity(entity)
                changed = True
            elif eu.description is not None:
                # Same value -> no-op
                entity.description = eu.description
            if eu.entity_type is not None and eu.entity_type != entity.entity_type:
                entity.entity_type = eu.entity_type
                changed = True
            if eu.is_root is not None and eu.is_root != entity.is_root:
                entity.is_root = eu.is_root
                changed = True
            if eu.add_observations:
                entity.observations.extend(eu.add_observations)
                changed = True
            if eu.remove_observations is not None:
                to_remove = set(eu.remove_observations)
                before = entity.observations
                after = [o for o in before if o not in to_remove]
                if len(after) != len(before):
                    changed = True
                entity.observations = after

            if changed:
                applied.append(entity)
        return applied, errors

    # ------------------------------------------------------------------
    # CRUD: Delete
    # ------------------------------------------------------------------

    def _delete_entities(
        self, names: list[str]
    ) -> tuple[list[uuid.UUID], list[uuid.UUID], list[str]]:
        """Remove entities by name with cascade deletion of relations.

        Also removes any associated VectorEntry embeddings from VectorStoreActor.

        Returns:
            Tuple of (deleted entity ids, cascaded relation ids, errors).
        """
        errors: list[str] = []
        existing_names = {e.name for e in self.state.knowledge_graph.entities}

        for name in names:
            if name not in existing_names:
                errors.append(f"Entity '{name}' not found for deletion")

        names_set = set(names) & existing_names

        if not names_set:
            return [], [], errors

        # Collect UUIDs of deleted entities for embedding removal
        deleted_entity_ids: list[uuid.UUID] = [
            e.id for e in self.state.knowledge_graph.entities if e.name in names_set
        ]

        # Collect UUIDs of cascade-deleted relations
        cascaded_rel_ids: list[uuid.UUID] = [
            r.id
            for r in self.state.knowledge_graph.relations
            if r.from_entity in names_set or r.to_entity in names_set
        ]

        # Remove entities
        self.state.knowledge_graph.entities = [
            e for e in self.state.knowledge_graph.entities if e.name not in names_set
        ]

        # Cascade-delete relations referencing deleted entities
        self.state.knowledge_graph.relations = [
            r
            for r in self.state.knowledge_graph.relations
            if r.from_entity not in names_set and r.to_entity not in names_set
        ]

        if self._vs_proxy is not None:
            ref_ids = [str(eid) for eid in deleted_entity_ids] + [
                str(rid) for rid in cascaded_rel_ids
            ]
            self._vs_proxy.remove(KG_COLLECTION, ref_ids)

        return deleted_entity_ids, cascaded_rel_ids, errors

    def _delete_relations(
        self, deletions: list[RelationDelete]
    ) -> tuple[list[uuid.UUID], list[str]]:
        """Remove relations by ``(from_entity, to_entity, relation_type)`` triple.

        Also removes any associated VectorEntry embeddings from VectorStoreActor.

        Returns:
            Tuple of (ids of relations actually removed, errors).
        """
        errors: list[str] = []
        existing_triples = {
            (r.from_entity, r.to_entity, r.relation_type): r
            for r in self.state.knowledge_graph.relations
        }

        triples_to_delete: set[tuple[str, str, str]] = set()
        for rd in deletions:
            triple = (rd.from_entity, rd.to_entity, rd.relation_type)
            if triple not in existing_triples:
                errors.append(
                    f"Relation ({rd.from_entity} -> {rd.to_entity} [{rd.relation_type}]) not found"
                )
            else:
                triples_to_delete.add(triple)

        deleted_rel_ids: list[uuid.UUID] = []
        if triples_to_delete:
            deleted_rel_ids = [
                r.id
                for r in self.state.knowledge_graph.relations
                if (r.from_entity, r.to_entity, r.relation_type) in triples_to_delete
            ]

            self.state.knowledge_graph.relations = [
                r
                for r in self.state.knowledge_graph.relations
                if (r.from_entity, r.to_entity, r.relation_type) not in triples_to_delete
            ]

            if self._vs_proxy is not None:
                self._vs_proxy.remove(KG_COLLECTION, [str(rid) for rid in deleted_rel_ids])

        return deleted_rel_ids, errors

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def update_graph(self, update: ManageGraph) -> str:
        """Execute batch mutations in deterministic order.

        Order: create_entities → create_relations → update_entities
        → delete_relations → delete_entities.

        All errors are collected across all operations. After mutations,
        ``state.notify_state_change()`` is called regardless of errors.
        A single ``RetriableError`` is raised if any errors occurred.

        Args:
            update: Batch mutation request.

        Returns:
            ``"Done"`` on success.

        Raises:
            RetriableError: With all collected error messages joined by "; ".
        """
        errors: list[str] = []

        created_entities, errs = self._create_entities(update.create_entities)
        errors.extend(errs)

        created_relations, errs = self._create_relations(update.create_relations)
        errors.extend(errs)

        modified_entities, errs = self._update_entities(update.update_entities)
        errors.extend(errs)

        deleted_relation_ids, errs = self._delete_relations(update.delete_relations)
        errors.extend(errs)

        deleted_entity_ids, cascaded_relation_ids, errs = self._delete_entities(
            update.delete_entities
        )
        errors.extend(errs)

        # Merge explicit + cascaded relation deletions, preserving order and deduping.
        merged_relations_removed: list[uuid.UUID] = list(deleted_relation_ids)
        seen_rel_ids: set[uuid.UUID] = set(deleted_relation_ids)
        for rid in cascaded_relation_ids:
            if rid not in seen_rel_ids:
                merged_relations_removed.append(rid)
                seen_rel_ids.add(rid)

        delta = KnowledgeGraphStateEvent(
            entities_added=created_entities,
            entities_modified=modified_entities,
            entities_removed=deleted_entity_ids,
            relations_added=created_relations,
            relations_removed=merged_relations_removed,
        )

        self.state.notify_state_change()

        if self._delta_is_non_empty(delta):
            self._state_event_seq += 1
            self.notify_event(
                ToolStateEvent(
                    tool_id=KG_ACTOR_NAME,
                    seq=self._state_event_seq,
                    payload=delta,
                )
            )

        if errors:
            raise RetriableError("Update errors: " + "; ".join(errors))
        return "Done"

    @staticmethod
    def _delta_is_non_empty(delta: KnowledgeGraphStateEvent) -> bool:
        """Return ``True`` iff any collection on the delta is non-empty."""
        return bool(
            delta.entities_added
            or delta.entities_modified
            or delta.entities_removed
            or delta.relations_added
            or delta.relations_modified
            or delta.relations_removed
        )

    def get_graph(self, query: GetGraphQuery | None = None) -> GraphView:
        """Return a full or filtered subgraph view.

        When ``query`` is None or has empty ``entity_names`` (and
        ``roots_only`` is False), the full graph is returned.  Otherwise
        BFS expansion is performed from the seed entities up to
        ``depth`` hops, optionally filtered by ``relation_types``.

        When ``roots_only`` is True, entities with ``is_root=True`` are
        used as BFS seeds and ``entity_names`` is ignored.  Depth
        defaults to 0 (no expansion) for ``roots_only`` mode.

        When ``path`` is non-empty, directed path traversal is performed
        (takes highest precedence): the actor navigates from
        ``entity_names[0]`` through each ``PathStep`` waypoint, then
        applies BFS with ``depth`` hops from the terminal entity.

        Args:
            query: Subgraph query parameters.  Defaults to full graph.

        Returns:
            GraphView with discovered entities and connecting relations.
        """
        if query is None:
            query = GetGraphQuery()

        # Directed path traversal takes precedence over all other modes (AC-1)
        if query.path:
            if not query.entity_names:
                return GraphView()
            start_entity = query.entity_names[0]
            effective_depth = query.depth if query.depth is not None else 1
            return self._get_path_subgraph(
                start_entity=start_entity,
                path=query.path,
                depth=effective_depth,
                relation_types=query.relation_types,
            )

        # roots_only takes precedence over entity_names (AC-6)
        if query.roots_only:
            root_names = [e.name for e in self.state.knowledge_graph.entities if e.is_root]
            if not root_names:
                return GraphView()
            # depth=None → 0 for roots_only (AC-4); explicit depth used as-is (AC-5)
            effective_depth = query.depth if query.depth is not None else 0
            return self._get_subgraph(
                entity_names=root_names,
                depth=effective_depth,
                relation_types=query.relation_types,
            )

        if not query.entity_names:
            return GraphView(
                entities=list(self.state.knowledge_graph.entities),
                relations=list(self.state.knowledge_graph.relations),
            )

        # depth=None → 1 for entity_names mode (backward compat)
        effective_depth = query.depth if query.depth is not None else 1
        return self._get_subgraph(
            entity_names=query.entity_names,
            depth=effective_depth,
            relation_types=query.relation_types,
        )

    # ------------------------------------------------------------------
    # Subgraph BFS
    # ------------------------------------------------------------------

    def _get_subgraph(
        self,
        entity_names: list[str],
        depth: int,
        relation_types: list[str],
        pre_visited: set[str] | None = None,
    ) -> GraphView:
        """BFS expansion from root entities.

        At each hop, relations connecting a frontier entity to an
        unvisited entity are traversed.  ``relation_types`` limits which
        edge types are followed.  After expansion completes, only
        relations whose **both** endpoints are in the visited set are
        included in the result.

        ``pre_visited`` optionally seeds the visited set before BFS starts,
        preventing backtracking into already-collected path entities.
        """
        graph = self.state.knowledge_graph
        entity_map = {e.name: e for e in graph.entities}

        visited: set[str] = set(pre_visited) if pre_visited else set()
        frontier: set[str] = set()
        for name in entity_names:
            if name in entity_map:
                frontier.add(name)

        # BFS expansion: depth=0 means roots only (no expansion)
        for _ in range(depth):
            visited |= frontier
            next_frontier: set[str] = set()
            for rel in graph.relations:
                if relation_types and rel.relation_type not in relation_types:
                    continue
                if rel.from_entity in frontier and rel.to_entity not in visited:
                    next_frontier.add(rel.to_entity)
                if rel.to_entity in frontier and rel.from_entity not in visited:
                    next_frontier.add(rel.from_entity)
            frontier = next_frontier

        visited |= frontier

        result_entities = [e for e in graph.entities if e.name in visited]
        result_relations = [
            r for r in graph.relations if r.from_entity in visited and r.to_entity in visited
        ]
        return GraphView(entities=result_entities, relations=result_relations)

    def _get_path_subgraph(
        self,
        start_entity: str,
        path: list[PathStep],
        depth: int,
        relation_types: list[str],
    ) -> GraphView:
        """Navigate directed waypoints, then BFS-expand from the terminal entity.

        Algorithm:
        1. Start at ``start_entity`` (seed = {start_entity}).
        2. For each ``PathStep(relation_type, to_entity)``:
           a. Find a relation from the current seed matching the specified
              ``relation_type`` leading to ``to_entity``.
           b. If found, ``to_entity`` becomes the new seed.
           c. If NOT found, return empty ``GraphView`` (invalid path).
        3. Collect all entities and relations visited during the waypoint walk.
        4. Apply ``_get_subgraph()`` BFS with ``depth`` from the terminal entity.
        5. Merge and deduplicate path entities/relations with BFS result.

        ``relation_types`` is applied only during the BFS expansion phase
        (step 4), not during the waypoint navigation phase (step 2).

        Args:
            start_entity: Name of the entity to start traversal from.
            path: Ordered list of directed waypoints.
            depth: BFS expansion hops from the terminal entity.
            relation_types: Edge type filter for the BFS expansion phase.

        Returns:
            ``GraphView`` with path entities/relations plus BFS expansion,
            or empty ``GraphView`` if any waypoint cannot be resolved.
        """
        graph = self.state.knowledge_graph
        entity_map = {e.name: e for e in graph.entities}

        if start_entity not in entity_map:
            return GraphView()

        # Collect path entities and relations during waypoint walk
        path_entities: dict[str, Entity] = {}
        path_relations: dict[str, Relation] = {}

        current_name = start_entity
        path_entities[current_name] = entity_map[current_name]

        for step in path:
            # Find matching relation: from current → to_entity via relation_type
            matching_rel: Relation | None = None
            for rel in graph.relations:
                if (
                    rel.from_entity == current_name
                    and rel.relation_type == step.relation_type
                    and rel.to_entity == step.to_entity
                ):
                    matching_rel = rel
                    break

            if matching_rel is None or step.to_entity not in entity_map:
                return GraphView()

            path_relations[str(matching_rel.id)] = matching_rel
            current_name = step.to_entity
            path_entities[current_name] = entity_map[current_name]

        # BFS expansion from terminal entity; pre_visited prevents backtracking
        # into path entities already collected during waypoint navigation.
        pre_visited = {name for name in path_entities if name != current_name}
        bfs_result = self._get_subgraph(
            entity_names=[current_name],
            depth=depth,
            relation_types=relation_types,
            pre_visited=pre_visited,
        )

        # Merge: path entities/relations + BFS result (deduplicate by id/name)
        merged_entities: dict[str, Entity] = {**path_entities}
        for e in bfs_result.entities:
            merged_entities[e.name] = e

        merged_relations: dict[str, Relation] = {**path_relations}
        for r in bfs_result.relations:
            merged_relations[str(r.id)] = r

        return GraphView(
            entities=list(merged_entities.values()),
            relations=list(merged_relations.values()),
        )

    # ------------------------------------------------------------------
    # Search (keyword / vector / hybrid)
    # ------------------------------------------------------------------

    def search(self, query: SearchQuery) -> SearchResult:
        """Search the knowledge graph by keyword, vector, or hybrid mode.

        ``mode="keyword"``: case-insensitive substring matching across
        entity and relation fields.  ``mode="vector"``: embeds the query
        and returns top-k results by cosine similarity; returns empty when
        no embeddings exist or the embedding service is unavailable.
        ``mode="hybrid"``: merges keyword and vector results with weighted
        scoring; falls back to keyword-only when embeddings are unavailable.

        Epic 3 expansion options (``include_neighbors``, ``include_edges``,
        ``find_paths``) are applied as post-processing after the core search.

        Args:
            query: Search parameters.

        Returns:
            SearchResult with ranked hits and optional expansion data.
        """
        effective_top_k = (
            query.top_k
            if query.top_k is not None
            else self.config.search_top_k
        )
        effective_threshold = (
            query.score_threshold
            if query.score_threshold is not None
            else self.config.search_score_threshold
        )
        if query.mode == "vector":
            base_result = self._vector_search(query.query, effective_top_k, effective_threshold)
        elif query.mode == "hybrid":
            base_result = self._hybrid_search(query.query, effective_top_k, effective_threshold)
        else:
            base_result = self._keyword_search(query.query, effective_top_k)

        if query.include_neighbors or query.include_edges or query.find_paths:
            return self._expand_search_result(base_result.hits, query)
        return base_result

    def _resolve_ref(self, ref_id: str) -> Entity | Relation | None:
        """Resolve a ``ref_id`` UUID string to the corresponding Entity or Relation.

        Scans entities first, then relations.  Returns ``None`` when the
        ref_id is not found (stale index entry).

        Args:
            ref_id: UUID string from a ``VectorEntry``.

        Returns:
            The matching ``Entity`` or ``Relation``, or ``None``.
        """
        for entity in self.state.knowledge_graph.entities:
            if str(entity.id) == ref_id:
                return entity
        for relation in self.state.knowledge_graph.relations:
            if str(relation.id) == ref_id:
                return relation
        return None

    def _vector_search(
        self, query_text: str, top_k: int, score_threshold: float = 0.0
    ) -> SearchResult:
        """Embed ``query_text`` and return top-k results by cosine similarity.

        Returns an empty ``SearchResult`` when the VectorStoreActor proxy is
        unavailable or the embedding call fails.  Hits with score below
        ``score_threshold`` are filtered out before returning.

        Args:
            query_text: The natural-language query to embed and search.
            top_k: Maximum number of results to return.
            score_threshold: Minimum cosine similarity score to include a hit.

        Returns:
            ``SearchResult`` with hits ranked by cosine similarity score.
        """
        if self._vs_proxy is None:
            return SearchResult(hits=[])
        try:
            vectors = self._vs_proxy.embed([query_text])
            if not vectors:
                return SearchResult(hits=[])
            query_vector = vectors[0]
        except Exception as exc:  # noqa: BLE001
            logger.warning("[%s] Vector search embedding failed: %s", self.config.name, exc)
            return SearchResult(hits=[])

        try:
            vs_result: VsSearchResult = self._vs_proxy.search(
                KG_COLLECTION, query_vector, top_k
            )
            pairs = [(hit.ref_id, hit.score) for hit in vs_result.hits]
        except Exception as exc:  # noqa: BLE001
            logger.warning("[%s] Vector search failed: %s", self.config.name, exc)
            return SearchResult(hits=[])

        hits: list[SearchHit] = []
        for ref_id, score in pairs:
            obj = self._resolve_ref(ref_id)
            if obj is None:
                continue
            if isinstance(obj, Entity):
                hits.append(SearchHit(ref_type="entity", ref_id=ref_id, score=score, entity=obj))
            else:
                hits.append(
                    SearchHit(ref_type="relation", ref_id=ref_id, score=score, relation=obj)
                )
        hits = [h for h in hits if h.score >= score_threshold]
        return SearchResult(hits=hits)

    def _hybrid_search(
        self, query_text: str, top_k: int, score_threshold: float = 0.0
    ) -> SearchResult:
        """Merge keyword and vector search results with weighted scoring.

        Scoring rules:
        - Keyword-only hit: ``score = 1.0``
        - Vector-only hit: ``score = cosine_similarity``
        - Hit in both: ``score = cosine_similarity + 0.5`` (keyword boost)

        Falls back to keyword-only when the vector search returns no hits
        (embedding service unavailable or index empty — AC-4).
        Hits with score below ``score_threshold`` are filtered out after
        merging and before the ``top_k`` slice.

        Args:
            query_text: The query string.
            top_k: Maximum number of results to return.
            score_threshold: Minimum score to include a hit.

        Returns:
            ``SearchResult`` with merged hits ranked by combined score.
        """
        keyword_result = self._keyword_search(query_text, top_k * 2)
        vector_result = self._vector_search(query_text, top_k * 2)

        if not vector_result.hits:
            # Graceful fallback: no embeddings → keyword only (AC-4)
            return SearchResult(hits=keyword_result.hits[:top_k])

        merged: dict[str, SearchHit] = {}

        for hit in vector_result.hits:
            merged[hit.ref_id] = hit  # start with vector score

        for kw_hit in keyword_result.hits:
            if kw_hit.ref_id in merged:
                existing = merged[kw_hit.ref_id]
                merged[kw_hit.ref_id] = existing.model_copy(update={"score": existing.score + 0.5})
            else:
                merged[kw_hit.ref_id] = kw_hit  # keyword-only hit (score = 1.0)

        ranked = sorted(merged.values(), key=lambda h: h.score, reverse=True)
        ranked = [h for h in ranked if h.score >= score_threshold]
        return SearchResult(hits=ranked[:top_k])

    def _keyword_search(self, query: str, top_k: int) -> SearchResult:
        """Case-insensitive substring search across entity and relation fields."""
        hits: list[SearchHit] = []
        q = query.lower()

        for entity in self.state.knowledge_graph.entities:
            if (
                q in entity.name.lower()
                or q in entity.description.lower()
                or q in entity.entity_type.lower()
            ):
                hits.append(
                    SearchHit(
                        ref_type="entity",
                        ref_id=str(entity.id),
                        score=1.0,
                        entity=entity,
                    )
                )

        for relation in self.state.knowledge_graph.relations:
            if (
                q in relation.relation_type.lower()
                or q in relation.description.lower()
                or q in relation.from_entity.lower()
                or q in relation.to_entity.lower()
            ):
                hits.append(
                    SearchHit(
                        ref_type="relation",
                        ref_id=str(relation.id),
                        score=1.0,
                        relation=relation,
                    )
                )

        return SearchResult(hits=sorted(hits, key=lambda h: h.score, reverse=True)[:top_k])

    def _expand_search_result(self, hits: list[SearchHit], query: SearchQuery) -> SearchResult:
        """Post-process search hits with Epic 3 expansion options.

        Applies ``include_neighbors``, ``include_edges``, and ``find_paths``
        expansions to the given hits and returns an enriched ``SearchResult``.

        Args:
            hits: Raw search hits from the core search.
            query: Original search query with expansion flags.

        Returns:
            ``SearchResult`` with hits plus expanded context.
        """
        # Extract entity hits for expansion; filter gives Entity (not Entity | None)
        entity_hits = [h for h in hits if h.entity is not None]
        hit_entities = [h.entity for h in entity_hits if h.entity is not None]
        hit_entity_names = {e.name for e in hit_entities}

        neighbors: list[Entity] = []
        connected_relations: list[Relation] = []
        paths: list[list[Entity | Relation]] = []

        if query.include_neighbors and entity_hits:
            seen_neighbor_names: set[str] = set(hit_entity_names)
            for hit in entity_hits:
                if hit.entity is None:
                    continue
                # 1-hop BFS from the entity
                subgraph = self._get_subgraph(
                    entity_names=[hit.entity.name], depth=1, relation_types=[]
                )
                for e in subgraph.entities:
                    if e.name not in seen_neighbor_names:
                        neighbors.append(e)
                        seen_neighbor_names.add(e.name)

        if query.include_edges and entity_hits:
            seen_rel_ids: set[str] = set()
            for rel in self.state.knowledge_graph.relations:
                if (
                    rel.from_entity in hit_entity_names or rel.to_entity in hit_entity_names
                ) and str(rel.id) not in seen_rel_ids:
                    connected_relations.append(rel)
                    seen_rel_ids.add(str(rel.id))

        if query.find_paths:
            top_entity_hits = entity_hits[:5]  # top 5 entity hits by score
            pairs = [
                (i, j)
                for i in range(len(top_entity_hits))
                for j in range(i + 1, len(top_entity_hits))
            ]
            for i, j in pairs[:10]:
                e_i = top_entity_hits[i].entity
                e_j = top_entity_hits[j].entity
                if e_i is None or e_j is None:
                    continue
                path = self._find_shortest_path(e_i.name, e_j.name)
                if path is not None:
                    paths.append(path)

        return SearchResult(
            hits=hits,
            neighbors=neighbors,
            connected_relations=connected_relations,
            paths=paths,
        )

    def _find_shortest_path(
        self, from_entity: str, to_entity: str
    ) -> list[Entity | Relation] | None:
        """BFS to find the shortest path between two entity names.

        Returns an alternating ``[Entity, Relation, Entity, ...]`` sequence,
        or ``None`` if no path exists.

        Args:
            from_entity: Name of the source entity.
            to_entity: Name of the target entity.

        Returns:
            Alternating entity/relation path list, or ``None`` if unreachable.
        """
        graph = self.state.knowledge_graph
        entity_map = {e.name: e for e in graph.entities}

        if from_entity not in entity_map or to_entity not in entity_map:
            return None
        if from_entity == to_entity:
            return [entity_map[from_entity]]

        # BFS tracking full path (list of Entity | Relation)
        queue: deque[list[Entity | Relation]] = deque([[entity_map[from_entity]]])
        visited: set[str] = {from_entity}

        while queue:
            path = queue.popleft()
            current_entity = path[-1]
            if not isinstance(current_entity, Entity):  # pragma: no cover
                raise TypeError(
                    f"Expected Entity at path tail, got {type(current_entity).__name__}"
                )

            for rel in graph.relations:
                neighbor_name: str | None = None
                if rel.from_entity == current_entity.name:
                    neighbor_name = rel.to_entity
                elif rel.to_entity == current_entity.name:
                    neighbor_name = rel.from_entity
                else:
                    continue

                if neighbor_name in visited:
                    continue
                neighbor = entity_map.get(neighbor_name)
                if neighbor is None:
                    continue

                new_path: list[Entity | Relation] = [*path, rel, neighbor]
                if neighbor_name == to_entity:
                    return new_path
                visited.add(neighbor_name)
                queue.append(new_path)

        return None
