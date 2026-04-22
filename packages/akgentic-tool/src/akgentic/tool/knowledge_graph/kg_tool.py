"""KnowledgeGraph ToolCard integration.

Provides ``KnowledgeGraphTool`` — a ``ToolCard`` that exposes graph
operations (get_graph, update_graph, search) through configurable channels,
with read-only mode support.

Follows the same pattern as ``PlanningTool`` in akgentic.tool.planning.
"""

from __future__ import annotations

import logging
from typing import Callable

from pydantic import Field

from akgentic.core.orchestrator import Orchestrator
from akgentic.tool.core import (
    COMMAND,
    SYSTEM_PROMPT,
    TOOL_CALL,
    BaseToolParam,
    Channels,
    ToolCard,
    _resolve,
)
from akgentic.tool.event import ActorToolObserver
from akgentic.tool.knowledge_graph.kg_actor import (
    KG_ACTOR_NAME,
    KG_ACTOR_ROLE,
    KnowledgeGraphActor,
    KnowledgeGraphConfig,
)
from akgentic.tool.knowledge_graph.models import (
    GetGraphQuery,
    GraphView,
    ManageGraph,
    SearchQuery,
    SearchResult,
)
from akgentic.tool.vector_store.protocol import CollectionConfig

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


# ---------------------------------------------------------------------------
# BaseToolParam subclasses (Task 1)
# ---------------------------------------------------------------------------


class GetGraph(BaseToolParam):
    """Get the full knowledge graph — as system prompt and/or command."""

    expose: set[Channels] = {SYSTEM_PROMPT, COMMAND}
    prompt_include_schema: bool = Field(
        default=True,
        description="Include entity/relation type schema in system prompt.",
    )
    prompt_include_roots: bool = Field(
        default=True,
        description="Include root entities listing in system prompt.",
    )


class UpdateGraph(BaseToolParam):
    """Update the knowledge graph (create/update/delete entities & relations)."""

    expose: set[Channels] = {TOOL_CALL}


class SearchGraph(BaseToolParam):
    """Search the knowledge graph by keyword, vector, or hybrid mode."""

    expose: set[Channels] = {TOOL_CALL, COMMAND}


# ---------------------------------------------------------------------------
# KnowledgeGraphTool ToolCard (Task 2)
# ---------------------------------------------------------------------------


