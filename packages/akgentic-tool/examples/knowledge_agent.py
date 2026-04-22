"""Knowledge Graph Tool example — demonstrates end-to-end usage.

Shows how to integrate ``KnowledgeGraphTool`` with an actor-based setup,
build a knowledge graph from a realistic domain scenario, query and
search it, and observe compact system-prompt injection.

Run without OpenAI API key for keyword-only search (hybrid falls back gracefully):
    uv run python packages/akgentic-tool/examples/knowledge_agent.py

Run with OpenAI API key for semantic/hybrid search:
    OPENAI_API_KEY=sk-... uv run python packages/akgentic-tool/examples/knowledge_agent.py
"""

from __future__ import annotations

import os
import uuid
from typing import Any

from akgentic.core.actor_address import ActorAddress
from akgentic.core.agent import AkgentType
from akgentic.core.utils.deserializer import ActorAddressDict

from akgentic.tool.knowledge_graph.kg_actor import (
    KG_ACTOR_NAME,
    KG_ACTOR_ROLE,
    KnowledgeGraphActor,
    KnowledgeGraphConfig,
)
from akgentic.tool.knowledge_graph.kg_tool import (
    GetGraph,
    KnowledgeGraphTool,
)
from akgentic.tool.knowledge_graph.models import (
    EntityCreate,
    GetGraphQuery,
    ManageGraph,
    RelationCreate,
    SearchQuery,
)

# ---------------------------------------------------------------------------
# Minimal actor wiring (same pattern as test_kg_integration.py)
# ---------------------------------------------------------------------------


class _MockAddress(ActorAddress):
    """Minimal ActorAddress stand-in for example wiring."""

    def __init__(self, name: str = "example-agent", role: str = "example-role") -> None:
        self._name = name
        self._role = role
        self._agent_id = uuid.uuid4()

    @property
    def agent_id(self) -> uuid.UUID:
        return self._agent_id

    @property
    def name(self) -> str:
        return self._name

    @property
    def role(self) -> str:
        return self._role

    @property
    def team_id(self) -> uuid.UUID:
        return self._agent_id

    @property
    def squad_id(self) -> uuid.UUID | None:
        return None

    def send(self, recipient: Any, message: Any) -> None:  # noqa: ANN401 — ActorAddress protocol allows Any for generic message passing
        pass

    def is_alive(self) -> bool:
        return True

    def stop(self) -> None:
        pass

    def handle_user_message(self) -> bool:
        return False

    def serialize(self) -> ActorAddressDict:
        return {
            "__actor_address__": True,
            "__actor_type__": type(self).__qualname__,
            "agent_id": str(self._agent_id),
            "name": self._name,
            "role": self._role,
            "team_id": "",
            "squad_id": "",
            "user_message": False,
        }

    def __repr__(self) -> str:
        return f"_MockAddress(name={self._name})"


class _OrchestratorStub:
    """Minimal orchestrator stub returning the singleton KG actor address."""

    def __init__(self, kg_addr: _MockAddress) -> None:
        self._kg_addr = kg_addr
        self._vs_addr = _MockAddress("#VectorStore", "ToolActor")

    def getChildrenOrCreate(  # noqa: N802
        self, actor_class: type, config: object = None,
    ) -> ActorAddress:
        """Return KG addr for KG actors, VS addr for VectorStore actors."""
        from akgentic.tool.vector_store.actor import VectorStoreActor

        if actor_class is VectorStoreActor:
            return self._vs_addr
        return self._kg_addr


class _ExampleObserver:
    """Observer that wires a real KnowledgeGraphActor for the example.

    Replicates the pattern from ``test_kg_integration.py`` so the example
    runs without a full Pykka actor system.
    """

    def __init__(self) -> None:
        self.events: list[object] = []
        self._address = _MockAddress("knowledge-agent")
        self._orchestrator_addr = _MockAddress("orchestrator")
        self._kg_actor = KnowledgeGraphActor(config=KnowledgeGraphConfig())
        self._kg_actor.on_start()
        self._kg_addr = _MockAddress(KG_ACTOR_NAME, KG_ACTOR_ROLE)

    @property
    def myAddress(self) -> ActorAddress:  # noqa: N802
        """Return the example agent's actor address."""
        return self._address

    @property
    def orchestrator(self) -> ActorAddress:
        """Return the orchestrator's actor address."""
        return self._orchestrator_addr

    def notify_event(self, event: object) -> None:
        """Record events for introspection."""
        self.events.append(event)

    def proxy_ask(
        self,
        actor: ActorAddress,
        actor_type: type[AkgentType] | None = None,
        timeout: int | None = None,
    ) -> Any:  # noqa: ANN401
        """Return the appropriate stub based on which actor is being asked."""
        if actor == self._orchestrator_addr:
            return _OrchestratorStub(self._kg_addr)
        # Return mock for VectorStoreActor proxy
        if hasattr(actor, 'name') and actor.name == "#VectorStore":
            from unittest.mock import MagicMock

            return MagicMock()
        return self._kg_actor


