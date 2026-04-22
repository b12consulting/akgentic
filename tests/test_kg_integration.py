"""Integration tests: KnowledgeGraphTool ↔ KnowledgeGraphActor end-to-end.

Covers AC-5 integration: create tool, wire with mock observer,
verify update_graph → get_graph → search round-trip through the ToolCard.
"""

from __future__ import annotations

import uuid
from typing import Any

from akgentic.core.actor_address import ActorAddress
from akgentic.core.agent import AkgentType

from akgentic.tool.core import TOOL_CALL
from akgentic.tool.knowledge_graph.kg_actor import (
    KG_ACTOR_NAME,
    KG_ACTOR_ROLE,
    KnowledgeGraphActor,
    KnowledgeGraphConfig,
)
from akgentic.tool.knowledge_graph.kg_tool import (
    GetGraph,
    KnowledgeGraphTool,
    SearchGraph,
)
from akgentic.tool.knowledge_graph.models import (
    EntityCreate,
    ManageGraph,
    RelationCreate,
    SearchQuery,
)

# ---------------------------------------------------------------------------
# Shared mock infrastructure
# ---------------------------------------------------------------------------


class MockActorAddress(ActorAddress):
    """Minimal ActorAddress mock for integration tests."""

    def __init__(self, name: str = "test-agent", role: str = "test-role") -> None:
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
    def team_id(self) -> uuid.UUID | None:
        return None

    @property
    def squad_id(self) -> uuid.UUID | None:
        return None

    def send(self, recipient: Any, message: Any) -> None:
        pass

    def is_alive(self) -> bool:
        return True

    def stop(self) -> None:
        pass

    def handle_user_message(self) -> bool:
        return False

    def serialize(self) -> dict[str, Any]:
        return {"name": self._name, "role": self._role, "agent_id": str(self._agent_id)}

    def __repr__(self) -> str:
        return f"MockActorAddress(name={self._name})"


class IntegrationObserver:
    """Mock observer wiring a real KnowledgeGraphActor for integration testing."""

    def __init__(self) -> None:
        from akgentic.tool.knowledge_graph.models import KnowledgeGraphState
        from akgentic.tool.vector_store.actor import VS_ACTOR_NAME

        self.events: list[object] = []
        self._address = MockActorAddress("test-agent")
        self._orchestrator_addr = MockActorAddress("orchestrator")
        self._kg_actor = KnowledgeGraphActor()
        # Set typed config so search methods can access search_score_threshold.
        self._kg_actor.config = KnowledgeGraphConfig(name=KG_ACTOR_NAME, role=KG_ACTOR_ROLE)
        # Manually init without orchestrator dependency
        self._kg_actor.state = KnowledgeGraphState()
        self._kg_actor.state.observer(self._kg_actor)
        self._kg_actor._vs_proxy = None
        self._kg_actor._state_event_seq = 0
        self._kg_addr = MockActorAddress(KG_ACTOR_NAME, KG_ACTOR_ROLE)
        self._vs_addr = MockActorAddress(VS_ACTOR_NAME, "ToolActor")

    @property
    def myAddress(self) -> ActorAddress:  # noqa: N802
        return self._address

    @property
    def orchestrator(self) -> ActorAddress:
        return self._orchestrator_addr

    def notify_event(self, event: object) -> None:
        self.events.append(event)

    def proxy_ask(
        self,
        actor: ActorAddress,
        actor_type: type[AkgentType] | None = None,
        timeout: int | None = None,
    ) -> Any:
        from unittest.mock import MagicMock

        if actor == self._orchestrator_addr:
            return _OrchestratorStub(self._kg_addr, self._vs_addr)
        if actor == self._vs_addr:
            return MagicMock()  # mock VectorStoreActor proxy
        return self._kg_actor


class _OrchestratorStub:
    """Minimal orchestrator stub that returns known actor addresses."""

    def __init__(self, kg_addr: MockActorAddress, vs_addr: MockActorAddress) -> None:
        self._kg_addr = kg_addr
        self._vs_addr = vs_addr

    def getChildrenOrCreate(  # noqa: N802
        self, actor_class: type, config: object = None,
    ) -> ActorAddress:
        from akgentic.tool.knowledge_graph.kg_actor import KnowledgeGraphActor
        from akgentic.tool.vector_store.actor import VectorStoreActor

        if actor_class is VectorStoreActor:
            return self._vs_addr
        if actor_class is KnowledgeGraphActor:
            return self._kg_addr
        return self._kg_addr

    def get_team_member(self, name: str) -> ActorAddress | None:
        from akgentic.tool.vector_store.actor import VS_ACTOR_NAME

        if name == KG_ACTOR_NAME:
            return self._kg_addr
        if name == VS_ACTOR_NAME:
            return self._vs_addr
        return None

    def createActor(self, *args: Any, **kwargs: Any) -> ActorAddress:  # noqa: N802
        return self._kg_addr


# ===========================================================================
# Integration tests
# ===========================================================================