class KnowledgeGraphTool(ToolCard):
    """Knowledge graph tool exposing graph operations through configurable channels.

    Follows the same actor-based pattern as ``PlanningTool``: a singleton
    ``KnowledgeGraphActor`` is created/retrieved via the orchestrator,
    and tool factories delegate to the actor proxy. The ``VectorStoreActor``
    singleton is owned by ``VectorStoreTool`` (declared via ``depends_on``);
    this tool only looks it up at actor-start time.
    """

    name: str = "KnowledgeGraph"
    description: str = "Knowledge graph tool for structured knowledge with semantic search"

    vector_store: bool | str = Field(
        default=True,
        description=(
            "False disables vector store wiring; True uses the default VectorStoreActor; "
            "str names a specific VectorStoreActor to look up."
        ),
    )

    collection: CollectionConfig = Field(
        default_factory=CollectionConfig,
        description=(
            "Vector collection configuration (backend, persistence, dimension, tenant). "
            "Propagated to KnowledgeGraphConfig and used by "
            "KnowledgeGraphActor._acquire_vs_proxy when calling create_collection on the "
            "VectorStoreActor."
        ),
    )

    search_top_k: int = Field(
        default=10,
        description=(
            "Default maximum number of search hits to return. "
            "Can be overridden per-call via SearchQuery.top_k."
        ),
    )
    search_score_threshold: float = Field(
        default=0.3,
        description=(
            "Default minimum cosine similarity score for vector/hybrid search results. "
            "Hits below this threshold are filtered out. "
            "Can be overridden per-call via SearchQuery.score_threshold."
        ),
    )

    @property
    def depends_on(self) -> list[str]:
        """Runtime dependency on VectorStoreTool, conditional on vector_store.

        When ``vector_store`` is ``False`` this tool is in degraded mode and
        does not need VectorStoreActor — the factory must not require a
        ``VectorStoreTool`` in the team config. Any other value (``True`` or a
        name ``str``) requires VectorStoreTool to be wired first so the
        KG actor can look up the VectorStoreActor during ``on_start``.
        """
        return ["VectorStoreTool"] if self.vector_store is not False else []

    get_graph: GetGraph | bool = Field(
        default=True,
        description="Get the full graph — SYSTEM_PROMPT + COMMAND by default",
    )
    update_graph: UpdateGraph | bool = Field(
        default=True,
        description="Update graph — TOOL_CALL by default",
    )
    search: SearchGraph | bool = Field(
        default=True,
        description="Search graph — TOOL_CALL + COMMAND by default",
    )

    read_only: bool = False

    # ------------------------------------------------------------------
    # Observer / actor wiring (2.2)
    # ------------------------------------------------------------------

    def observer(self, observer: ActorToolObserver) -> None:  # type: ignore[override]
        """Attach observer and set up the KG actor proxy.

        Assumes ``VectorStoreTool.observer()`` has already created the
        ``VectorStoreActor`` singleton (ordering enforced by
        ``ToolFactory`` topological sort via ``depends_on``). The
        ``KnowledgeGraphActor`` looks that actor up by name during its own
        ``on_start``.
        """
        from akgentic.tool.knowledge_graph import _check_kg_dependencies

        _check_kg_dependencies()
        self._observer = observer

        if observer.orchestrator is None:
            raise ValueError("KnowledgeGraphTool requires access to the orchestrator.")

        orchestrator_proxy = observer.proxy_ask(observer.orchestrator, Orchestrator)

        # Create/retrieve KnowledgeGraphActor singleton. VectorStoreActor creation
        # is owned by VectorStoreTool (depends_on enforces ordering).
        kg_addr = orchestrator_proxy.getChildrenOrCreate(
            KnowledgeGraphActor,
            config=KnowledgeGraphConfig(
                name=KG_ACTOR_NAME,
                role=KG_ACTOR_ROLE,
                vector_store=self.vector_store,
                collection=self.collection,
                search_top_k=self.search_top_k,
                search_score_threshold=self.search_score_threshold,
            ),
        )

        self._kg_proxy = observer.proxy_ask(kg_addr, KnowledgeGraphActor)

    # ------------------------------------------------------------------
    # Formatting helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _format_graph_view(view: GraphView) -> str:
        """Format a ``GraphView`` as a human-readable string."""
        if not view.entities:
            return "Knowledge graph is empty."
        lines = ["Knowledge Graph:"]
        lines.append("Entities:")
        for e in view.entities:
            lines.append(f"  - {e.name} ({e.entity_type}): {e.description}")
        lines.append("Relations:")
        for r in view.relations:
            desc = f" — {r.description}" if r.description else ""
            lines.append(f"  - {r.from_entity} --[{r.relation_type}]--> {r.to_entity}{desc}")
        return "\n".join(lines)

    @staticmethod
    def _format_graph_summary(
        view: GraphView,
        include_schema: bool = True,
        include_roots: bool = True,
    ) -> str:
        """Format a compact system prompt summary of the graph.

        Output scales as O(types + roots), not O(entities + relations).
        """
        if not view.entities:
            return "Knowledge graph is empty."

        lines = ["**Knowledge Graph Summary:**"]
        lines.append(f"Entities: {len(view.entities)} | Relations: {len(view.relations)}")

        if include_schema:
            entity_types = sorted({e.entity_type for e in view.entities})
            relation_types = sorted({r.relation_type for r in view.relations})
            lines.append(f"Entity types: {', '.join(entity_types)}")
            if relation_types:
                lines.append(f"Relation types: {', '.join(relation_types)}")

        if include_roots:
            root_entities = sorted((e for e in view.entities if e.is_root), key=lambda e: e.name)
            if root_entities:
                lines.append("Root entities:")
                for e in root_entities:
                    lines.append(f"- {e.name} ({e.entity_type}): {e.description}")

        lines.append("")
        lines.append("Use the get_graph tool to explore the full graph or subgraphs.")
        return "\n".join(lines)

    @staticmethod
    def _format_search_result(result: SearchResult) -> str:
        """Format a ``SearchResult`` as a human-readable string."""
        if not result.hits:
            return "No results found."
        lines = ["Search Results:"]
        for hit in result.hits:
            if hit.entity:
                lines.append(
                    f"  - [entity] {hit.entity.name} ({hit.entity.entity_type}): "
                    f"{hit.entity.description} (score: {hit.score:.2f})"
                )
            elif hit.relation:
                r = hit.relation
                lines.append(
                    f"  - [relation] {r.from_entity} --[{r.relation_type}]--> "
                    f"{r.to_entity} (score: {hit.score:.2f})"
                )
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # System prompts (2.3)
    # ------------------------------------------------------------------

    def get_system_prompts(self) -> list[Callable]:
        """Return prompt callables when get_graph is exposed on SYSTEM_PROMPT."""
        gp = _resolve(self.get_graph, GetGraph)
        if gp and SYSTEM_PROMPT in gp.expose:
            return [self._get_graph_prompt_factory()]
        return []

    def _get_graph_prompt_factory(self) -> Callable:
        """Create a closure that returns a compact graph summary as a prompt string."""
        kg_proxy = self._kg_proxy
        format_summary = self._format_graph_summary
        gp = _resolve(self.get_graph, GetGraph)
        include_schema = gp.prompt_include_schema if gp else True
        include_roots = gp.prompt_include_roots if gp else True

        def graph_prompt() -> str:
            """Get the current knowledge graph state."""
            view = kg_proxy.get_graph(GetGraphQuery())
            return format_summary(view, include_schema=include_schema, include_roots=include_roots)

        return graph_prompt

    # ------------------------------------------------------------------
    # Tools (2.4)
    # ------------------------------------------------------------------

    def get_tools(self) -> list[Callable]:
        """Return callable tool functions for LLM agents."""
        tools: list[Callable] = []

        gp = _resolve(self.get_graph, GetGraph)
        if gp and TOOL_CALL in gp.expose:
            tools.append(self._get_graph_factory(gp))

        if not self.read_only:
            up = _resolve(self.update_graph, UpdateGraph)
            if up and TOOL_CALL in up.expose:
                tools.append(self._update_graph_factory(up))

        sp = _resolve(self.search, SearchGraph)
        if sp and TOOL_CALL in sp.expose:
            tools.append(self._search_factory(sp))

        return tools

    # ------------------------------------------------------------------
    # Commands (2.5)
    # ------------------------------------------------------------------

    def get_commands(self) -> dict[type[BaseToolParam], Callable]:
        """Return command mappings for inter-agent orchestration."""
        commands: dict[type[BaseToolParam], Callable] = {}

        gp = _resolve(self.get_graph, GetGraph)
        if gp and COMMAND in gp.expose:
            commands[GetGraph] = self._get_graph_factory(gp)

        sp = _resolve(self.search, SearchGraph)
        if sp and COMMAND in sp.expose:
            commands[SearchGraph] = self._search_factory(sp)

        return commands

    # ------------------------------------------------------------------
    # Factory closures (2.6, 2.7, 2.8)
    # ------------------------------------------------------------------

    def _get_graph_factory(self, params: GetGraph) -> Callable:
        """Return a closure that fetches and formats the graph."""
        kg_proxy = self._kg_proxy
        format_view = self._format_graph_view

        def get_graph() -> str:
            """Get the current knowledge graph."""
            view = kg_proxy.get_graph(GetGraphQuery())
            return format_view(view)

        get_graph.__doc__ = params.format_docstring(get_graph.__doc__)
        return get_graph

    def _update_graph_factory(self, params: UpdateGraph) -> Callable:
        """Return a closure that applies graph mutations."""
        kg_proxy = self._kg_proxy

        def update_graph(update: ManageGraph) -> str:
            """Update the knowledge graph (create/update/delete entities & relations).

            Use this tool to add new entities, update existing ones, create or
            remove relations, and delete entities from the knowledge graph."""
            return kg_proxy.update_graph(update)

        update_graph.__doc__ = params.format_docstring(update_graph.__doc__)
        return update_graph

    def _search_factory(self, params: SearchGraph) -> Callable:
        """Return a closure that searches the graph."""
        kg_proxy = self._kg_proxy
        format_result = self._format_search_result

        def search_graph(query: SearchQuery) -> str:
            """Search the knowledge graph by keyword, vector, or hybrid mode."""
            result = kg_proxy.search(query)
            return format_result(result)

        search_graph.__doc__ = params.format_docstring(search_graph.__doc__)
        return search_graph