# ---------------------------------------------------------------------------
# Domain: software tech stack knowledge graph
# ---------------------------------------------------------------------------

_TECH_STACK_ENTITIES: list[EntityCreate] = [
    EntityCreate(
        name="FastAPI",
        entity_type="Framework",
        description="Modern async Python web framework for building APIs",
        is_root=True,
    ),
    EntityCreate(
        name="PostgreSQL",
        entity_type="Database",
        description="Open-source relational database used as the primary store",
    ),
    EntityCreate(
        name="Redis",
        entity_type="Cache",
        description="In-memory data structure store used for caching and sessions",
    ),
    EntityCreate(
        name="Docker",
        entity_type="Container",
        description="Container platform for packaging and deploying the application",
        is_root=True,
    ),
    EntityCreate(
        name="GitHub Actions",
        entity_type="CI-CD",
        description="Continuous integration and delivery pipeline automation",
    ),
    EntityCreate(
        name="Python",
        entity_type="Language",
        description="Primary programming language for the backend services",
    ),
]

_TECH_STACK_RELATIONS: list[RelationCreate] = [
    RelationCreate(
        from_entity="FastAPI",
        to_entity="PostgreSQL",
        relation_type="DEPENDS_ON",
        description="FastAPI reads and writes data to PostgreSQL",
    ),
    RelationCreate(
        from_entity="FastAPI",
        to_entity="Redis",
        relation_type="USES",
        description="FastAPI uses Redis for caching and session management",
    ),
    RelationCreate(
        from_entity="Docker",
        to_entity="FastAPI",
        relation_type="DEPLOYS",
        description="Docker containers package and run the FastAPI service",
    ),
    RelationCreate(
        from_entity="GitHub Actions",
        to_entity="Docker",
        relation_type="BUILDS",
        description="GitHub Actions CI builds Docker images on push",
    ),
    RelationCreate(
        from_entity="FastAPI",
        to_entity="Python",
        relation_type="BUILT_WITH",
        description="FastAPI is implemented in Python",
    ),
]


# ---------------------------------------------------------------------------
# Example functions
# ---------------------------------------------------------------------------


def build_tech_stack_graph(kg_actor: KnowledgeGraphActor) -> None:
    """Populate the knowledge graph with the software tech stack domain.

    Args:
        kg_actor: The KnowledgeGraphActor instance to mutate.
    """
    print("\n--- Step 1: Building tech stack knowledge graph ---")
    result = kg_actor.update_graph(
        ManageGraph(
            create_entities=_TECH_STACK_ENTITIES,
            create_relations=_TECH_STACK_RELATIONS,
        )
    )
    print(f"Graph built: {result}")
    print(f"  Entities: {len(_TECH_STACK_ENTITIES)}")
    print(f"  Relations: {len(_TECH_STACK_RELATIONS)}")