class TestEndToEndUpdateGetSearch:
    """Full round-trip: update_graph → get_graph → search via ToolCard."""

    def _make_tool(self) -> tuple[KnowledgeGraphTool, IntegrationObserver]:
        observer = IntegrationObserver()
        tool = KnowledgeGraphTool()
        tool.observer(observer)
        return tool, observer

    def test_update_then_get_graph_prompt(self) -> None:
        tool, observer = self._make_tool()

        # Update via tool factory
        tools = tool.get_tools()
        update_fn = [t for t in tools if t.__name__ == "update_graph"][0]
        update_fn(
            ManageGraph(
                create_entities=[
                    EntityCreate(
                        name="Python", entity_type="Language", description="Programming lang"
                    ),
                    EntityCreate(
                        name="FastAPI", entity_type="Framework", description="Web framework"
                    ),
                ],
                create_relations=[
                    RelationCreate(
                        from_entity="FastAPI",
                        to_entity="Python",
                        relation_type="BUILT_WITH",
                    ),
                ],
            )
        )

        # Get via system prompt — compact summary shows counts + types
        prompts = tool.get_system_prompts()
        result = prompts[0]()
        assert "Knowledge Graph Summary:" in result
        assert "Entities: 2" in result
        assert "Relations: 1" in result
        assert "BUILT_WITH" in result
        assert "Language" in result
        assert "Framework" in result

    def test_update_then_search(self) -> None:
        tool, observer = self._make_tool()

        tools = tool.get_tools()
        update_fn = [t for t in tools if t.__name__ == "update_graph"][0]
        search_fn = [t for t in tools if t.__name__ == "search_graph"][0]

        update_fn(
            ManageGraph(
                create_entities=[
                    EntityCreate(
                        name="Django", entity_type="Framework", description="Full-stack web"
                    ),
                ]
            )
        )

        result = search_fn(SearchQuery(query="Django"))
        assert "Django" in result
        assert "Framework" in result

    def test_read_only_blocks_update(self) -> None:
        observer = IntegrationObserver()
        tool = KnowledgeGraphTool(read_only=True)
        tool.observer(observer)

        tools = tool.get_tools()
        tool_names = [t.__name__ for t in tools]
        assert "update_graph" not in tool_names
        assert "search_graph" in tool_names

    def test_get_graph_command_returns_string(self) -> None:
        tool, observer = self._make_tool()

        # Seed data via actor directly
        observer._kg_actor.update_graph(
            ManageGraph(
                create_entities=[
                    EntityCreate(name="Node", entity_type="Runtime", description="JS runtime"),
                ]
            )
        )

        commands = tool.get_commands()
        get_graph_cmd = commands[GetGraph]
        result = get_graph_cmd()
        assert "Node" in result

    def test_search_command_returns_string(self) -> None:
        tool, observer = self._make_tool()
        observer._kg_actor.update_graph(
            ManageGraph(
                create_entities=[
                    EntityCreate(name="TypeScript", entity_type="Language", description="Typed JS"),
                ]
            )
        )

        commands = tool.get_commands()
        search_cmd = commands[SearchGraph]
        result = search_cmd(SearchQuery(query="TypeScript"))
        assert "TypeScript" in result

    def test_get_graph_with_tool_call_channel(self) -> None:
        """When get_graph exposes TOOL_CALL, it appears in get_tools()."""
        observer = IntegrationObserver()
        tool = KnowledgeGraphTool(get_graph=GetGraph(expose={TOOL_CALL}))
        tool.observer(observer)

        tools = tool.get_tools()
        tool_names = [t.__name__ for t in tools]
        assert "get_graph" in tool_names

    def test_empty_graph_search_returns_no_results(self) -> None:
        tool, observer = self._make_tool()

        tools = tool.get_tools()
        search_fn = [t for t in tools if t.__name__ == "search_graph"][0]
        result = search_fn(SearchQuery(query="nonexistent"))
        assert "No results" in result

    def test_full_lifecycle_update_get_search_delete(self) -> None:
        """Full lifecycle: create → read → search → delete → verify empty."""
        tool, observer = self._make_tool()

        tools = tool.get_tools()
        update_fn = [t for t in tools if t.__name__ == "update_graph"][0]
        search_fn = [t for t in tools if t.__name__ == "search_graph"][0]

        # Create
        update_fn(
            ManageGraph(
                create_entities=[
                    EntityCreate(name="Go", entity_type="Language", description="Compiled lang"),
                ]
            )
        )

        # Read — compact summary shows counts + types
        prompts = tool.get_system_prompts()
        prompt_result = prompts[0]()
        assert "Entities: 1" in prompt_result
        assert "Language" in prompt_result

        # Search
        search_result = search_fn(SearchQuery(query="Go"))
        assert "Go" in search_result

        # Delete
        update_fn(ManageGraph(delete_entities=["Go"]))

        # Verify empty
        prompt_after = prompts[0]()
        assert "empty" in prompt_after.lower()