def demonstrate_queries(kg_actor: KnowledgeGraphActor, tool: KnowledgeGraphTool) -> None:
    """Show different query patterns: full graph, subgraph, and system prompt summary.

    Args:
        kg_actor: Direct actor access for subgraph queries.
        tool: The configured ToolCard for system-prompt demonstration.
    """
    print("\n--- Step 2: Querying the knowledge graph ---")

    # Full graph via actor
    full_graph = kg_actor.get_graph(GetGraphQuery())
    entity_count = len(full_graph.entities)
    relation_count = len(full_graph.relations)
    print(f"\nFull graph: {entity_count} entities, {relation_count} relations")
    for entity in full_graph.entities:
        root_marker = " [ROOT]" if entity.is_root else ""
        print(f"  {entity.name} ({entity.entity_type}){root_marker}: {entity.description}")

    # Subgraph: 2-hop BFS from FastAPI
    print("\nSubgraph (2-hop from FastAPI):")
    subgraph = kg_actor.get_graph(GetGraphQuery(entity_names=["FastAPI"], depth=2))
    print(f"  {len(subgraph.entities)} entities reachable")
    for rel in subgraph.relations:
        print(f"  {rel.from_entity} --[{rel.relation_type}]--> {rel.to_entity}")

    # Roots-only view
    print("\nRoot entities only:")
    roots_view = kg_actor.get_graph(GetGraphQuery(roots_only=True))
    for entity in roots_view.entities:
        print(f"  {entity.name} ({entity.entity_type})")

    # System prompt summary — compact graph context injection for LLM agents
    print("\n--- Step 3: System prompt summary (compact graph context) ---")
    prompts = tool.get_system_prompts()
    if prompts:
        summary = prompts[0]()
        print(summary)


def demonstrate_search(tool: KnowledgeGraphTool) -> None:
    """Show keyword and (optional) hybrid search patterns.

    Args:
        tool: The configured ToolCard providing search callables.
    """
    print("\n--- Step 4: Searching the knowledge graph ---")

    tools = tool.get_tools()
    search_fn = next((t for t in tools if t.__name__ == "search_graph"), None)

    if search_fn is None:
        print("search_graph tool not available")
        return

    # Keyword search — always works, no API key required
    print("\nKeyword search for 'database':")
    kw_result = search_fn(SearchQuery(query="database", mode="keyword"))
    print(kw_result)

    print("\nKeyword search for 'deploy':")
    kw_result2 = search_fn(SearchQuery(query="deploy", mode="keyword"))
    print(kw_result2)

    # Hybrid/vector search — queries with no keyword match test real semantic value
    if os.environ.get("OPENAI_API_KEY"):
        # "containerization" has zero substring overlap → keyword returns nothing,
        # but a good embedding model should rank Docker highly.
        print("\nHybrid search for 'containerization' (semantic-only signal):")
        hybrid_result = search_fn(SearchQuery(query="containerization", mode="hybrid"))
        print(hybrid_result)

        # "RDBMS" has zero substring overlap → keyword returns nothing,
        # but semantic search should surface PostgreSQL.
        print("\nHybrid search for 'RDBMS' (semantic-only signal):")
        hybrid_result2 = search_fn(SearchQuery(query="RDBMS", mode="hybrid"))
        print(hybrid_result2)
    else:
        print("\nOPENAI_API_KEY not set — using keyword search only")
        print("  Hybrid mode falls back gracefully to keyword search when embeddings unavailable")
        fallback_result = search_fn(SearchQuery(query="cache", mode="hybrid"))
        print(f"  Hybrid (keyword fallback) for 'cache':\n{fallback_result}")


def main() -> None:
    """Run the knowledge agent example end-to-end.

    Sets up an actor-based knowledge graph tool, builds a tech stack
    knowledge graph, demonstrates queries, search, and system prompt
    injection, then shuts down cleanly.
    """
    print("=" * 60)
    print("Knowledge Graph Tool — End-to-End Example")
    print("=" * 60)

    # --- Setup: wire KnowledgeGraphTool with observer ---
    print("\n--- Setup: configuring KnowledgeGraphTool ---")
    observer = _ExampleObserver()
    tool = KnowledgeGraphTool(
        get_graph=GetGraph(prompt_include_schema=True, prompt_include_roots=True),
        update_graph=True,
        search=True,
    )
    tool.observer(observer)
    print(f"Tool: {tool.name} — {tool.description}")

    # Access the underlying actor via observer for direct calls
    kg_actor = observer._kg_actor

    # --- Build the graph ---
    build_tech_stack_graph(kg_actor)

    # --- Query the graph ---
    demonstrate_queries(kg_actor, tool)

    # --- Search the graph ---
    demonstrate_search(tool)

    # --- Summary of tool call events ---
    print("\n--- Tool call event log ---")
    print(f"Total tool events recorded: {len(observer.events)}")
    for event in observer.events:
        print(f"  [{event.tool_name}]")

    # --- Shutdown ---
    print("\n--- Shutdown complete ---")
    print("Knowledge graph example finished successfully.")
    print("=" * 60)


if __name__ == "__main__":
    main()
